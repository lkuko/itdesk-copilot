"""Persistent workbench editing, deletion, and credential boundaries; no paid API."""

import asyncio
import json
import os
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.business import DEMO_USER_ID, BusinessError, BusinessStore
from app.input_safety import redact_sensitive_text, redact_sensitive_value, sensitive_input_reason
from app.main import create_app
from app.schemas import AgentFinal, GeneratedAgentAnswer, GeneratedTriage, Triage


@pytest.fixture
def frontend_client(settings, provider):
    provider.model_calls = 0

    async def triage(_context):
        provider.model_calls += 1
        return GeneratedTriage(Triage(
            category="vpn", intent="service_status", priority="normal", missing_fields=[], needs_info=False
        ))

    async def step(messages, _tools):
        provider.model_calls += 1
        calls = [{"name": "get_service_status", "args": {"service": "vpn"}, "id": "call-service"}]
        return AIMessage(content="", tool_calls=calls if messages[-1].type == "human" else [])

    async def final(_context):
        provider.model_calls += 1
        return GeneratedAgentAnswer(AgentFinal(
            answer="VPN 服务的固定模拟快照正常。", citation_ids=[], evidence_sufficient=True,
        ))

    provider.agent_triage, provider.agent_step, provider.agent_final = triage, step, final
    application = create_app(settings, provider)
    asyncio.run(application.state.store.build_index())
    with TestClient(application) as client:
        yield client, provider, application


def create_draft(application):
    business = application.state.business
    session_id = business.create_session(DEMO_USER_ID)["session_id"]
    draft = business.create_draft(session_id, DEMO_USER_ID, "VPN 原草稿", "VPN-403 连接失败。", "vpn")
    return session_id, draft["draft_id"]


def test_renamed_session_and_edited_draft_persist_and_confirmation_uses_new_fields(frontend_client):
    client, _, application = frontend_client
    session_id, draft_id = create_draft(application)
    renamed = client.patch(f"/api/sessions/{session_id}", json={"title": " 我的网络排查 "})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "我的网络排查"
    fields = {
        "title": "邮箱人工核查", "description": "更正：MAIL-504 同步延迟，未重启。", "category": "email",
    }
    edited = client.patch(f"/api/drafts/{draft_id}", json=fields)
    assert edited.status_code == 200
    assert edited.json()["status"] == "draft_updated"
    assert {key: edited.json()["draft"][key] for key in fields} == fields
    assert edited.json()["draft"]["status"] == "pending"
    assert not any(t["draft_id"] == draft_id for t in application.state.business.list_tickets())

    # Reopening the repository reads the update from SQLite, not widget state.
    reopened = BusinessStore(application.state.settings.data_dir)
    assert reopened.get_session(session_id)["title"] == "我的网络排查"
    assert reopened.get_draft(draft_id)["description"] == fields["description"]
    first = client.post(f"/api/drafts/{draft_id}/confirm", json={})
    repeated = client.post(f"/api/drafts/{draft_id}/confirm", json={})
    assert first.status_code == 200 and first.json() == repeated.json()
    assert {key: first.json()["ticket"][key] for key in fields} == fields
    assert first.json()["ticket"]["priority"] == "normal"
    restored = client.get(f"/api/sessions/{session_id}").json()
    assert restored["drafts"][0]["ticket_id"] == first.json()["ticket"]["ticket_id"]
    with sqlite3.connect(reopened.path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit WHERE action='draft_edited'").fetchone()[0] == 1


@pytest.mark.parametrize("status", ["confirmed", "cancelled"])
def test_finished_draft_cannot_be_modified(frontend_client, status):
    client, _, application = frontend_client
    _, draft_id = create_draft(application)
    action = "confirm" if status == "confirmed" else "cancel"
    client.post(f"/api/drafts/{draft_id}/{action}", json={})
    before = application.state.business.get_draft(draft_id)
    rejected = client.patch(f"/api/drafts/{draft_id}", json={
        "title": "替换标题", "description": "替换说明", "category": "device",
    })
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "draft_not_pending"
    assert application.state.business.get_draft(draft_id) == before


@pytest.mark.parametrize("field,value", [("priority", "high"), ("user_id", "admin"), ("status", "confirmed")])
def test_draft_edit_cannot_set_identity_priority_or_status(frontend_client, field, value):
    client, _, application = frontend_client
    _, draft_id = create_draft(application)
    response = client.patch(f"/api/drafts/{draft_id}", json={
        "title": "VPN问题", "description": "仍未连接", "category": "vpn", field: value,
    })
    assert response.status_code == 422
    assert application.state.business.get_draft(draft_id)["status"] == "pending"


def test_delete_removes_content_and_checkpoint_but_preserves_confirmed_ticket_and_audit(frontend_client):
    client, _, application = frontend_client
    business = application.state.business
    session_id, draft_id = create_draft(application)
    business.append_message(session_id, DEMO_USER_ID, "user", "查询VPN服务", request_id="delete-test-001")
    # A real graph turn creates a checkpoint for this session.
    assert client.post(f"/api/sessions/{session_id}/messages", json={
        "question": "VPN 服务正常吗？", "request_id": "delete-test-002",
    }).status_code == 200
    ticket = business.confirm_draft(draft_id)["ticket"]
    second_draft = business.create_draft(session_id, DEMO_USER_ID, "未提交草稿", "VPN仍然失败", "vpn")
    assert client.request("DELETE", f"/api/sessions/{session_id}", json={}).status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert client.get(f"/api/drafts/{draft_id}").status_code == 404
    assert client.get(f"/api/drafts/{second_draft['draft_id']}").status_code == 404
    preserved = client.get(f"/api/tickets/{ticket['ticket_id']}").json()
    assert preserved["title"] == ticket["title"] and preserved["draft_id"] is None
    with sqlite3.connect(business.path) as db:
        count = db.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]
        assert count == 0
        detail = json.loads(db.execute(
            "SELECT details_json FROM audit WHERE action='session_deleted' AND entity_id=?", (session_id,)
        ).fetchone()[0])
        assert detail["preserved_tickets"] == [{"ticket_id": ticket["ticket_id"], "draft_id": draft_id}]
        assert db.execute("SELECT COUNT(*) FROM audit WHERE action='ticket_confirmed'").fetchone()[0] == 1
    with sqlite3.connect(application.state.settings.data_dir / "checkpoints.sqlite3") as db:
        for table in ["checkpoints", "writes"]:
            count = db.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id=?", (session_id,)).fetchone()[0]
            assert count == 0


def test_delete_requires_empty_body_and_rejects_busy_session(frontend_client):
    client, _, application = frontend_client
    session_id, _ = create_draft(application)
    response = client.request("DELETE", f"/api/sessions/{session_id}", json={"confirmed": True})
    assert response.status_code == 422
    application.state.agent._busy.add(session_id)
    try:
        response = client.request("DELETE", f"/api/sessions/{session_id}", json={})
        assert response.status_code == 409
        assert client.get(f"/api/sessions/{session_id}").status_code == 200
    finally:
        application.state.agent._busy.discard(session_id)


def test_foreign_rename_delete_and_edit_match_missing_resource(frontend_client):
    client, _, application = frontend_client
    business = application.state.business
    session = business.create_session("someone-else")
    draft = business.create_draft(session["session_id"], "someone-else", "外部标题", "外部描述", "vpn")
    for method, foreign, missing, body in [
        ("PATCH", f"/api/sessions/{session['session_id']}", "/api/sessions/missing", {"title": "新标题"}),
        ("DELETE", f"/api/sessions/{session['session_id']}", "/api/sessions/missing", {}),
        ("PATCH", f"/api/drafts/{draft['draft_id']}", "/api/drafts/missing",
         {"title": "新标题", "description": "新描述", "category": "vpn"}),
    ]:
        actual = client.request(method, foreign, json=body)
        absent = client.request(method, missing, json=body)
        assert actual.status_code == absent.status_code == 404
        assert actual.json() == absent.json()


def test_delete_rollback_preserves_tickets_and_drafts_when_audit_fails(frontend_client):
    _, _, application = frontend_client
    business = application.state.business
    session_id, draft_id = create_draft(application)
    ticket = business.confirm_draft(draft_id)["ticket"]
    with sqlite3.connect(business.path) as db:
        db.execute("CREATE TRIGGER reject_delete BEFORE INSERT ON audit WHEN NEW.action='session_deleted' "
                   "BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END")
    with pytest.raises(sqlite3.Error):
        business.delete_session(session_id, DEMO_USER_ID)
    assert business.get_session(session_id)["drafts"][0]["draft_id"] == draft_id
    assert business.get_ticket(ticket["ticket_id"])["draft_id"] == draft_id


@pytest.mark.parametrize("text", [
    "我的密码是 SecretForTest42",
    "API_KEY=demo_test_credential",
    "Authorization: Bearer abcdefghijklmnop",
    "sk-testcredential123456789",
    "-----BEGIN PRIVATE KEY-----\nTEST-BODY\n-----END PRIVATE KEY-----",
])
def test_sensitive_questions_rejected_without_model_or_storage_and_never_reflected(frontend_client, text):
    client, provider, application = frontend_client
    session_id = application.state.business.create_session()["session_id"]
    response = client.post(f"/api/sessions/{session_id}/messages", json={"question": text})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "sensitive_input"
    assert text not in response.text
    assert provider.model_calls == 0
    assert application.state.business.get_session(session_id)["messages"] == []
    assert client.post("/api/ask", json={"question": text}).status_code == 422
    assert not provider.queries


def test_rename_and_draft_edit_reject_credentials_and_validation_never_echoes_input(frontend_client):
    client, _, application = frontend_client
    session_id, draft_id = create_draft(application)
    secret = "password=PrivateExample42"
    responses = [
        client.patch(f"/api/sessions/{session_id}", json={"title": secret}),
        client.patch(f"/api/drafts/{draft_id}", json={
            "title": "VPN问题", "description": secret, "category": "vpn",
        }),
        client.post(f"/api/sessions/{session_id}/messages", json={"question": "VPN", "extra": secret}),
        client.post(f"/api/sessions/{session_id}/messages", json={"question": secret * 200}),
    ]
    assert all(response.status_code == 422 for response in responses)
    assert all("PrivateExample42" not in response.text for response in responses)
    assert application.state.business.get_draft(draft_id)["description"] == "VPN-403 连接失败。"


def test_normal_error_codes_password_topics_and_recursive_display_masking():
    for text in [
        "DEV-201，资产编号 LT-8241，未重启", "VPN-691 提示密码错误", "忘记密码怎么办？", "密码：错误",
    ]:
        assert sensitive_input_reason(text) is None
        assert redact_sensitive_text(text) == text
    original = {
        "title": "密码是 PrivateExample42", "trace": [{"token": "sk-testcredential12345"}], "count": 3,
        "details": {"api_key": "opaque-private-value", "password": "short", "status": "ok"},
    }
    masked = redact_sensitive_value(original)
    rendered = json.dumps(masked, ensure_ascii=False)
    assert "PrivateExample42" not in rendered and "sk-testcredential12345" not in rendered
    assert "opaque-private-value" not in rendered and '"short"' not in rendered
    assert masked["details"]["status"] == "ok"
    assert masked["count"] == 3 and original["title"].endswith("PrivateExample42")
    assert "TEST-BODY" not in redact_sensitive_text(
        "-----BEGIN RSA PRIVATE KEY-----\nTEST-BODY\n-----END RSA PRIVATE KEY-----"
    )


def test_draft_edit_failure_rolls_back_all_fields(frontend_client):
    _, _, application = frontend_client
    business = application.state.business
    _, draft_id = create_draft(application)
    before = business.get_draft(draft_id)
    with sqlite3.connect(business.path) as db:
        db.execute("CREATE TRIGGER reject_edit BEFORE INSERT ON audit WHEN NEW.action='draft_edited' "
                   "BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END")
    with pytest.raises((BusinessError, sqlite3.Error)):
        business.update_draft(draft_id, DEMO_USER_ID, "新标题", "新描述", "device")
    assert business.get_draft(draft_id) == before


def test_health_reports_real_index_manifest_time_instead_of_check_time(frontend_client):
    client, _, application = frontend_client
    timestamp = 1_700_000_000
    os.utime(application.state.store.manifest_path, (timestamp, timestamp))
    for _ in range(2):
        payload = client.get("/api/health").json()
        assert payload["index"]["updated_at"] == datetime.fromtimestamp(timestamp, UTC).isoformat()
        assert payload["index"]["updated_at_source"] == "index_manifest_mtime"
        assert "真实能力" in payload["checks"]["configuration"]["message"] or (
            payload["checks"]["configuration"]["status"] == "missing"
        )
