"""Executable fault evaluation and missing adapter/timeout failure contracts."""

import json

import httpx2
import openai
import pytest

from app.business import BusinessStore
from app.errors import ITDeskError
from app.providers import OpenAIProvider
from evaluation.faults import FAULT_IDS, execute_fault


@pytest.mark.parametrize("fault", sorted(FAULT_IDS))
async def test_offline_fault_evaluation_checks_real_boundaries(settings, fault):
    original_directory = settings.data_dir

    result = await execute_fault({"id": "fault-" + fault, "fault": fault}, settings)

    assert result["passed"] is True, result
    assert result["checks"] and all(check["passed"] for check in result["checks"])
    assert result["evaluation_kind"] == "deterministic_fault_injection_not_model_quality"
    assert settings.data_dir == original_directory
    assert not original_directory.exists(), "fault run must not write into the caller's application data"
    if fault == "tool_timeout":
        # This scenario uses the real 10s deadline, rather than an immediately raised timeout stub.
        assert result["elapsed_ms"] >= 9000
        assert result["observed"]["tool_trace"][0]["result"]["error"]["code"] == "tool_timeout"
    elif fault == "sqlite_write_failure":
        assert result["observed"]["status"] == "storage_error"
        assert result["observed"]["injection_point"].startswith("BusinessStore.finish_turn:")
        assert {item["name"] for item in result["checks"]} >= {
            "draft_rolled_back", "assistant_message_rolled_back", "draft_audit_rolled_back",
            "request_not_reported_complete",
        }
    elif fault == "confirm_write_failure":
        assert result["observed"]["injection_point"].startswith("BusinessStore.confirm_draft:")
        assert {item["name"] for item in result["checks"]} >= {
            "ticket_rolled_back", "draft_rolled_back", "confirmation_audit_rolled_back",
        }


@pytest.mark.parametrize("fault", ["does_not_exist", None, {"unexpected": "object"}])
async def test_unknown_fault_identifier_cannot_be_reported_as_pass(settings, fault):
    result = await execute_fault({"id": "unknown", "fault": fault}, settings)

    assert result["passed"] is False
    assert result["observed"]["error_code"] == "unknown_fault"


async def test_fault_evaluator_detects_broken_confirmation_invariant(settings, monkeypatch):
    def fake_confirmation(_self, _draft_id, _user_id="demo-user"):
        return {"ticket": {"ticket_id": "FAKE-NO-DATABASE-WRITE"}, "draft": {"status": "confirmed"}}

    monkeypatch.setattr(BusinessStore, "confirm_draft", fake_confirmation)
    result = await execute_fault({"id": "broken", "fault": "duplicate_confirmation"}, settings)

    assert result["passed"] is False
    failed = {item["name"] for item in result["checks"] if not item["passed"]}
    assert "exactly_one_ticket_added" in failed
    assert "exactly_one_confirmation_audit" in failed


async def test_fault_evaluator_unexpected_exception_is_not_a_success_or_secret_leak(settings, monkeypatch):
    def broken_initialize(_self):
        raise RuntimeError("credential-sentinel-do-not-expose")

    monkeypatch.setattr(BusinessStore, "initialize", broken_initialize)
    result = await execute_fault({"id": "broken", "fault": "model_timeout"}, settings)

    assert result["passed"] is False
    assert result["observed"]["scenario_error_type"] == "RuntimeError"
    assert "credential-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["timeout", "authentication"])
async def test_real_triage_adapter_maps_upstream_failures_without_echoing_body(settings, failure):
    request = httpx2.Request("POST", "https://provider.example.test/chat/completions")
    upstream = (
        openai.APITimeoutError(request=request)
        if failure == "timeout"
        else openai.AuthenticationError(
            "credential-sentinel-do-not-expose",
            response=httpx2.Response(401, request=request),
            body={"secret": "credential-sentinel-do-not-expose"},
        )
    )

    class FailingChat:
        async def ainvoke(self, _messages):
            raise upstream

    provider = OpenAIProvider(settings)
    provider._chat = FailingChat()
    with pytest.raises(ITDeskError) as caught:
        await provider.agent_triage({"question": "VPN连接失败", "history": []})

    assert caught.value.code == ("provider_timeout" if failure == "timeout" else "provider_auth")
    assert "credential-sentinel" not in caught.value.message
