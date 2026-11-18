import asyncio
import json
import logging
import sqlite3
import time
import uuid

from app.config import Settings
from app.errors import ITDeskError
from app.providers import Provider
from app.retrieval.store import KnowledgeStore
from app.schemas import AskResponse, Citation, ErrorInfo, Metrics

logger = logging.getLogger(__name__)
NO_EVIDENCE = "当前知识库中没有足够证据回答这个问题。请补充具体症状或错误码，或联系 IT 人工支持。"


class AnswerService:
    def __init__(self, settings: Settings, provider: Provider, store: KnowledgeStore):
        self.settings, self.provider, self.store = settings, provider, store

    async def ask(self, question: str) -> AskResponse:
        started = time.perf_counter()
        response = AskResponse(run_id=uuid.uuid4().hex, status="failed", answer="", metrics=Metrics())
        try:
            async with asyncio.timeout(90):
                phase = time.perf_counter()
                retrieved = await self.store.retrieve(question, self.settings.top_k)
                response.metrics.retrieval_ms = (time.perf_counter() - phase) * 1000
                response.metrics.embedding_tokens = retrieved.embedding_tokens
                response.retrieved = retrieved.chunks
                if not retrieved.chunks:
                    response.status, response.answer = "no_evidence", NO_EVIDENCE
                else:
                    phase = time.perf_counter()
                    for attempt in range(2):
                        try:
                            generated = await self.provider.generate_answer(question, retrieved.chunks)
                        except ITDeskError as error:
                            response.metrics.input_tokens += getattr(error, "input_tokens", 0)
                            response.metrics.output_tokens += getattr(error, "output_tokens", 0)
                            if attempt == 0 and error.code == "invalid_model_output":
                                continue
                            raise
                        response.metrics.input_tokens += generated.input_tokens
                        response.metrics.output_tokens += generated.output_tokens
                        draft = generated.draft
                        available = {c.chunk_id: c for c in retrieved.chunks}
                        ids = list(dict.fromkeys(draft.citation_ids))
                        if not draft.evidence_sufficient:
                            response.status, response.answer = "no_evidence", NO_EVIDENCE
                            break
                        if not ids or any(identifier not in available for identifier in ids):
                            if attempt == 0:
                                continue
                            raise ITDeskError("invalid_citations", "模型引用校验失败，已拦截未经验证的答案。")
                        response.status, response.answer = "answered", draft.answer
                        response.citations = [
                            Citation.model_validate(available[key].model_dump()) for key in ids
                        ]
                        break
                    response.metrics.generation_ms = (time.perf_counter() - phase) * 1000
        except ITDeskError as error:
            response.error = ErrorInfo(code=error.code, message=error.message)
            response.answer = "本次请求未完成。" + error.message
        except TimeoutError:
            response.error = ErrorInfo(code="request_timeout", message="本轮请求超过 90 秒，请稍后重试。")
            response.answer = response.error.message
        except Exception as error:
            # Only the class is logged: exception text could contain provider credentials or prompts.
            logger.error("run_id=%s error_type=%s", response.run_id, type(error).__name__)
            response.error = ErrorInfo(
                code="internal_error", message="本地服务暂时不可用，请检查索引和运行环境。"
            )
            response.answer = response.error.message
        response.metrics.total_ms = (time.perf_counter() - started) * 1000
        m, s = response.metrics, self.settings
        prices = [
            s.chat_input_price_per_million,
            s.chat_output_price_per_million,
            s.embedding_price_per_million,
        ]
        usage = [m.input_tokens, m.output_tokens, m.embedding_tokens]
        m.cost_usd = (
            sum((price or 0) * tokens for price, tokens in zip(prices, usage, strict=True)) / 1e6
            if all(price is not None or tokens == 0 for price, tokens in zip(prices, usage, strict=True))
            else None
        )
        if response.status == "failed":
            m.cost_usd = None  # Upstream failure may still have been billed without returning usage.
        try:
            await asyncio.to_thread(self._record, response)
        except (OSError, sqlite3.Error):
            response.status = "failed"
            response.answer = "运行记录保存失败，本次请求未完成，请检查 data 目录权限或磁盘空间。"
            response.citations = []
            response.error = ErrorInfo(code="record_failed", message=response.answer)
        response.metrics.total_ms = (time.perf_counter() - started) * 1000
        return response

    def _record(self, response: AskResponse):
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.settings.data_dir / "runs.sqlite3", timeout=5) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL, metrics_json TEXT NOT NULL, citation_ids_json TEXT NOT NULL,
                error_code TEXT)""")
            db.execute(
                "INSERT INTO runs(run_id,status,metrics_json,citation_ids_json,error_code) VALUES(?,?,?,?,?)",
                (
                    response.run_id,
                    response.status,
                    response.metrics.model_dump_json(),
                    json.dumps([c.chunk_id for c in response.citations]),
                    response.error.code if response.error else None,
                ),
            )
