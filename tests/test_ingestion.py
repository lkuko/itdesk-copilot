"""Exercise the seed corpus and lossless, reproducible document ingestion."""

from collections import Counter
from pathlib import Path

import pytest

from app.errors import ITDeskError
from app.retrieval.ingestion import chunk_documents, load_documents

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_document(root: Path, doc_id: str = "vpn-test", *, body: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{doc_id}.md"
    path.write_text(
        "---\n"
        f"doc_id: {doc_id}\n"
        "title: VPN 测试手册\n"
        "category: vpn\n"
        "version: '1.0'\n"
        "error_codes: [VPN-403]\n"
        "---\n\n"
        + (body or "# VPN 故障\n\n虚构演示资料。VPN-403 表示测试设备未注册，请联系 IT 核实。"),
        encoding="utf-8",
    )
    return path


def test_seed_corpus_has_24_distinct_documents_and_eight_per_category():
    documents = load_documents(PROJECT_ROOT / "knowledge")

    assert len(documents) == 24
    assert Counter(document.category for document in documents) == {
        "vpn": 8,
        "email": 8,
        "device": 8,
    }
    assert len({document.doc_id for document in documents}) == 24
    for document in documents:
        assert document.title.strip()
        assert document.version
        assert document.content.strip()
        assert document.source
        for code in document.error_codes:
            assert code in document.content, f"{code} missing from {document.doc_id} body"


def test_parser_rejects_duplicate_document_identifiers(tmp_path):
    write_document(tmp_path / "first")
    write_document(tmp_path / "second")

    with pytest.raises(ITDeskError, match="(?i)(duplicate|重复)"):
        load_documents(tmp_path)


def test_parser_rejects_missing_required_metadata(tmp_path):
    (tmp_path / "invalid.md").write_text(
        "---\ntitle: 没有标识符\n---\n\n此文档缺少必填元数据。", encoding="utf-8"
    )

    with pytest.raises(ITDeskError):
        load_documents(tmp_path)


def test_chunking_is_reproducible_preserves_metadata_and_all_paragraphs(tmp_path):
    paragraphs = [f"独立段落{i:03d}：" + "排障步骤与支持边界。" * 8 for i in range(20)]
    write_document(tmp_path, body="\n\n".join(paragraphs))
    documents = load_documents(tmp_path)

    first = chunk_documents(documents, chunk_size=200, chunk_overlap=40)
    second = chunk_documents(documents, chunk_size=200, chunk_overlap=40)

    assert len(first) > 1
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert len({chunk.chunk_id for chunk in first}) == len(first)
    assert all(chunk.doc_id == "vpn-test" for chunk in first)
    assert all(chunk.title == documents[0].title for chunk in first)
    assert all(chunk.source == documents[0].source for chunk in first)
    assert all(chunk.version == documents[0].version for chunk in first)
    assert all(chunk.content.strip() and len(chunk.content) <= 200 for chunk in first)
    combined = "\n".join(chunk.content for chunk in first)
    assert all(f"独立段落{i:03d}" in combined for i in range(20))


@pytest.mark.parametrize("size,overlap", [(0, 0), (100, -1), (100, 100), (100, 101)])
def test_chunking_rejects_invalid_window_sizes(tmp_path, size, overlap):
    write_document(tmp_path)
    documents = load_documents(tmp_path)

    with pytest.raises(ValueError):
        chunk_documents(documents, chunk_size=size, chunk_overlap=overlap)
