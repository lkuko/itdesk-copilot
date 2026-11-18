"""Server-approved models and a persisted profile for the knowledge workspace."""

import asyncio
import math
import re
from functools import partial
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.errors import ITDeskError
from app.input_safety import sensitive_input_reason
from app.providers import Provider
from app.schemas import EmbeddingBatch

if TYPE_CHECKING:
    from app.retrieval.store import KnowledgeStore

LOCAL_RERANKER = "local-lexical"
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}")
LEXICAL_TERMS = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*|[\u3400-\u9fff]+")


class RetrievalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    embedding_model: str = Field(max_length=160)
    chunk_strategy: Literal["boundary", "fixed"] = "boundary"
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=80, ge=0, le=99)
    search_mode: Literal["exact_vector", "vector"] = "exact_vector"
    top_k: int = Field(default=5, ge=1, le=10)
    score_threshold: float = Field(default=0.5, ge=0, le=1)
    rerank_model: str | None = Field(default=None, min_length=1, max_length=160)
    rerank_top_n: int = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def check_related_fields(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("切片重叠必须小于切片长度。")
        if self.rerank_model is not None and self.rerank_top_n < self.top_k:
            raise ValueError("重排候选数不得少于召回数量。")
        return self


class _SelectedEmbedding:
    def __init__(self, provider: Provider, model: str):
        self.provider = provider
        self.model = model

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        method = getattr(self.provider, "embed_documents_for_model", None)
        if not callable(method):
            raise ITDeskError("embedding_model_unavailable", "当前 Embedding 提供方无法使用所选模型。")
        return await method(texts, self.model)

    async def embed_query(self, text: str) -> EmbeddingBatch:
        method = getattr(self.provider, "embed_query_for_model", None)
        if not callable(method):
            raise ITDeskError("embedding_model_unavailable", "当前 Embedding 提供方无法使用所选模型。")
        return await method(text, self.model)


def _allowed_names(names: list[str]) -> list[str]:
    return list(dict.fromkeys(
        name for name in (item.strip() for item in names)
        if MODEL_NAME.fullmatch(name) and "://" not in name and not sensitive_input_reason(name)
    ))


def _rerank_endpoint(base: str) -> str | None:
    try:
        url = urlsplit(base)
        url.port  # Check that the supplied port is valid.
    except ValueError:
        return None
    if (
        url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
        or url.password is not None or url.query or url.fragment or any(char.isspace() for char in base)
    ):
        return None
    return base.rstrip("/") + "/rerank"


def _lexical_terms(text: str) -> set[str]:
    terms = set()
    for term in LEXICAL_TERMS.findall(text.casefold()):
        if term[0].isascii():
            terms.add(term)
        else:
            terms.update(term[index : index + 2] for index in range(max(1, len(term) - 1)))
    return terms


def _remote_scores(response: object, count: int) -> list[float]:
    rows = response.get("results") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("Incomplete rerank results")
    scores: dict[int, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid rerank entry")
        index, score = row.get("index"), row.get("relevance_score")
        if (
            type(index) is not int or not 0 <= index < count or index in scores
            or type(score) not in {int, float} or not math.isfinite(score)
        ):
            raise ValueError("Invalid rerank entry")
        scores[index] = float(score)
    return [scores[index] for index in range(count)]


class ModelRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.embedding_models = _allowed_names([settings.embedding_model, *settings.embedding_models])
        if settings.rerank_use_embedding_provider:
            self._rerank_url = _rerank_endpoint(settings.embedding_base_url)
            self._rerank_key = settings.embedding_api_key
        else:
            self._rerank_url = _rerank_endpoint(settings.rerank_base_url)
            self._rerank_key = settings.rerank_api_key
        self.rerank_models = [LOCAL_RERANKER]
        if self._rerank_url and self._rerank_key.get_secret_value().strip():
            self.rerank_models.extend(
                model for model in _allowed_names(settings.rerank_models) if model != LOCAL_RERANKER
            )

    def default_profile(self) -> RetrievalProfile:
        default_name = self.settings.embedding_model.strip()
        return RetrievalProfile(
            embedding_model=default_name if default_name in self.embedding_models else "",
            chunk_size=self.settings.chunk_size,
            chunk_overlap=self.settings.chunk_overlap,
            top_k=self.settings.top_k,
            score_threshold=max(0.0, 1.0 - self.settings.max_cosine_distance),
        )

    def validate_profile(self, profile: RetrievalProfile) -> None:
        if profile.embedding_model not in self.embedding_models:
            raise ITDeskError("invalid_embedding_model", "所选 Embedding 模型未在后端配置中启用。")
        if profile.rerank_model is not None and profile.rerank_model not in self.rerank_models:
            raise ITDeskError("invalid_rerank_model", "所选重排模型未在后端配置中启用。")

    def make_store(self, settings: Settings, provider: Provider, profile: RetrievalProfile):
        from app.retrieval.store import KnowledgeStore

        self.validate_profile(profile)
        embedding = provider
        if profile.embedding_model != self.settings.embedding_model.strip():
            embedding = _SelectedEmbedding(provider, profile.embedding_model)
        ranker = partial(self.rerank, profile.rerank_model) if profile.rerank_model else None
        return KnowledgeStore(settings, embedding, profile=profile, ranker=ranker)

    async def rerank(self, model: str, query: str, documents: list[str]) -> list[float]:
        if model not in self.rerank_models:
            raise ITDeskError("invalid_rerank_model", "所选重排模型未在后端配置中启用。")
        if not documents:
            return []
        if model == LOCAL_RERANKER:
            query_terms = _lexical_terms(query)
            return [len(query_terms & _lexical_terms(text)) / max(1, len(query_terms)) for text in documents]

        timeout = min(self.settings.request_timeout_s, 10.0)
        try:
            async with asyncio.timeout(timeout), httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self._rerank_url,
                    headers={"Authorization": "Bearer " + self._rerank_key.get_secret_value()},
                    json={"model": model, "query": query, "documents": documents, "top_n": len(documents)},
                )
                response.raise_for_status()
                return _remote_scores(response.json(), len(documents))
        except (httpx.HTTPError, TimeoutError, ValueError, OverflowError):
            raise ITDeskError("rerank_failed", "重排服务不可用，已保留初始召回顺序。") from None


def make_store(settings: Settings, provider: Provider, profile: RetrievalProfile) -> "KnowledgeStore":
    return ModelRegistry(settings).make_store(settings, provider, profile)
