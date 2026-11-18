"""Deterministic Markdown/TXT loading and Chinese-friendly character windows."""

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from app.errors import ITDeskError
from app.schemas import KnowledgeChunk, KnowledgeDocument

CODE_PATTERN = re.compile(r"(?<![A-Z0-9_-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+[A-Z0-9](?![A-Z0-9_-])", re.I)
ChunkStrategy = Literal["boundary", "fixed"]


@dataclass(frozen=True)
class KnowledgeLoadLimits:
    documents: int
    document_bytes: int
    corpus_bytes: int


@dataclass(frozen=True)
class ChunkSpan:
    chunk: KnowledgeChunk
    start: int
    end: int


def extract_error_codes(text: str) -> set[str]:
    return {match.group().upper() for match in CODE_PATTERN.finditer(text)}


def load_documents(path: Path, *, limits: KnowledgeLoadLimits | None = None) -> list[KnowledgeDocument]:
    documents, seen = [], set()
    size = 0
    root = path.resolve() if limits else path
    for file in sorted(path.rglob("*")):
        if file.suffix.lower() not in (".md", ".txt") or not file.is_file():
            continue
        if limits:
            if len(documents) >= limits.documents:
                raise ITDeskError("knowledge_limit", "实验支持的文档数量已达上限。")
            resolved = file.resolve()
            if not resolved.is_relative_to(root):
                raise ITDeskError("invalid_document", "知识目录中存在指向外部的文档链接。")
            maximum = min(limits.document_bytes, limits.corpus_bytes - size)
            with resolved.open("rb") as handle:
                raw = handle.read(maximum + 1)
            if len(raw) > maximum:
                raise ITDeskError("knowledge_limit", "实验的单文件或知识库总容量已达上限。")
            size += len(raw)
            text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        else:
            text = file.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        parts = text.split("---", 2) if text.startswith("---\n") else []
        if len(parts) != 3:
            raise ITDeskError("invalid_document", f"文档缺少 YAML 元数据：{file.name}")
        try:
            metadata = yaml.safe_load(parts[1])
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be a mapping")
            content = parts[2].strip()
            if not content:
                raise ValueError("empty body")
            document = KnowledgeDocument(
                **metadata, source=file.relative_to(path).as_posix(), content=content
            )
        except (ValueError, TypeError, yaml.YAMLError):
            raise ITDeskError("invalid_document", f"文档元数据或正文无效：{file.name}") from None
        if document.doc_id in seen:
            raise ITDeskError("duplicate_doc_id", f"重复文档 ID：{document.doc_id}")
        seen.add(document.doc_id)
        documents.append(document)
    if not documents:
        raise ITDeskError("knowledge_empty", "知识目录中没有有效的 Markdown/TXT 文档。")
    return documents


def chunk_documents(
    documents: list[KnowledgeDocument], chunk_size: int = 500, chunk_overlap: int = 80
) -> list[KnowledgeChunk]:
    return [span.chunk for span in iter_chunk_spans(documents, chunk_size, chunk_overlap)]


def iter_chunk_spans(
    documents: list[KnowledgeDocument],
    chunk_size: int,
    chunk_overlap: int,
    strategy: ChunkStrategy = "boundary",
) -> Iterator[ChunkSpan]:
    """Yield chunks and their half-open Python character offsets in the normalized body."""
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    if strategy not in {"boundary", "fixed"}:
        raise ValueError("Unsupported chunking strategy")
    for document in documents:
        start, number = 0, 0
        while start < len(document.content):
            end = min(start + chunk_size, len(document.content))
            # Prefer paragraph/sentence boundaries while keeping useful minimum context.
            if strategy == "boundary" and end < len(document.content):
                tail = document.content[start + chunk_size // 2 : end]
                matches = list(re.finditer(r"\n\n|[。！？]\n?", tail))
                if matches:
                    end = start + chunk_size // 2 + matches[-1].end()
            content = document.content[start:end]
            digest = hashlib.sha256(content.encode()).hexdigest()[:12]
            yield ChunkSpan(
                KnowledgeChunk(
                    **{**document.model_dump(), "content": content},
                    chunk_id=f"{document.doc_id}:{number:03d}:{digest}",
                ),
                start,
                end,
            )
            if end == len(document.content):
                break
            start, number = max(start + 1, end - chunk_overlap), number + 1
