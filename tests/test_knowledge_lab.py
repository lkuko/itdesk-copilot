"""Verify the lab through the API and the reusable evaluation path with local embeddings."""

import asyncio
import math
from itertools import chain

import pytest
from fastapi.testclient import TestClient

from app.business import BusinessError
from app.errors import ITDeskError
from app.main import create_app
from app.retrieval import lab as lab_module
from app.retrieval.lab import CompareRequest, KnowledgeLab
from app.retrieval.store import KnowledgeStore
from app.schemas import EmbeddingBatch
from tests.conftest import write_fixture_document

ROOT = "/api/knowledge"


@pytest.fixture
def client(settings, provider):
    with TestClient(create_app(settings, provider)) as client:
        yield client


def test_catalog_and_preview_use_the_current_normalized_files_without_embedding(client, settings, provider):
    write_fixture_document(
        settings.knowledge_dir,
        doc_id="dev-preview",
        category="device",
        code="DEV-201",
        body="😀 文段。\n\n" * 100,
    )
    catalog = client.get(ROOT)
    assert catalog.status_code == 200
    assert catalog.json()["index"]["status"] == "empty"
    assert catalog.json()["embedding_model"] == settings.embedding_model
    metadata = next(item for item in catalog.json()["documents"] if item["doc_id"] == "dev-preview")
    assert "content" not in metadata
    detail = client.get(f"{ROOT}/documents/dev-preview").json()
    assert metadata["chunk_count"] == len(detail["chunks"]) > 1
    assert detail["chunks"][0]["start"] == 0
    assert detail["chunks"][-1]["end"] == len(detail["content"])
    for chunk in detail["chunks"]:
        assert chunk["content"] == detail["content"][chunk["start"] : chunk["end"]]
    for left, right in zip(detail["chunks"], detail["chunks"][1:]):
        assert left["end"] - right["start"] == settings.chunk_overlap
    assert client.get(f"{ROOT}/documents/missing").status_code == 404
    assert not provider.queries and not provider.document_batches


def test_compare_shares_embeddings_preserves_source_and_scores_each_strategy(client, settings, provider):
    write_fixture_document(
        settings.knowledge_dir,
        doc_id="vpn-long",
        category="vpn",
        code="VPN-403",
        body=("补充步骤。 " * 60 + "\n\n" + "VPN-403 特殊条件。 " * 40),
    )
    # A sentinel deliberately unusable by KnowledgeStore proves compare does not open the active index.
    manifest = settings.data_dir / "index.json"
    manifest.write_bytes(b"do not read or replace this index")
    response = client.post(f"{ROOT}/compare", json={"query": " VPN-403 ", "top_k": 10})
    assert response.status_code == 200
    comparison = response.json()
    assert comparison["query"] == "VPN-403"
    assert provider.queries == ["VPN-403"] and not provider.generation_calls
    texts = list(chain.from_iterable(provider.document_batches))
    assert len(set(texts)) == len(texts) == comparison["unique_chunk_inputs"]
    assert all(len(batch) <= 8 for batch in provider.document_batches)
    assert comparison["document_embedding_tokens"] == 8 * len(texts)
    assert comparison["query_embedding_tokens"] == 8
    assert manifest.read_bytes() == b"do not read or replace this index"
    assert not settings.chroma_dir.exists()

    indexed_inputs = []
    strategies = comparison["strategies"]
    assert [strategy["strategy"] for strategy in strategies] == ["boundary", "fixed"]
    for strategy in strategies:
        assert strategy["embedding_tokens"] is None
        assert strategy["chunk_count"] == len(strategy["hits"])
        strategy_inputs = [f"{hit['title']}\n{hit['content']}" for hit in strategy["hits"]]
        assert strategy["embedding_characters"] == sum(len(text) for text in strategy_inputs)
        indexed_inputs.extend(strategy_inputs)
        assert (
            sorted(strategy["hits"], key=lambda hit: (-hit["similarity"], hit["chunk_id"]))
            == strategy["hits"]
        )
        for hit, text in zip(strategy["hits"], strategy_inputs, strict=True):
            document = client.get(f"{ROOT}/documents/{hit['doc_id']}").json()
            assert hit["version"] == document["version"]
            assert hit["content"] == document["content"][hit["start"] : hit["end"]]
            assert hit["similarity"] == provider.vector(text)[0]
    assert set(indexed_inputs) == set(texts) and len(indexed_inputs) > len(texts)
    assert {hit["chunk_id"] for hit in strategies[0]["hits"]} != {
        hit["chunk_id"] for hit in strategies[1]["hits"]
    }


async def test_reused_snapshot_keeps_document_embedding_cost_out_of_later_queries(settings, provider):
    lab = KnowledgeLab(settings, provider, KnowledgeStore(settings, provider))
    prepared = await lab.prepare_comparison()
    doc_batches = provider.document_batches.copy()
    first = await lab.compare(CompareRequest(query="VPN", top_k=1), prepared=prepared)
    second = await lab.compare(CompareRequest(query="MAIL", top_k=1), prepared=prepared)
    assert provider.document_batches == doc_batches
    assert provider.queries == ["VPN", "MAIL"]
    assert first.strategies[0].hits[0].doc_id.startswith("vpn")
    assert second.strategies[0].hits[0].doc_id == "mail-login"


def test_cosine_is_normalized_and_keeps_negative_similarity(client, provider):
    provider.vector = lambda text: [3, 4] if text == "query" else [-30, -40]
    response = client.post(f"{ROOT}/compare", json={"query": "query", "top_k": 1})
    assert response.status_code == 200
    for strategy in response.json()["strategies"]:
        assert strategy["hits"][0]["similarity"] == pytest.approx(-1.0)


@pytest.mark.parametrize("row", [[0, 0], [math.nan, 1], [1, math.inf], [1, "bad"], [True, 1], []])
def test_invalid_document_vectors_return_a_safe_error(client, provider, row):
    async def embed_documents(texts):
        return EmbeddingBatch([row] * len(texts))

    provider.embed_documents = embed_documents
    response = client.post(f"{ROOT}/compare", json={"query": "VPN-403"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_embeddings"


async def test_mismatched_query_dimension_cannot_be_sorted_against_documents(settings, provider):
    lab = KnowledgeLab(settings, provider, KnowledgeStore(settings, provider))
    prepared = await lab.prepare_comparison()
    provider.vector = lambda _text: [1.0, 2.0]
    with pytest.raises(BusinessError) as failure:
        await lab.compare(CompareRequest(query="VPN"), prepared=prepared)
    assert failure.value.code == "invalid_embeddings"


def test_validation_and_recognized_secrets_are_rejected_before_model_calls(client, provider):
    key = "sk-dont-display-this"
    rejected_bodies = [
        {"query": "   "},
        {"query": "VPN", "top_k": "5"},
        {"query": "VPN", "top_k": 11},
        {"query": key, "top_k": 0},
        {"query": key},
        {"query": "VPN", "extra": True},
    ]
    for body in rejected_bodies:
        response = client.post(f"{ROOT}/compare", json=body)
        assert response.status_code == 422 and key not in response.text
    assert not provider.queries and not provider.document_batches


def test_paid_comparison_requires_json_media_type(client, provider):
    response = client.post(f"{ROOT}/compare", content='{"query":"VPN"}')
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "json_required"
    assert not provider.queries and not provider.document_batches


@pytest.mark.parametrize(
    ("file_id", "body", "status", "code"),
    [
        ("oversized", b"x" * 48_001, 413, "knowledge_limit"),
        ("wrong-encoding", b"\xff\xff", 422, "invalid_document"),
        ("malformed", b"no metadata", 422, "invalid_document"),
        ("sensitive", None, 422, "sensitive_document"),
    ],
)
def test_bad_documents_fail_before_exposure_or_embedding(
    client, settings, provider, file_id, body, status, code
):
    secret = "sk-dont-display-this"
    if body is None:
        write_fixture_document(
            settings.knowledge_dir, doc_id=file_id, category="device", code="DEV-201", body=f"secret={secret}"
        )
    else:
        (settings.knowledge_dir / f"{file_id}.md").write_bytes(body)
    for response in [client.get(ROOT), client.post(f"{ROOT}/compare", json={"query": "VPN"})]:
        assert response.status_code == status and secret not in response.text
        assert response.json()["detail"]["code"] == code
    assert not provider.queries and not provider.document_batches


def test_chunk_limit_is_checked_before_embedding(client, provider, monkeypatch):
    monkeypatch.setattr(lab_module, "MAX_CHUNKS_PER_STRATEGY", 2)
    response = client.post(f"{ROOT}/compare", json={"query": "VPN"})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "chunk_limit"
    assert not provider.queries and not provider.document_batches


@pytest.mark.parametrize(
    ("provider_code", "status", "public_code"),
    [
        ("configuration_missing", 503, "configuration_missing"),
        ("provider_auth", 502, "provider_error"),
        ("provider_timeout", 504, "provider_timeout"),
        ("provider_rate_limit", 429, "provider_rate_limit"),
    ],
)
def test_provider_errors_do_not_emit_upstream_details(client, provider, provider_code, status, public_code):
    provider.embedding_error = ITDeskError(provider_code, "https://vendor.test/?api_key=sk-hide-me")
    response = client.post(f"{ROOT}/compare", json={"query": "VPN"})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == public_code
    assert "vendor.test" not in response.text and "sk-hide-me" not in response.text


async def test_second_comparison_is_rejected_and_lock_releases_after_timeout(settings, provider, monkeypatch):
    monkeypatch.setattr(lab_module, "COMPARISON_TIMEOUT_SECONDS", 0.05)
    lab = KnowledgeLab(settings, provider, KnowledgeStore(settings, provider))
    original_embed = provider.embed_documents
    entered = asyncio.Event()

    async def blocked(_texts):
        entered.set()
        await asyncio.Event().wait()

    provider.embed_documents = blocked
    request = CompareRequest(query="VPN")
    task = asyncio.create_task(lab.compare(request))
    await asyncio.wait_for(entered.wait(), timeout=1)
    with pytest.raises(BusinessError) as concurrent:
        await lab.compare(request)
    assert concurrent.value.code == "knowledge_compare_busy"
    with pytest.raises(BusinessError) as timed_out:
        await task
    assert timed_out.value.code == "provider_timeout"

    provider.embed_documents = original_embed
    monkeypatch.setattr(lab_module, "COMPARISON_TIMEOUT_SECONDS", 1)
    assert (await lab.compare(request)).strategies
