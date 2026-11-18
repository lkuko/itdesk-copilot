import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agent import AgentService
from app.bot import build_telegram_router
from app.business import DEMO_USER_ID, BusinessError, BusinessStore, DraftFields, SessionRename
from app.config import Settings
from app.errors import ITDeskError
from app.input_safety import redact_sensitive_text, sensitive_input_reason
from app.providers import OpenAIProvider, Provider
from app.retrieval.lab import create_knowledge_router
from app.retrieval.store import KnowledgeStore
from app.retrieval.workspace import KnowledgeWorkspace, create_workspace_router
from app.schemas import AskRequest, AskResponse, EmptyRequest, MessageRequest
from app.service import AnswerService


def create_app(settings: Settings | None = None, provider: Provider | None = None) -> FastAPI:
    settings = settings or Settings()
    provider = provider or OpenAIProvider(settings)
    store = KnowledgeStore(settings, provider)
    workspace = KnowledgeWorkspace(settings, provider, store)
    service = AnswerService(settings, provider, workspace)
    business = BusinessStore(settings.data_dir)

    @asynccontextmanager
    async def lifespan(application):
        await asyncio.to_thread(business.initialize)
        agent_logger = logging.getLogger("app.agent")
        handler = RotatingFileHandler(
            settings.data_dir / "agent.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        agent_logger.setLevel(logging.INFO)
        agent_logger.addHandler(handler)
        try:
            async with AsyncSqliteSaver.from_conn_string(
                str(settings.data_dir / "checkpoints.sqlite3")
            ) as saver:
                await saver.setup()
                application.state.checkpointer = saver
                application.state.agent = AgentService(settings, provider, workspace, business, saver)
                yield
        finally:
            agent_logger.removeHandler(handler)
            handler.close()
            if hasattr(provider, "close"):
                await provider.close()

    app = FastAPI(title="ITDesk Copilot Lite", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.state.settings, app.state.store, app.state.provider = settings, store, provider
    app.state.business = business
    app.state.knowledge_workspace = workspace
    app.include_router(create_workspace_router(workspace))
    app.include_router(create_knowledge_router(settings, provider, store, workspace=workspace))
    app.include_router(build_telegram_router(
        service.ask,
        secret_token=settings.telegram_webhook_secret,
        allowed_chat_id=settings.telegram_allowed_chat_id,
    ))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, error):
        # Pydantic's default response includes the rejected input. Never reflect
        # a password/token from a malformed or oversized request into the UI.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": [redact_sensitive_text(loc) if isinstance(loc, str) else loc for loc in item["loc"]],
             "msg": redact_sensitive_text(item["msg"]), "type": item["type"]}
            for item in error.errors()
        ]})

    def safe_input(*texts):
        for text in texts:
            reason = sensitive_input_reason(text)
            if reason:
                raise BusinessError(422, "sensitive_input", reason)

    @app.exception_handler(BusinessError)
    async def business_error(_request, error):
        return JSONResponse(
            status_code=error.status_code,
            content={
                "detail": {
                    "code": error.code,
                    "message": error.message,
                }
            },
        )

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_error(_request, _error):
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "storage_unavailable",
                    "message": "本地存储不可用，请检查磁盘与目录权限后刷新会话。",
                }
            },
        )

    @app.get("/api/health")
    def health():
        missing = settings.missing_configuration()
        configuration = {
            "status": "missing" if missing else "ready",
            "message": "待配置：" + ", ".join(missing)
            if missing
            else "配置字段齐全；真实能力请运行 doctor --live。",
        }
        embedding_model = settings.embedding_model
        index_updated_at = None
        try:
            snapshot = workspace.snapshot()
        except ITDeskError as error:
            index = {"status": "invalid", "message": error.message}
        else:
            index = snapshot.store.health()
            embedding_model = snapshot.settings.embedding_model
            try:
                index_updated_at = datetime.fromtimestamp(
                    snapshot.store.manifest_path.stat().st_mtime, UTC
                ).isoformat()
            except OSError:
                pass
        return {
            "status": "ok" if not missing and index["status"] == "ready" else "not_ready",
            "checks": {"configuration": configuration, "index": index},
            "models": {"chat": settings.chat_model, "embedding": embedding_model},
            "index": {
                **{key: index[key] for key in ("document_count", "chunk_count") if key in index},
                "updated_at": index_updated_at,
                "updated_at_source": "index_manifest_mtime",
            },
            "mode": "local_demo_single_user",
            "agent": {"max_tool_attempts": 3, "tools": 3, "ticket_write": "explicit_confirmation"},
        }

    @app.post("/api/ask", response_model=AskResponse)
    async def ask(request: AskRequest):
        safe_input(request.question)
        return await service.ask(request.question)

    @app.post("/api/sessions", status_code=201)
    async def create_session(_body: EmptyRequest):
        return await asyncio.to_thread(business.create_session, DEMO_USER_ID)

    @app.get("/api/sessions")
    async def sessions(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
        return {"sessions": await asyncio.to_thread(business.list_sessions, DEMO_USER_ID, limit, offset)}

    @app.get("/api/sessions/{session_id}")
    async def session_detail(session_id: str):
        return await asyncio.to_thread(business.get_session, session_id, DEMO_USER_ID)

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, request: SessionRename):
        return await asyncio.to_thread(business.rename_session, session_id, DEMO_USER_ID, request.title)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, _body: EmptyRequest):
        agent = app.state.agent
        if session_id in agent._busy:
            raise BusinessError(409, "session_busy", "本会话正在处理请求，完成后才能删除。")
        agent._busy.add(session_id)
        try:
            # Check ownership before touching graph storage. Clearing diagnostic
            # checkpoints first is safe: business history is the source of truth.
            await asyncio.to_thread(business.get_session, session_id, DEMO_USER_ID)
            await app.state.checkpointer.adelete_thread(session_id)
            return await asyncio.to_thread(business.delete_session, session_id, DEMO_USER_ID)
        finally:
            agent._busy.discard(session_id)

    @app.post("/api/sessions/{session_id}/messages")
    async def message(session_id: str, request: MessageRequest):
        safe_input(request.question)
        return await app.state.agent.ask(session_id, DEMO_USER_ID, request.question, request.request_id)

    @app.get("/api/drafts/{draft_id}")
    async def draft(draft_id: str):
        return await asyncio.to_thread(business.get_draft, draft_id, DEMO_USER_ID)

    @app.patch("/api/drafts/{draft_id}")
    async def update_draft(draft_id: str, request: DraftFields):
        return await asyncio.to_thread(
            business.update_draft, draft_id, DEMO_USER_ID,
            request.title, request.description, request.category,
        )

    @app.post("/api/drafts/{draft_id}/confirm")
    async def confirm(draft_id: str, _body: EmptyRequest):
        result = await asyncio.to_thread(business.confirm_draft, draft_id, DEMO_USER_ID)
        return {"status": "ticket_created", **result}

    @app.post("/api/drafts/{draft_id}/cancel")
    async def cancel(draft_id: str, _body: EmptyRequest):
        result = await asyncio.to_thread(business.cancel_draft, draft_id, DEMO_USER_ID)
        return {"status": "cancelled", **result}

    @app.get("/api/tickets")
    async def tickets(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
        return {"tickets": await asyncio.to_thread(business.list_tickets, DEMO_USER_ID, limit, offset)}

    @app.get("/api/tickets/{ticket_id}")
    async def ticket(ticket_id: str):
        return await asyncio.to_thread(business.get_ticket, ticket_id, DEMO_USER_ID)

    return app


app = create_app()
