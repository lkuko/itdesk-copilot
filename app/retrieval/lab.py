"""Inspect the demo corpus and compare two chunkers with isolated cosine search."""

import asyncio
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.business import BusinessError
from app.config import Settings
from app.errors import ITDeskError
from app.input_safety import sensitive_input_reason
from app.providers import Provider
from app.retrieval.ingestion import (
    ChunkSpan,
    ChunkStrategy,
    KnowledgeLoadLimits,
    iter_chunk_spans,
    load_documents,
)
from app.retrieval.store import KnowledgeStore
from app.schemas import EmbeddingBatch, KnowledgeDocument

if TYPE_CHECKING:
    from app.retrieval.workspace import KnowledgeWorkspace

CORPUS_LIMITS = KnowledgeLoadLimits(documents=64, document_bytes=48_000, corpus_bytes=256_000)
MAX_CHUNKS_PER_STRATEGY = 128
EMBEDDING_BATCH_SIZE = 8
MAX_EMBEDDING_DIMENSION = 8192
COMPARISON_TIMEOUT_SECONDS = 75
STRATEGIES: tuple[ChunkStrategy, ...] = ("boundary", "fixed")
UnitVector = tuple[float, ...]

_PROVIDER_FAILURES = {
    "configuration_missing": (503, "请在后端配置 Embedding。"),
    "provider_timeout": (504, "Embedding 对照超时，请稍后重试。"),
    "provider_rate_limit": (429, "Embedding 服务限流，请稍后重试。"),
    "invalid_embeddings": (502, "Embedding 服务返回了无效向量。"),
}


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def meaningful_query(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("查询不能为空")
        return text.strip()


class ChunkExcerpt(BaseModel):
    chunk_id: str
    content: str
    start: int
    end: int


class DocumentMetadata(BaseModel):
    doc_id: str
    title: str
    source: str
    version: str


class CatalogDocument(DocumentMetadata):
    chunk_count: int


class DocumentDetail(DocumentMetadata):
    content: str
    chunks: list[ChunkExcerpt]


class IndexStatus(BaseModel):
    status: str
    message: str


class ChunkSettings(BaseModel):
    embedding_model: str
    chunk_size: int
    chunk_overlap: int


class Catalog(ChunkSettings):
    index: IndexStatus
    documents: list[CatalogDocument]


class SearchHit(ChunkExcerpt, DocumentMetadata):
    similarity: float


class StrategyResult(BaseModel):
    strategy: ChunkStrategy
    chunk_count: int
    embedding_tokens: int | None = Field(default=None, description="Shared usage is reported on Comparison.")
    embedding_characters: int
    hits: list[SearchHit]


class Comparison(ChunkSettings):
    query: str
    top_k: int
    unique_chunk_inputs: int
    document_embedding_tokens: int | None
    query_embedding_tokens: int | None
    strategies: list[StrategyResult]


@dataclass(frozen=True)
class PreparedComparison:
    partitions: dict[ChunkStrategy, list[ChunkSpan]]
    vectors: dict[str, UnitVector]
    dimension: int
    document_embedding_tokens: int | None


def _excerpt(span: ChunkSpan) -> ChunkExcerpt:
    return ChunkExcerpt(
        chunk_id=span.chunk.chunk_id, content=span.chunk.content, start=span.start, end=span.end
    )


def _embedding_input(span: ChunkSpan) -> str:
    return f"{span.chunk.title}\n{span.chunk.content}"


def _unit_vectors(batch: EmbeddingBatch, count: int, dimension: int | None = None) -> list[UnitVector]:
    """Check every row before its dot product contributes to the result."""
    vectors: list[UnitVector] = []
    failure = BusinessError(502, "invalid_embeddings", "Embedding 服务返回了无效向量，请检查模型。")
    if (
        not isinstance(batch, EmbeddingBatch)
        or not isinstance(batch.vectors, list)
        or len(batch.vectors) != count
    ):
        raise failure
    for row in batch.vectors:
        if not isinstance(row, list) or not 1 <= len(row) <= MAX_EMBEDDING_DIMENSION:
            raise failure
        if dimension is None:
            dimension = len(row)
        if len(row) != dimension or any(type(value) not in (int, float) for value in row):
            raise failure
        try:
            scale = max(abs(value) for value in row)
            if not math.isfinite(scale) or scale == 0:
                raise failure
            scaled = tuple(value / scale for value in row)
            length = math.hypot(*scaled)
            if not math.isfinite(length) or length == 0:
                raise failure
        except OverflowError:
            raise failure from None
        vectors.append(tuple(value / length for value in scaled))
    return vectors


def _reported_tokens(batch: EmbeddingBatch) -> int | None:
    return batch.tokens if type(batch.tokens) is int and 0 <= batch.tokens <= 2**53 - 1 else None


def _cosine(vector: UnitVector, query: UnitVector) -> float:
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(vector, query, strict=True))))


@asynccontextmanager
async def _embedding_request(timeout: float | None = None):
    try:
        async with asyncio.timeout(COMPARISON_TIMEOUT_SECONDS if timeout is None else timeout):
            yield
    except TimeoutError:
        raise BusinessError(504, "provider_timeout", _PROVIDER_FAILURES["provider_timeout"][1]) from None
    except ITDeskError as error:
        if error.code in _PROVIDER_FAILURES:
            status, message = _PROVIDER_FAILURES[error.code]
            raise BusinessError(status, error.code, message) from None
        raise BusinessError(502, "provider_error", "Embedding 调用失败，请检查服务与配置。") from None
    except (AttributeError, ValueError, TypeError, OverflowError):
        # A compatible upstream can return malformed data before vector validation.
        raise BusinessError(502, "invalid_embeddings", _PROVIDER_FAILURES["invalid_embeddings"][1]) from None


class KnowledgeLab:
    def __init__(
        self,
        settings: Settings,
        provider: Provider,
        store: KnowledgeStore,
        *,
        active_strategy: ChunkStrategy = "boundary",
        comparison_lock: asyncio.Lock | None = None,
    ):
        self.settings, self.provider, self.store = settings, provider, store
        self.active_strategy = active_strategy
        self._comparison_lock = comparison_lock if comparison_lock is not None else asyncio.Lock()

    def _configuration(self) -> dict:
        return {
            "embedding_model": self.settings.embedding_model,
            "chunk_size": self.settings.chunk_size,
            "chunk_overlap": self.settings.chunk_overlap,
        }

    def _documents(self) -> list[KnowledgeDocument]:
        try:
            documents = load_documents(self.settings.knowledge_dir, limits=CORPUS_LIMITS)
        except ITDeskError as error:
            if error.code == "knowledge_limit":
                raise BusinessError(413, "knowledge_limit", error.message) from None
            if error.code == "knowledge_empty":
                raise BusinessError(409, "knowledge_empty", "知识目录中没有可预览的文档。") from None
            raise BusinessError(422, "invalid_document", "请检查知识文档格式或重复编号。") from None
        except UnicodeError:
            raise BusinessError(422, "invalid_document", "知识文档必须使用 UTF-8 编码。") from None
        except (OSError, RuntimeError):
            raise BusinessError(503, "knowledge_unavailable", "无法读取本地知识目录。") from None
        for document in documents:
            values = (document.doc_id, document.title, document.source, document.version, document.content)
            if any(sensitive_input_reason(value) for value in values):
                raise BusinessError(422, "sensitive_document", "知识文档含疑似凭证，请移除后重试。")
            identifier = document.doc_id
            if (
                not identifier.strip()
                or identifier in {".", ".."}
                or len(identifier) > 128
                or any(char in identifier for char in "/\\")
                or any(ord(char) < 32 or ord(char) == 127 for char in identifier)
            ):
                raise BusinessError(422, "invalid_document", "知识文档编号无法用于文档预览。")
        return documents

    def _spans(
        self, documents: list[KnowledgeDocument], strategy: ChunkStrategy = "boundary"
    ) -> list[ChunkSpan]:
        spans = []
        for span in iter_chunk_spans(
            documents, self.settings.chunk_size, self.settings.chunk_overlap, strategy
        ):
            if len(spans) >= MAX_CHUNKS_PER_STRATEGY:
                raise BusinessError(413, "chunk_limit", "当前切片方案超出实验支持的片段数。")
            spans.append(span)
        return spans

    def catalog(self) -> Catalog:
        documents = self._documents()
        spans = self._spans(documents, self.active_strategy)
        counts = dict.fromkeys((document.doc_id for document in documents), 0)
        for span in spans:
            counts[span.chunk.doc_id] += 1
        return Catalog(
            **self._configuration(),
            index=IndexStatus.model_validate(self.store.health()),
            documents=[
                CatalogDocument(
                    **document.model_dump(exclude={"content"}), chunk_count=counts[document.doc_id]
                )
                for document in documents
            ],
        )

    def document(self, doc_id: str) -> DocumentDetail:
        document = next((item for item in self._documents() if item.doc_id == doc_id), None)
        if document is None:
            raise BusinessError(404, "document_not_found", "找不到该知识文档。")
        return DocumentDetail(
            **document.model_dump(),
            chunks=[_excerpt(span) for span in self._spans([document], self.active_strategy)],
        )

    async def prepare_comparison(self, *, timeout: float | None = None) -> PreparedComparison:
        """Read one corpus snapshot and reuse its document embeddings in a batch experiment."""
        documents = await asyncio.to_thread(self._documents)
        partitions = {strategy: self._spans(documents, strategy) for strategy in STRATEGIES}
        inputs = list(
            dict.fromkeys(_embedding_input(span) for spans in partitions.values() for span in spans)
        )
        document_tokens: int | None = 0
        dimension = None
        vectors: dict[str, UnitVector] = {}
        async with _embedding_request(timeout):
            for offset in range(0, len(inputs), EMBEDDING_BATCH_SIZE):
                batch = inputs[offset : offset + EMBEDDING_BATCH_SIZE]
                response = await self.provider.embed_documents(batch)
                normalized = _unit_vectors(response, len(batch), dimension)
                dimension = len(normalized[0])
                vectors.update(zip(batch, normalized, strict=True))
                tokens = _reported_tokens(response)
                if document_tokens is not None:
                    document_tokens = document_tokens + tokens if tokens is not None else None
        assert dimension is not None  # a valid corpus always yields at least one non-empty chunk
        return PreparedComparison(partitions, vectors, dimension, document_tokens)

    def _rank(
        self, strategy: ChunkStrategy, spans: list[ChunkSpan], scores: dict[str, float], top_k: int
    ) -> StrategyResult:
        ranked = sorted(spans, key=lambda span: (-scores[_embedding_input(span)], span.chunk.chunk_id))
        return StrategyResult(
            strategy=strategy,
            chunk_count=len(spans),
            embedding_characters=sum(len(_embedding_input(span)) for span in spans),
            hits=[
                SearchHit(
                    **_excerpt(span).model_dump(),
                    doc_id=span.chunk.doc_id,
                    title=span.chunk.title,
                    source=span.chunk.source,
                    version=span.chunk.version,
                    similarity=scores[_embedding_input(span)],
                )
                for span in ranked[:top_k]
            ],
        )

    async def compare(
        self, request: CompareRequest, *, prepared: PreparedComparison | None = None
    ) -> Comparison:
        if reason := sensitive_input_reason(request.query):
            raise BusinessError(422, "sensitive_input", reason)
        if self._comparison_lock.locked():
            raise BusinessError(429, "knowledge_compare_busy", "已有对照实验正在运行，请稍后再试。")
        async with self._comparison_lock:
            async with _embedding_request():
                if prepared is None:
                    prepared = await self.prepare_comparison()
                query_batch = await self.provider.embed_query(request.query)
                (query,) = _unit_vectors(query_batch, 1, prepared.dimension)
            scores = {text: _cosine(vector, query) for text, vector in prepared.vectors.items()}
            return Comparison(
                **self._configuration(),
                query=request.query,
                top_k=request.top_k,
                unique_chunk_inputs=len(prepared.vectors),
                document_embedding_tokens=prepared.document_embedding_tokens,
                query_embedding_tokens=_reported_tokens(query_batch),
                strategies=[
                    self._rank(strategy, spans, scores, request.top_k)
                    for strategy, spans in prepared.partitions.items()
                ],
            )


def create_knowledge_router(
    settings: Settings,
    provider: Provider,
    store: KnowledgeStore,
    *,
    workspace: "KnowledgeWorkspace | None" = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
    legacy_lab = KnowledgeLab(settings, provider, store)
    comparison_lock = asyncio.Lock()

    def current_lab() -> KnowledgeLab:
        if workspace is None:
            return legacy_lab
        try:
            current = workspace.snapshot()
        except ITDeskError:
            raise BusinessError(503, "workspace_invalid", "当前知识库版本文件或配置无效。") from None
        return KnowledgeLab(
            current.settings,
            current.provider,
            current.store,
            active_strategy=current.profile.chunk_strategy,
            comparison_lock=comparison_lock,
        )

    @router.get("")
    def catalog() -> Catalog:
        return current_lab().catalog()

    @router.get("/documents/{doc_id}")
    def document(doc_id: Annotated[str, Path(min_length=1, max_length=128)]) -> DocumentDetail:
        return current_lab().document(doc_id)

    async def require_json(request: Request) -> None:
        # Require JSON media type for a paid browser action, so it needs a CORS preflight across origins.
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            raise BusinessError(415, "json_required", "对照实验请求需要 application/json。")

    @router.post("/compare", dependencies=[Depends(require_json)])
    async def compare(request: CompareRequest) -> Comparison:
        return await current_lab().compare(request)

    return router
