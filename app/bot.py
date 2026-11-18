"""Telegram private-chat adapter for document questions."""

import asyncio
import json
import re
from collections import deque
from collections.abc import Awaitable, Callable
from secrets import compare_digest

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from starlette.requests import ClientDisconnect

from app.input_safety import SENSITIVE_INPUT_MESSAGE, redact_sensitive_text, sensitive_input_reason
from app.schemas import AskResponse

TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
MAX_UPDATE_BYTES = 32_768
ANSWER_TIMEOUT_SECONDS = 8
_SECRET_FORMAT = re.compile(r"[A-Za-z0-9_-]{1,256}")
_BOT_TOKEN = re.compile(r"\b\d{8,16}:[A-Za-z0-9_-]{30,64}\b")
_UNAVAILABLE = "知识问答暂不可用，请稍后重新提问或联系 IT 服务台。"
_NO_EVIDENCE = "当前模拟知识库没有足够依据。请补充故障现象或错误码，或联系 IT 服务台。"


def _reply(chat_id: int, text: str) -> JSONResponse:
    # Telegram interprets this webhook response itself. The app needs no outbound connection.
    return JSONResponse({
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    })


def _render_answer(result: AskResponse, webhook_secret: str) -> str:
    if result.status == "failed":
        return _UNAVAILABLE
    if result.status != "answered" or not result.citations:
        return _NO_EVIDENCE

    def clean(text: str) -> str:
        masked = redact_sensitive_text(text.replace(webhook_secret, "[已遮蔽]"))
        return _BOT_TOKEN.sub("[已遮蔽]", masked).encode("utf-8", errors="replace").decode("utf-8")

    # Limit plain text plus citation labels below Telegram's 4096-character limit.
    answer = clean(result.answer).strip()[:1600] or _NO_EVIDENCE
    labels = [clean(c.title).strip()[:75] for c in result.citations[:3]]
    sources = "；".join(dict.fromkeys(label for label in labels if label))
    return f"{answer}\n\n手册来源：{sources}" if sources else answer


def build_telegram_router(
    answer: Callable[[str], Awaitable[AskResponse]],
    *,
    secret_token: SecretStr | None,
    allowed_chat_id: int | None,
) -> APIRouter:
    """Expose one configured private chat and only the injected document QA operation."""
    router = APIRouter()
    secret = secret_token.get_secret_value() if secret_token else ""
    if not _SECRET_FORMAT.fullmatch(secret) or type(allowed_chat_id) is not int:
        return router
    if not 0 < allowed_chat_id < 2**52:
        return router
    recent_updates: deque[int] = deque(maxlen=256)
    answer_lock = asyncio.Lock()

    @router.post("/api/bot/telegram", include_in_schema=False)
    async def telegram_update(request: Request) -> Response:
        supplied = request.headers.getlist(TELEGRAM_SECRET_HEADER)
        if len(supplied) != 1 or not compare_digest(secret.encode("ascii"), supplied[0].encode("latin-1")):
            return Response(status_code=403)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return Response(status_code=415)

        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_UPDATE_BYTES:
                    return Response(status_code=413)
                body.extend(chunk)
            update = json.loads(body)
        except (ValueError, RecursionError, ClientDisconnect):
            return Response(status_code=400)
        if not isinstance(update, dict):
            return Response(status_code=400)
        update_id = update.get("update_id")
        if type(update_id) is not int or not 0 <= update_id < 2**63:
            return Response(status_code=400)

        message = update.get("message")
        if not isinstance(message, dict):
            return Response(status_code=204)
        chat = message.get("chat")
        sender = message.get("from")
        if (
            not isinstance(chat, dict) or chat.get("type") != "private"
            or type(chat.get("id")) is not int or chat["id"] != allowed_chat_id
            or not isinstance(sender, dict) or sender.get("is_bot") is not False
            or type(sender.get("id")) is not int or sender["id"] != allowed_chat_id
        ):
            return Response(status_code=204)
        text = message.get("text")
        if not isinstance(text, str) or update_id in recent_updates:
            return Response(status_code=204)

        recent_updates.append(update_id)
        question = text.strip()
        if not question or len(question) > 2000:
            return _reply(allowed_chat_id, "请输入 1–2000 字的 IT 故障问题。")
        if secret in question or _BOT_TOKEN.search(question) or sensitive_input_reason(question):
            return _reply(allowed_chat_id, SENSITIVE_INPUT_MESSAGE)
        if answer_lock.locked():
            return _reply(allowed_chat_id, "上一条问题仍在处理中，请稍后重新提问。")

        async with answer_lock:
            try:
                async with asyncio.timeout(ANSWER_TIMEOUT_SECONDS):
                    result = await answer(question)
                return _reply(allowed_chat_id, _render_answer(result, secret))
            except Exception:
                return _reply(allowed_chat_id, _UNAVAILABLE)

    return router
