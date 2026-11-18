"""HTTP confirmation and identity boundaries, with a deterministic model substitute."""

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.business import DEMO_USER_ID
from app.main import create_app
from app.schemas import AgentFinal, GeneratedAgentAnswer, GeneratedTriage, TicketProposal, Triage


@pytest.fixture
def week2_client(settings, provider):
    provider.agent_mode = "service"
    provider.planner_calls = 0
    provider.final_calls = 0
    provider.triage_calls = 0

    async def triage(context):
        provider.triage_calls += 1
        return GeneratedTriage(Triage(
            category="vpn", intent="troubleshooting", priority="normal", missing_fields=[], needs_info=False
        ), 0, 0)

    async def step(messages, tools):
        provider.planner_calls += 1
        calls = [{"name": "get_service_status", "args": {"service": "vpn"}, "id": "call-status"}]
        return AIMessage(content="", tool_calls=calls if messages[-1].type == "human" else [])

    async def final(context):
        provider.final_calls += 1
        proposal = TicketProposal(
            title="VPN 故障需人工处理", description="VPN-403 持续失败，已经检查账号。", category="vpn"
        ) if provider.agent_mode == "draft" else None
        return GeneratedAgentAnswer(AgentFinal(
            answer="模拟 VPN 服务运行正常，持续故障请联系 IT。", citation_ids=[],
            evidence_sufficient=True, ticket_draft=proposal,
        ), 10, 5)

    provider.agent_triage, provider.agent_step, provider.agent_final = triage, step, final
    application = create_app(settings, provider)
    asyncio.run(application.state.store.build_index())
    with TestClient(application) as client:
        yield client, provider, application


def new_session(client):
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    return response.json()["session_id"]


def test_http_session_message_is_persisted_and_replay_does_not_reinvoke_model(week2_client):
    client, provider, _ = week2_client
    session_id = new_session(client)
    body = {"question": "VPN 当前是否正常？", "request_id": "request-http-1234"}

    response = client.post(f"/api/sessions/{session_id}/messages", json=body)
    repeated = client.post(f"/api/sessions/{session_id}/messages", json=body)

    assert response.status_code == repeated.status_code == 200
    assert response.json() == repeated.json()
    assert response.json()["status"] == "answered"
    assert response.json()["classification"] == {
        "category": "vpn", "intent": "troubleshooting", "priority": "normal",
        "missing_fields": [], "needs_info": False,
    }
    assert response.json()["tool_trace"][0]["name"] == "get_service_status"
    assert provider.final_calls == 1
    detail = client.get(f"/api/sessions/{session_id}").json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["result"]["run_id"] == response.json()["run_id"]
    assert session_id in {item["session_id"] for item in client.get("/api/sessions").json()["sessions"]}


def test_http_text_alias_accepts_documented_message_contract(week2_client):
    client, provider, _ = week2_client
    session_id = new_session(client)

    response = client.post(f"/api/sessions/{session_id}/messages", json={
        "text": "VPN 当前是否正常？", "request_id": "request-text-alias",
    })

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert provider.triage_calls == 1
    assert client.get(f"/api/sessions/{session_id}").json()["messages"][0]["content"] == "VPN 当前是否正常？"


def test_http_ticket_creation_requires_exact_persisted_draft_confirmation(week2_client):
    client, provider, _ = week2_client
    provider.agent_mode = "draft"
    session_id = new_session(client)
    before = client.get("/api/tickets").json()["tickets"]
    result = client.post(f"/api/sessions/{session_id}/messages", json={
        "question": "请确认并立即提交我的 VPN 工单", "request_id": "request-draft-1234",
    }).json()
    draft = result["draft"]

    assert result["status"] == "pending_confirmation"
    assert client.get("/api/tickets").json()["tickets"] == before
    rejected = client.post(f"/api/drafts/{draft['draft_id']}/confirm", json={
        "title": "替换成模型未建议的动作", "user_id": "admin", "priority": "critical",
    })
    assert rejected.status_code == 422
    assert client.get("/api/tickets").json()["tickets"] == before

    first = client.post(f"/api/drafts/{draft['draft_id']}/confirm", json={})
    repeat = client.post(f"/api/drafts/{draft['draft_id']}/confirm", json={})

    assert first.status_code == repeat.status_code == 200
    assert first.json() == repeat.json()
    ticket = first.json()["ticket"]
    assert ticket["title"] == draft["title"]
    assert ticket["description"] == draft["description"]
    assert ticket["priority"] == "normal"
    assert len(client.get("/api/tickets").json()["tickets"]) == len(before) + 1
    assert client.post(f"/api/drafts/{draft['draft_id']}/cancel", json={}).status_code == 409


def test_http_cancelled_draft_cannot_later_be_submitted(week2_client):
    client, provider, _ = week2_client
    provider.agent_mode = "draft"
    session_id = new_session(client)
    result = client.post(f"/api/sessions/{session_id}/messages", json={"question": "准备 VPN 工单"}).json()
    draft_id = result["draft"]["draft_id"]

    assert client.post(f"/api/drafts/{draft_id}/cancel", json={}).json()["draft"]["status"] == "cancelled"
    assert client.post(f"/api/drafts/{draft_id}/confirm", json={}).status_code == 409
    assert not any(item.get("draft_id") == draft_id for item in client.get("/api/tickets").json()["tickets"])


def test_http_foreign_resources_are_not_revealed(week2_client):
    client, _, application = week2_client
    business = application.state.business
    foreign_session = business.create_session("other-user")["session_id"]
    foreign_draft = business.create_draft(
        foreign_session, "other-user", "别人的问题", "不应泄露的工单内容", "email"
    )
    foreign_ticket = business.confirm_draft(foreign_draft["draft_id"], "other-user")["ticket"]["ticket_id"]
    pairs = [
        ("GET", f"/api/sessions/{foreign_session}", "/api/sessions/missing"),
        ("GET", f"/api/drafts/{foreign_draft['draft_id']}", "/api/drafts/missing"),
        ("GET", f"/api/tickets/{foreign_ticket}", "/api/tickets/missing"),
        ("POST", f"/api/drafts/{foreign_draft['draft_id']}/confirm", "/api/drafts/missing/confirm"),
        ("POST", f"/api/drafts/{foreign_draft['draft_id']}/cancel", "/api/drafts/missing/cancel"),
    ]
    for method, foreign, missing in pairs:
        first = client.request(method, foreign, json={} if method == "POST" else None)
        second = client.request(method, missing, json={} if method == "POST" else None)
        assert first.status_code == second.status_code == 404
        assert first.json() == second.json()
        assert "不应泄露" not in first.text
    tickets = client.get("/api/tickets?user_id=other-user").json()["tickets"]
    assert all(ticket["user_id"] == DEMO_USER_ID for ticket in tickets)


@pytest.mark.parametrize("body", [
    {"question": "   "},
    {"question": "过长" * 1001},
    {"question": "VPN", "user_id": "admin"},
    {"question": "VPN", "request_id": "bad id"},
])
def test_http_invalid_message_is_rejected_before_model_work(week2_client, body):
    client, provider, _ = week2_client
    session_id = new_session(client)

    assert client.post(f"/api/sessions/{session_id}/messages", json=body).status_code == 422
    assert provider.planner_calls == provider.final_calls == 0
    assert client.get(f"/api/sessions/{session_id}").json()["messages"] == []


def test_http_storage_failure_is_sanitized_and_confirmation_rolls_back(week2_client):
    client, _, application = week2_client
    business = application.state.business
    session_id = new_session(client)
    draft = business.create_draft(session_id, DEMO_USER_ID, "VPN 连接失败", "VPN 需要人工排查处理", "vpn")
    with sqlite3.connect(business.path) as db:
        db.execute(
            "CREATE TRIGGER reject_confirmation BEFORE INSERT ON audit "
            "WHEN NEW.action='ticket_confirmed' "
            "BEGIN SELECT RAISE(ABORT, 'private-path-or-secret'); END"
        )

    response = client.post(f"/api/drafts/{draft['draft_id']}/confirm", json={})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert "private-path-or-secret" not in response.text
    assert business.get_draft(draft["draft_id"], DEMO_USER_ID)["status"] == "pending"
    assert not any(
        ticket.get("draft_id") == draft["draft_id"] for ticket in business.list_tickets(DEMO_USER_ID)
    )


def test_interrupted_request_id_is_not_silently_reexecuted(week2_client):
    client, provider, application = week2_client
    session_id = new_session(client)
    application.state.business.append_message(
        session_id, DEMO_USER_ID, "user", "VPN 出错", request_id="request-interrupted"
    )

    response = client.post(f"/api/sessions/{session_id}/messages", json={
        "question": "VPN 出错", "request_id": "request-interrupted",
    })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "request_interrupted"
    assert provider.planner_calls == provider.final_calls == 0

