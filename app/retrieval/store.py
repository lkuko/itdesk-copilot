import asyncio
import hashlib
import json
import math
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import Settings
from app.errors import ITDeskError
from app.providers import Provider
from app.retrieval.ingestion import extract_error_codes, iter_chunk_spans, load_documents
from app.schemas import EmbeddingBatch, IndexSummary, KnowledgeChunk, RetrievalResult, RetrievedChunk

if TYPE_CHECKING:
    from app.retrieval.profile import RetrievalProfile

Reranker = Callable[[str, list[str]], Awaitable[list[float]]]


class KnowledgeStore:
    def __init__(
        self, settings: Settings, provider: Provider, *,
        profile: "RetrievalProfile | None" = None, ranker: Reranker | None = None,
    ):
        self.settings, self.provider = settings, provider
        self.profile, self.ranker = profile, ranker
        self.manifest_path = settings.data_dir / "index.json"
        self._client = None

    def _current(self):
        documents = load_documents(self.settings.knowledge_dir)
        chunk_size = self.profile.chunk_size if self.profile else self.settings.chunk_size
        overlap = self.profile.chunk_overlap if self.profile else self.settings.chunk_overlap
        strategy = self.profile.chunk_strategy if self.profile else "boundary"
        embedding_model = self.profile.embedding_model if self.profile else self.settings.embedding_model
        chunks = [span.chunk for span in iter_chunk_spans(documents, chunk_size, overlap, strategy)]
        value = {
            "schema": 1,
            "documents": [d.model_dump() for d in documents],
            "embedding_model": embedding_model,
            "embedding_base_url": self.settings.embedding_base_url.rstrip("/"),
            "chunk_size": chunk_size,
            "chunk_overlap": overlap,
        }
        if strategy != "boundary":
            value["chunk_strategy"] = strategy
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return documents, chunks, digest

    def _manifest(self):
        if not self.manifest_path.exists():
            return None
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ITDeskError("index_invalid", "索引清单损坏，请显式重建索引。") from None

    def _db(self):
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=str(self.settings.chroma_dir), settings=ChromaSettings(anonymized_telemetry=False)
            )
        return self._client

    def health(self) -> dict:
        try:
            documents, chunks, fingerprint = self._current()
            manifest = self._manifest()
            if manifest is None:
                return {"status": "empty", "message": "尚未建立索引，请运行 python -m app.cli index。"}
            summary = {"document_count": len(documents), "chunk_count": len(chunks)}
            if manifest.get("fingerprint") != fingerprint:
                return {
                    "status": "stale",
                    "message": "知识文档或 Embedding 配置已变化，请重建索引。",
                    **summary,
                }
            collection = self._db().get_collection(manifest["collection"], embedding_function=None)
            if collection.count() != len(chunks):
                return {"status": "stale", "message": "索引条目不完整，请重建索引。", **summary}
            return {"status": "ready", "message": "索引可用。", **summary}
        except Exception:
            return {"status": "invalid", "message": "索引或知识文档不可读，请检查后重建。"}

    @staticmethod
    def _check_vectors(vectors: list[list[float]], count: int):
        if (
            len(vectors) != count
            or not vectors
            or not vectors[0]
            or any(
                len(v) != len(vectors[0])
                or any(not math.isfinite(x) for x in v)
                or sum(x * x for x in v) == 0
                for v in vectors
            )
        ):
            raise ITDeskError("invalid_embeddings", "Embedding 返回了无效、零值或维度不一致的向量。")

    async def build_index(self, rebuild: bool = False) -> IndexSummary:
        documents, chunks, fingerprint = self._current()
        existing = self._manifest()
        if existing and not rebuild:
            if self.health()["status"] != "ready":
                raise ITDeskError("index_stale", "索引配置或文档已变化，请使用 index --rebuild。")
            return IndexSummary.model_validate(existing["summary"])
        embeddings = await self.provider.embed_documents([f"{c.title}\n{c.content}" for c in chunks])
        self._check_vectors(embeddings.vectors, len(chunks))
        # Stage a new collection. A failed embedding/upsert never replaces the active manifest.
        name = f"knowledge-{uuid.uuid4().hex}"
        collection = self._db().create_collection(
            name, embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
        try:
            for offset in range(0, len(chunks), 100):
                batch = chunks[offset : offset + 100]
                collection.add(
                    ids=[c.chunk_id for c in batch],
                    documents=[c.content for c in batch],
                    embeddings=embeddings.vectors[offset : offset + 100],
                    metadatas=[{"chunk": c.model_dump_json()} for c in batch],
                )
            summary = IndexSummary(
                document_count=len(documents),
                chunk_count=len(chunks),
                fingerprint=fingerprint,
                embedding_model=(
                    self.profile.embedding_model if self.profile else self.settings.embedding_model
                ),
                embedding_tokens=embeddings.tokens,
            )
            manifest = {
                "collection": name,
                "fingerprint": fingerprint,
                "dimension": len(embeddings.vectors[0]),
                "summary": summary.model_dump(),
            }
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            pending = self.manifest_path.with_suffix(".pending")
            pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            pending.replace(self.manifest_path)
        except Exception:
            self._db().delete_collection(name)
            raise
        if existing and existing.get("collection") != name:
            try:
                self._db().delete_collection(existing["collection"])
            except Exception:
                pass  # an orphaned old collection is safe; the manifest points only to the verified new one
        return summary

    async def retrieve(
        self,
        question: str,
        top_k: int | None = None,
        *,
        strategy: Literal["vector", "exact_vector"] | None = None,
        query_embedding: EmbeddingBatch | None = None,
    ) -> RetrievalResult:
        if top_k is None:
            top_k = self.profile.top_k if self.profile else 5
        if strategy is None:
            strategy = self.profile.search_mode if self.profile else "exact_vector"
        if strategy not in ("vector", "exact_vector"):
            raise ValueError("Unsupported retrieval strategy")
        if not 1 <= top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        if self.health()["status"] != "ready":
            raise ITDeskError("index_not_ready", "索引未就绪或已过期，请先建立或重建索引。")
        _, chunks, _ = self._current()
        manifest = self._manifest()
        codes = extract_error_codes(question) if strategy == "exact_vector" else set()
        # Exact matches require both metadata and the full identifier in the actual chunk body.
        exact = [
            c
            for c in chunks
            if codes & {code.upper() for code in c.error_codes} & extract_error_codes(c.content)
        ]
        result = query_embedding if query_embedding is not None else await self.provider.embed_query(question)
        self._check_vectors(result.vectors, 1)
        if len(result.vectors[0]) != manifest["dimension"]:
            raise ITDeskError("embedding_dimension_changed", "Embedding 维度已变化，请确认模型配置后重建。")
        collection = self._db().get_collection(manifest["collection"], embedding_function=None)
        use_rerank = self.profile is not None and self.profile.rerank_model is not None
        candidate_count = max(top_k, self.profile.rerank_top_n) if use_rerank else top_k
        found = collection.query(query_embeddings=result.vectors, n_results=min(candidate_count, len(chunks)))
        hits: list[RetrievedChunk] = []
        for chunk in exact:
            hits.append(RetrievedChunk(**chunk.model_dump(), exact_match=True))
        seen = {h.chunk_id for h in hits}
        # The next chunk often contains verification/escalation steps without repeating the error code.
        # Keep its own ID and do not label it as an exact body match.
        exact_positions = {c.chunk_id: position for position, c in enumerate(chunks) if c.chunk_id in seen}
        for chunk in exact:
            position = exact_positions[chunk.chunk_id]
            for neighbor in chunks[max(0, position - 1) : position + 2]:
                if neighbor.doc_id == chunk.doc_id and neighbor.chunk_id not in seen:
                    hits.append(RetrievedChunk(**neighbor.model_dump()))
                    seen.add(neighbor.chunk_id)
        threshold = 1.0 - self.profile.score_threshold if self.profile else self.settings.max_cosine_distance
        candidates = []
        for metadata, distance in zip(found["metadatas"][0], found["distances"][0], strict=True):
            chunk = KnowledgeChunk.model_validate_json(metadata["chunk"])
            if distance <= threshold and chunk.chunk_id not in seen:
                candidates.append(RetrievedChunk(**chunk.model_dump(), distance=distance))
                seen.add(chunk.chunk_id)
        rerank_status = "disabled"
        if use_rerank:
            if not candidates or len(hits) >= top_k:
                rerank_status = "no_candidates"
            else:
                candidates, rerank_status = await self._rerank(question, candidates)
        return RetrievalResult((hits + candidates)[:top_k], result.tokens, rerank_status)

    async def _rerank(
        self, question: str, candidates: list[RetrievedChunk],
    ) -> tuple[list[RetrievedChunk], Literal["applied", "fallback"]]:
        if self.ranker is None:
            return candidates, "fallback"
        try:
            async with asyncio.timeout(min(self.settings.request_timeout_s, 10.0)):
                scores = await self.ranker(question, [f"{c.title}\n{c.content}" for c in candidates])
            if len(scores) != len(candidates) or any(
                type(score) not in {int, float} or not math.isfinite(score) for score in scores
            ):
                return candidates, "fallback"
        except Exception:
            return candidates, "fallback"
        ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: pair[1], reverse=True)
        return [chunk.model_copy(update={"rerank_score": float(score)}) for chunk, score in ranked], "applied"
