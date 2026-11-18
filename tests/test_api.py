"""API behavior: verified citations, safe refusal and visible provider failures."""

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.errors import ITDeskError
from app.main import create_app
from app.schemas import AnswerDraft
from app.service import AnswerService


@pytest.fixture
def client(settings, provider):
    application = create_app(settings=settings, provider=provider)
    asyncio.run(application.state.store.build_index())
    with TestClient(application) as test_client:
        yield test_client


def test_answer_returns_source_text_and_usage(client, provider):
    response = client.post("/api/ask", json={"question": "VPN-403 怎么处理？"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["run_id"]
    assert payload["error"] is None
    assert payload["citations"]
    assert payload["citations"][0]["doc_id"] == "vpn-registration"
    assert "VPN-403" in payload["citations"][0]["content"]
    retrieved_ids = {chunk["chunk_id"] for chunk in payload["retrieved"]}
    assert all(item["chunk_id"] in retrieved_ids for item in payload["citations"])
    assert payload["metrics"]["input_tokens"] == 20
    assert payload["metrics"]["output_tokens"] == 15
    assert payload["metrics"]["total_ms"] >= 0
    assert payload["metrics"]["cost_usd"] is None
    assert len(provider.generation_calls) == 1


def test_unrelated_query_refuses_without_calling_generation(client, provider):
    response = client.post("/api/ask", json={"question": "公司绩效奖金怎么算？"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "no_evidence"
    assert payload["citations"] == []
    assert payload["retrieved"] == []
    assert provider.generation_calls == []


def test_invalid_citation_never_reaches_the_customer(client, provider):
    provider.draft_override = AnswerDraft(
        answer="危险的虚构答案", citation_ids=["another-session-or-invented-id"], evidence_sufficient=True
    )

    response = client.post("/api/ask", json={"question": "VPN-403 怎么处理？"})

    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["citations"] == []
    assert "危险的虚构答案" not in payload["answer"]
    assert payload["error"]["code"]


def test_model_admitting_insufficient_evidence_is_a_refusal(client, provider):
    provider.draft_override = AnswerDraft(
        answer="证据不足，无法确定具体处理步骤。", citation_ids=[], evidence_sufficient=False
    )

    payload = client.post("/api/ask", json={"question": "VPN-403 怎么处理？"}).json()

    assert payload["status"] == "no_evidence"
    assert payload["citations"] == []


def test_provider_failure_is_explicit_not_a_fabricated_answer(client, provider):
    provider.generation_error = ITDeskError("provider_timeout", "模型请求超时，请稍后重试。")

    response = client.post("/api/ask", json={"question": "VPN-403 怎么处理？"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "provider_timeout"
    assert payload["citations"] == []
    assert "超时" in payload["error"]["message"]


def test_malformed_model_output_gets_only_one_repair_attempt(client, provider):
    error = ITDeskError("invalid_model_output", "模型没有返回有效 JSON。")
    error.input_tokens, error.output_tokens = 10, 4
    provider.generation_error = error

    payload = client.post("/api/ask", json={"question": "VPN-403 怎么处理？"}).json()

    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "invalid_model_output"
    assert len(provider.generation_calls) == 2
    assert payload["metrics"]["input_tokens"] == 20
    assert payload["metrics"]["output_tokens"] == 8


def test_unexpected_provider_exception_does_not_expose_secrets(client, provider, caplog):
    provider.generation_error = RuntimeError("Authorization: Bearer never-show-this-secret")

    response = client.post("/api/ask", json={"question": "VPN-403 怎么处理？"})

    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == "internal_error"
    assert "never-show-this-secret" not in response.text
    assert "never-show-this-secret" not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   \n\t"},
        {"question": "过长" * 1001},
        {"question": "VPN-403", "user_id": "fake-admin"},
    ],
)
def test_invalid_request_is_rejected_before_provider_work(client, provider, body):
    before = (len(provider.queries), len(provider.generation_calls))

    response = client.post("/api/ask", json=body)

    assert response.status_code == 422
    assert before == (len(provider.queries), len(provider.generation_calls))


def test_health_reports_ready_without_exposing_credentials(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["index"]["document_count"] == 3
    serialized = response.text
    assert "test-chat-key" not in serialized
    assert "test-embedding-key" not in serialized


def test_unconfigured_health_and_request_fail_usefully(settings, provider):
    missing = settings.model_copy(update={"chat_model": "", "embedding_model": ""})
    with TestClient(create_app(settings=missing, provider=provider)) as local_client:
        health = local_client.get("/api/health")
        response = local_client.post("/api/ask", json={"question": "VPN-403"})

    assert health.json()["status"] == "not_ready"
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"]
    assert provider.queries == []


def test_run_record_failure_never_returns_success(client, monkeypatch):
    def fail_record(_self, _response):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(AnswerService, "_record", fail_record)

    payload = client.post("/api/ask", json={"question": "VPN-403 怎么处理？"}).json()

    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "record_failed"
    assert payload["citations"] == []
