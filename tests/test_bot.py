"""Exercise Telegram's webhook payload and inline reply entirely in-process."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import bot
from app.schemas import AskResponse, Citation

PATH = "/api/bot/telegram"
CHAT_ID = 123456789
SECRET = "local-fixture-telegram-webhook-secret"
HEADERS = {bot.TELEGRAM_SECRET_HEADER: SECRET}


def update(text=" VPN-403 怎么处理？ ", *, update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"type": "private", "id": CHAT_ID},
            "from": {"id": CHAT_ID, "is_bot": False},
            "text": text,
        },
    }


def make_client(answer, *, secret=SecretStr(SECRET), chat_id=CHAT_ID):
    app = FastAPI()
    app.include_router(bot.build_telegram_router(answer, secret_token=secret, allowed_chat_id=chat_id))
    return TestClient(app)


@pytest.fixture
def answer():
    return AsyncMock(return_value=AskResponse(
        run_id="fixture-run-id",
        status="answered",
        answer="请核对设备登记。此处仅引用模拟手册。",
        citations=[Citation(
            chunk_id="vpn-001:0", doc_id="vpn-001", title="VPN 故障手册",
            source="C:/private/absolute-path.md", version="1", content="不回传整段原文。",
        )],
    ))


@pytest.mark.parametrize(("secret", "chat_id"), [
    (None, CHAT_ID), (SecretStr(SECRET), None), (SecretStr("a portal URL"), CHAT_ID), (SecretStr(SECRET), -1),
])
def test_needs_valid_secret_and_one_private_chat(answer, secret, chat_id):
    with make_client(answer, secret=secret, chat_id=chat_id) as client:
        assert client.post(PATH, json=update(), headers=HEADERS).status_code == 404
    answer.assert_not_awaited()


def test_rejects_missing_wrong_or_repeated_secret_before_decoding(answer):
    with make_client(answer) as client:
        for headers in ({}, {bot.TELEGRAM_SECRET_HEADER: "wrong"}, list(HEADERS.items()) * 2):
            response = client.post(PATH, content=b"bad json", headers=headers)
            assert response.status_code == 403
            assert response.content == b""
    answer.assert_not_awaited()


def test_replies_inline_to_matching_private_chat_and_deduplicates_update(answer):
    with make_client(answer) as client:
        response = client.post(PATH, json=update(), headers=HEADERS)
        repeated = client.post(PATH, json=update(), headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "method": "sendMessage",
        "chat_id": CHAT_ID,
        "text": "请核对设备登记。此处仅引用模拟手册。\n\n手册来源：VPN 故障手册",
        "link_preview_options": {"is_disabled": True},
    }
    assert repeated.status_code == 204
    answer.assert_awaited_once_with("VPN-403 怎么处理？")


def test_ignores_other_chats_groups_bot_users_and_non_text_updates(answer):
    with make_client(answer) as client:
        for field, value in (
            ("chat", {"type": "group", "id": CHAT_ID}),
            ("chat", {"type": "private", "id": CHAT_ID + 1}),
            ("from", {"id": CHAT_ID, "is_bot": True}),
            ("from", {"id": CHAT_ID + 1, "is_bot": False}),
            ("text", None),
        ):
            payload = update()
            payload["message"][field] = value
            assert client.post(PATH, json=payload, headers=HEADERS).status_code == 204
        assert client.post(PATH, json={"update_id": 1}, headers=HEADERS).status_code == 204
    answer.assert_not_awaited()


def test_rejects_invalid_json_and_limits_request_size_without_echoing_input(answer):
    headers = {**HEADERS, "Content-Type": "application/json"}
    with make_client(answer) as client:
        for content in (b"{private", b"[]", b'{"update_id":true}'):
            response = client.post(PATH, content=content, headers=headers)
            assert response.status_code == 400
            assert not response.content
        oversized = client.post(PATH, content=b"x" * (bot.MAX_UPDATE_BYTES + 1), headers=headers)
        assert oversized.status_code == 413
    answer.assert_not_awaited()


@pytest.mark.parametrize("text", [" ", "文" * 2001, f"请求 {SECRET}", "password=not-for-the-provider"])
def test_bounds_and_filters_questions_before_answering(answer, text):
    with make_client(answer) as client:
        response = client.post(PATH, json=update(text), headers=HEADERS)
    assert response.status_code == 200
    shown = response.json()["text"]
    assert "1–2000" in shown or "删除敏感内容" in shown
    assert SECRET not in shown and "not-for-the-provider" not in shown
    answer.assert_not_awaited()


def test_masks_provider_display_secrets_and_bounds_plain_text(answer):
    answer.return_value.answer = f"{SECRET} password=private-material {'😀' * 3000}"
    answer.return_value.citations[0].title = f"参考 {SECRET}"
    with make_client(answer) as client:
        response = client.post(PATH, json=update(), headers=HEADERS)
    shown = response.json()["text"]
    assert SECRET not in shown and "private-material" not in shown
    assert len(shown.encode("utf-16-le")) // 2 <= 4096
    assert "手册来源：参考 [已遮蔽]" in shown


@pytest.mark.parametrize("mode", ["failed_result", "exception", "timeout"])
def test_fails_with_a_fixed_reply_when_qa_is_unavailable(answer, mode, monkeypatch):
    if mode == "failed_result":
        answer.return_value.status = "failed"
        answer.return_value.answer = "provider returned private details"
    elif mode == "exception":
        answer.side_effect = ValueError("provider returned private details")
    else:
        async def slow_answer(_question):
            await asyncio.sleep(1)

        answer.side_effect = slow_answer
        monkeypatch.setattr(bot, "ANSWER_TIMEOUT_SECONDS", 0.01)
    with make_client(answer) as client:
        response = client.post(PATH, json=update(), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["text"] == "知识问答暂不可用，请稍后重新提问或联系 IT 服务台。"


def test_insufficient_evidence_does_not_forward_raw_answer(answer):
    answer.return_value.status = "no_evidence"
    answer.return_value.answer = "不发送此内容"
    with make_client(answer) as client:
        response = client.post(PATH, json=update(), headers=HEADERS)
    assert "没有足够依据" in response.json()["text"]
    assert "不发送此内容" not in response.json()["text"]
