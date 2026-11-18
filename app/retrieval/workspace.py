"""Versioned, local knowledge workspace. Only a healthy generation can become active."""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from chromadb.errors import ChromaError
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.business import BusinessError
from app.config import Settings
from app.errors import ITDeskError
from app.input_safety import sensitive_input_reason
from app.providers import Provider
from app.retrieval.ingestion import extract_error_codes, iter_chunk_spans, load_documents
from app.retrieval.lab import CORPUS_LIMITS, MAX_CHUNKS_PER_STRATEGY, DocumentDetail, IndexStatus, _excerpt
from app.retrieval.profile import ModelRegistry, RetrievalProfile
from app.retrieval.store import KnowledgeStore
from app.schemas import EmbeddingBatch, KnowledgeDocument, RetrievalResult

BUILD_TIMEOUT_SECONDS = 120
INITIAL_REVISION = "seed"
_GENERATION_PATTERN = r"^[0-9a-f]{32}$"
_MANAGED_PATTERN = r"^managed-[0-9a-f]{24}$"
_FILE_NAME_UNSUPPORTED = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


class ImportFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=4, max_length=110)
    content: str = Field(min_length=1, max_length=48_000)
    title: str | None = Field(default=None, max_length=120)
    category: Literal["vpn", "email", "device"] = "device"

    @field_validator("name")
    @classmethod
    def supported_filename(cls, name: str) -> str:
        if (
            _FILE_NAME_UNSUPPORTED.search(name)
            or name != name.strip()
            or Path(name).suffix.lower() not in {".md", ".txt"}
        ):
            raise ValueError("请使用不含路径的 .md 或 .txt 文件名")
        return name


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile: RetrievalProfile
    files: list[ImportFile] = Field(default_factory=list, max_length=5)


class ApplyRequest(PreviewRequest):
    remove_ids: list[str] = Field(default_factory=list, max_length=64)
    revision: str = Field(pattern=r"^(?:seed|[0-9a-f]{32})$")


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=500)

    @field_validator("query")
    @classmethod
    def meaningful_query(cls, query: str) -> str:
        if not query.strip():
            raise ValueError("查询不能为空")
        return query.strip()


class PreviewResult(BaseModel):
    documents: list[DocumentDetail]


class WorkspaceState(BaseModel):
    profile: RetrievalProfile
    embedding_models: list[str]
    rerank_models: list[str]
    revision: str
    managed_ids: list[str]
    index: IndexStatus
    rebuilt: bool | None = None


class WorkspaceHit(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    source: str
    version: str
    content: str
    similarity: float | None
    rerank_score: float | None
    exact_match: bool


class SearchResult(BaseModel):
    hits: list[WorkspaceHit]
    rerank_status: Literal["disabled", "applied", "fallback", "no_candidates"]
    embedding_tokens: int


class _Pointer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(pattern=_GENERATION_PATTERN)
    generation: str = Field(pattern=_GENERATION_PATTERN)
    profile: RetrievalProfile
    managed_ids: list[str] = Field(max_length=64)

    @field_validator("managed_ids")
    @classmethod
    def valid_ids(cls, ids: list[str]) -> list[str]:
        if any(not re.fullmatch(_MANAGED_PATTERN, item) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("无效的知识文档编号")
        return ids


@dataclass(frozen=True)
class WorkspaceSnapshot:
    settings: Settings
    provider: Provider
    store: KnowledgeStore
    profile: RetrievalProfile
    revision: str = INITIAL_REVISION
    generation: str | None = None
    managed_ids: tuple[str, ...] = ()


def _prepare_import(item: ImportFile) -> tuple[KnowledgeDocument, bytes]:
    content = item.content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    title = (item.title or "").strip() or Path(item.name).stem
    if any(sensitive_input_reason(value) for value in (item.name, title, content)):
        raise BusinessError(422, "sensitive_document", "导入文档含疑似凭证，请移除后重试。")
    if not content or "\x00" in content:
        raise BusinessError(422, "invalid_document", "文档正文需要为非空的 UTF-8 文本。")
    try:
        if len(item.content.encode()) > CORPUS_LIMITS.document_bytes:
            raise BusinessError(413, "knowledge_limit", "单个文档最多 48000 字节。")
        identity = json.dumps([item.name.casefold(), title, item.category, content], ensure_ascii=False)
        doc_id = "managed-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        metadata = {
            "doc_id": doc_id,
            "title": title,
            "category": item.category,
            "version": "1.0",
            "error_codes": sorted(extract_error_codes(content)),
        }
        source = f"imported/{doc_id}-{item.name}"
        # JSON is valid YAML. Escape the front-matter delimiter inside string values.
        front_matter = json.dumps(metadata, ensure_ascii=False).replace("---", "\\u002d\\u002d\\u002d")
        raw = f"---\n{front_matter}\n---\n\n{content}\n".encode()
    except UnicodeError:
        raise BusinessError(422, "invalid_document", "文档需要为有效的 UTF-8 文本。") from None
    if len(raw) > CORPUS_LIMITS.document_bytes:
        raise BusinessError(413, "knowledge_limit", "文档加上元数据后超过 48000 字节。")
    return KnowledgeDocument(**metadata, source=source, content=content), raw


def _check_corpus(documents: list[KnowledgeDocument], profile: RetrievalProfile) -> None:
    count = 0
    for _span in iter_chunk_spans(
        documents, profile.chunk_size, profile.chunk_overlap, profile.chunk_strategy
    ):
        count += 1
        if count > MAX_CHUNKS_PER_STRATEGY:
            raise BusinessError(413, "chunk_limit", "切片数超过知识库上限，请缩小文档范围或调大窗口。")


def _index_fields(profile: RetrievalProfile) -> tuple:
    return profile.embedding_model, profile.chunk_strategy, profile.chunk_size, profile.chunk_overlap


class KnowledgeWorkspace:
    def __init__(
        self,
        settings: Settings,
        provider: Provider,
        store: KnowledgeStore,
        registry: ModelRegistry | None = None,
    ):
        self.settings, self.provider, self.legacy_store = settings, provider, store
        self.registry = registry if registry is not None else ModelRegistry(settings)
        self.root = settings.data_dir / "knowledge-workspace"
        self.pointer_path = self.root / "current.json"
        self._lock = asyncio.Lock()
        self._cached: WorkspaceSnapshot | None = None

    def _generation_settings(self, generation: str) -> Settings:
        root = self.root / "generations" / generation
        return self.settings.model_copy(
            update={"knowledge_dir": root / "documents", "data_dir": root / "index"}
        )

    def snapshot(self) -> WorkspaceSnapshot:
        try:
            raw = self.pointer_path.read_bytes()
        except FileNotFoundError:
            if self._cached is not None:
                raise ITDeskError("workspace_invalid", "当前知识库版本文件缺失。") from None
            return WorkspaceSnapshot(
                self.settings, self.provider, self.legacy_store, self.registry.default_profile()
            )
        except OSError:
            raise ITDeskError("workspace_invalid", "无法读取当前知识库版本。") from None
        try:
            pointer = _Pointer.model_validate_json(raw)
            if (
                self._cached is not None
                and self._cached.revision == pointer.revision
                and self._cached.generation == pointer.generation
                and self._cached.profile == pointer.profile
                and self._cached.managed_ids == tuple(pointer.managed_ids)
            ):
                return self._cached
            generation_settings = self._generation_settings(pointer.generation)
            store = self.registry.make_store(generation_settings, self.provider, pointer.profile)
        except (ValueError, BusinessError, ITDeskError):
            raise ITDeskError("workspace_invalid", "当前知识库配置无效，或所用模型已撤销。") from None
        snapshot = WorkspaceSnapshot(
            store.settings.model_copy(
                update={
                    "embedding_model": pointer.profile.embedding_model,
                    "chunk_size": pointer.profile.chunk_size,
                    "chunk_overlap": pointer.profile.chunk_overlap,
                    "top_k": pointer.profile.top_k,
                    "max_cosine_distance": 1 - pointer.profile.score_threshold,
                }
            ),
            store.provider,
            store,
            pointer.profile,
            pointer.revision,
            pointer.generation,
            tuple(pointer.managed_ids),
        )
        self._cached = snapshot
        return snapshot

    def health(self) -> dict:
        try:
            return self.snapshot().store.health()
        except ITDeskError as error:
            return {"status": "invalid", "message": error.message}

    async def retrieve(
        self,
        question: str,
        top_k: int | None = None,
        *,
        strategy: str | None = None,
        query_embedding: EmbeddingBatch | None = None,
    ) -> RetrievalResult:
        current = self.snapshot()
        if current.generation is not None:
            # The active workspace profile owns search configuration for all QA callers.
            return await current.store.retrieve(question)
        arguments = {"query_embedding": query_embedding}
        if top_k is not None:
            arguments["top_k"] = top_k
        if strategy is not None:
            arguments["strategy"] = strategy
        return await current.store.retrieve(question, **arguments)

    def state(
        self, current: WorkspaceSnapshot | None = None, *, rebuilt: bool | None = None
    ) -> WorkspaceState:
        current = current or self.snapshot()
        return WorkspaceState(
            profile=current.profile,
            embedding_models=list(self.registry.embedding_models),
            rerank_models=list(self.registry.rerank_models),
            revision=current.revision,
            managed_ids=list(current.managed_ids),
            index=IndexStatus.model_validate(current.store.health()),
            rebuilt=rebuilt,
        )

    def preview(self, request: PreviewRequest) -> PreviewResult:
        self.registry.validate_profile(request.profile)
        documents = [_prepare_import(item)[0] for item in request.files]
        if len({item.doc_id for item in documents}) != len(documents):
            raise BusinessError(422, "duplicate_doc_id", "本次导入有重复文档。")
        _check_corpus(documents, request.profile)
        return PreviewResult(
            documents=[
                DocumentDetail(
                    **document.model_dump(),
                    chunks=[
                        _excerpt(span)
                        for span in iter_chunk_spans(
                            [document],
                            request.profile.chunk_size,
                            request.profile.chunk_overlap,
                            request.profile.chunk_strategy,
                        )
                    ],
                )
                for document in documents
            ]
        )

    @contextmanager
    def _process_writer(self) -> Iterator[None]:
        """SQLite releases this writer lock when the process or connection exits."""
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / "writer.sqlite3", timeout=0)
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise BusinessError(409, "workspace_busy", "另一个知识库更新正在运行。") from None
                raise
            yield
        finally:
            connection.close()

    def _copy_documents(
        self,
        current: WorkspaceSnapshot,
        destination: Path,
        incoming: list[tuple[KnowledgeDocument, bytes]],
        remove_ids: set[str],
    ) -> list[str]:
        source = current.settings.knowledge_dir
        documents = load_documents(source, limits=CORPUS_LIMITS)
        removable = {
            doc.doc_id
            for doc in documents
            if doc.doc_id in current.managed_ids and doc.source.startswith(f"imported/{doc.doc_id}-")
        }
        if not remove_ids.issubset(removable):
            raise BusinessError(422, "invalid_remove", "只能移除工作台导入的文档。")
        existing_ids = {document.doc_id for document in documents} - remove_ids
        incoming_ids = {document.doc_id for document, _raw in incoming}
        if len(incoming_ids) != len(incoming) or incoming_ids & existing_ids:
            raise BusinessError(422, "duplicate_doc_id", "知识库中已存在相同文档。")
        source_root = source.resolve()
        destination.mkdir(parents=True, exist_ok=False)
        for document in documents:
            if document.doc_id in remove_ids:
                continue
            original = (source / document.source).resolve()
            if not original.is_relative_to(source_root):
                raise BusinessError(422, "invalid_document", "知识目录中存在指向外部的文档。")
            target = destination / document.source
            target.parent.mkdir(parents=True, exist_ok=True)
            with original.open("rb") as handle:
                content = handle.read(CORPUS_LIMITS.document_bytes + 1)
            if len(content) > CORPUS_LIMITS.document_bytes:
                raise BusinessError(413, "knowledge_limit", "单个文档最多 48000 字节。")
            target.write_bytes(content)
        for document, raw in incoming:
            target = destination / document.source
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        return sorted((set(current.managed_ids) - remove_ids) | incoming_ids)

    def _publish(self, pointer: _Pointer) -> None:
        temporary = self.pointer_path.with_name(f"{pointer.revision}.pending")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(pointer.model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.pointer_path)

    async def apply(self, request: ApplyRequest) -> WorkspaceState:
        self.registry.validate_profile(request.profile)
        if self._lock.locked():
            raise BusinessError(409, "workspace_busy", "另一个知识库更新正在运行。")
        async with self._lock:
            with self._process_writer():
                return await self._apply_locked(request)

    async def _apply_locked(self, request: ApplyRequest) -> WorkspaceState:
        current = self.snapshot()
        if request.revision != current.revision:
            raise BusinessError(409, "workspace_conflict", "知识库版本已变化，请刷新后重试。")
        healthy = current.store.health()["status"] == "ready"
        rebuild = (
            current.generation is None
            or not healthy
            or bool(request.files)
            or bool(request.remove_ids)
            or _index_fields(current.profile) != _index_fields(request.profile)
        )
        if current.profile == request.profile and not request.files and not request.remove_ids and healthy:
            return self.state(current, rebuilt=False)
        revision = uuid4().hex
        generation = current.generation
        managed_ids = list(current.managed_ids)
        if rebuild:
            generation = revision
            generation_settings = self._generation_settings(generation)
            incoming = [_prepare_import(item) for item in request.files]
            managed_ids = self._copy_documents(
                current,
                generation_settings.knowledge_dir,
                incoming,
                set(request.remove_ids),
            )
            documents = load_documents(generation_settings.knowledge_dir, limits=CORPUS_LIMITS)
            for document in documents:
                if any(
                    sensitive_input_reason(value)
                    for value in (document.doc_id, document.title, document.source, document.content)
                ):
                    raise BusinessError(422, "sensitive_document", "知识文档含疑似凭证，请移除后重试。")
            _check_corpus(documents, request.profile)
            store = self.registry.make_store(generation_settings, self.provider, request.profile)
            try:
                async with asyncio.timeout(BUILD_TIMEOUT_SECONDS):
                    await store.build_index()
            except TimeoutError:
                raise ITDeskError("provider_timeout", "知识索引构建超时，请重试。") from None
            if store.health()["status"] != "ready":
                raise ITDeskError("index_invalid", "新索引未通过检查，当前版本仍可用。")
        assert generation is not None
        self._publish(
            _Pointer(
                revision=revision,
                generation=generation,
                profile=request.profile,
                managed_ids=managed_ids,
            )
        )
        return self.state(rebuilt=rebuild)

    async def search(self, query: str) -> SearchResult:
        if reason := sensitive_input_reason(query):
            raise BusinessError(422, "sensitive_input", reason)
        result = await self.retrieve(query)
        return SearchResult(
            hits=[
                WorkspaceHit(
                    chunk_id=hit.chunk_id,
                    doc_id=hit.doc_id,
                    title=hit.title,
                    source=hit.source,
                    version=hit.version,
                    content=hit.content,
                    exact_match=hit.exact_match,
                    similarity=None if hit.distance is None else max(-1, min(1, 1 - hit.distance)),
                    rerank_score=hit.rerank_score,
                )
                for hit in result.chunks
            ],
            embedding_tokens=result.embedding_tokens,
            rerank_status=result.rerank_status,
        )


@contextmanager
def _workspace_errors() -> Iterator[None]:
    try:
        yield
    except ITDeskError as error:
        failures = {
            "knowledge_limit": (413, "文档超出知识库容量。"),
            "invalid_document": (422, "知识文档格式不正确。"),
            "duplicate_doc_id": (422, "存在重复文档编号。"),
            "invalid_embedding_model": (422, "所选 Embedding 模型未在后端配置中启用。"),
            "invalid_rerank_model": (422, "所选重排模型未在后端配置中启用。"),
            "configuration_missing": (503, "请在后端配置 Embedding。"),
            "index_not_ready": (409, "当前索引未就绪，请先保存并构建。"),
            "workspace_invalid": (503, "当前知识库版本文件或配置无效。"),
            "provider_timeout": (504, "Embedding 或索引构建超时，请重试。"),
            "provider_rate_limit": (429, "Embedding 服务限流，请稍后重试。"),
        }
        code = error.code if error.code in failures else "knowledge_update_failed"
        status, message = failures.get(code, (502, "知识库操作失败，请检查索引或模型配置。"))
        raise BusinessError(status, code, message) from None
    except (UnicodeError, ValidationError):
        raise BusinessError(422, "invalid_document", "文档或配置格式不正确。") from None
    except ChromaError:
        raise BusinessError(503, "index_unavailable", "无法读写知识索引，请检查本地索引配置。") from None


def create_workspace_router(workspace: KnowledgeWorkspace) -> APIRouter:
    router = APIRouter(prefix="/api/knowledge/workspace", tags=["knowledge"])

    async def require_json(request: Request) -> None:
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            raise BusinessError(415, "json_required", "知识库操作需要 application/json。")

    @router.get("")
    def state() -> WorkspaceState:
        with _workspace_errors():
            return workspace.state()

    @router.post("/preview", dependencies=[Depends(require_json)])
    def preview(request: PreviewRequest) -> PreviewResult:
        with _workspace_errors():
            return workspace.preview(request)

    @router.post("/apply", dependencies=[Depends(require_json)])
    async def apply(request: ApplyRequest) -> WorkspaceState:
        with _workspace_errors():
            return await workspace.apply(request)

    @router.post("/search", dependencies=[Depends(require_json)])
    async def search(request: SearchRequest) -> SearchResult:
        with _workspace_errors():
            return await workspace.search(request.query)

    return router
