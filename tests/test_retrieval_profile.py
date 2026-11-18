import json

import httpx
import httpx2
import openai
import pytest
from pydantic import ValidationError

from app.errors import ITDeskError
from app.providers import OpenAIProvider
from app.retrieval.profile import LOCAL_RERANKER, ModelRegistry, RetrievalProfile
from app.retrieval.store import KnowledgeStore
from tests.conftest import write_fixture_document


def profile_with(registry, **changes):
    return RetrievalProfile.model_validate({**registry.default_profile().model_dump(), **changes})


def test_registry_exposes_only_named_models_and_profile_cannot_accept_provider_credentials(settings):
    settings = settings.model_copy(update={"embedding_models": ["alternative", "test-embedding", "https://demo"]})
    registry = ModelRegistry(settings)
    assert registry.embedding_models == ["test-embedding", "alternative"]
    assert registry.rerank_models == [LOCAL_RERANKER]
    with pytest.raises(ValidationError):
        profile_with(registry, score_threshold="0.3")
    with pytest.raises(ValidationError):
        profile_with(registry, chunk_overlap=100)
    with pytest.raises(ValidationError):
        profile_with(registry, base_url="https://test.example/v1")
    with pytest.raises(ValidationError):
        profile_with(registry, top_k=8, rerank_model=LOCAL_RERANKER, rerank_top_n=7)
    with pytest.raises(ITDeskError) as error:
        registry.validate_profile(profile_with(registry, embedding_model="not-allowlisted"))
    assert error.value.code == "invalid_embedding_model"
    assert "not-allowlisted" not in str(error.value)


@pytest.mark.asyncio
async def test_search_changes_preserve_index_while_fixed_chunk_strategy_invalidates_it(settings, provider):
    registry = ModelRegistry(settings)
    legacy = KnowledgeStore(settings, provider)
    previous = await legacy.build_index()
    vector = registry.make_store(settings, provider, profile_with(registry, search_mode="vector", top_k=1))
    assert vector.health()["status"] == "ready"
    assert (await vector.build_index()).fingerprint == previous.fingerprint
    vector_result = await vector.retrieve("VPN-403")
    assert len(vector_result.chunks) == 1
    assert not vector_result.chunks[0].exact_match

    fixed = registry.make_store(settings, provider, profile_with(registry, chunk_strategy="fixed"))
    assert fixed.health()["status"] == "stale"
    assert (await fixed.build_index(rebuild=True)).fingerprint != previous.fingerprint


@pytest.mark.asyncio
async def test_alternate_embedding_model_used_to_index_and_search_without_mutating_main_provider(settings):
    settings = settings.model_copy(update={"embedding_models": ["alternative"]})
    registry = ModelRegistry(settings)
    provider = OpenAIProvider(settings)
    requests = []

    def embed(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx2.Response(200, json={
            "object": "list", "model": payload["model"],
            "data": [{"object": "embedding", "index": i, "embedding": [1.0, 0.0]}
                     for i, _ in enumerate(payload["input"])],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        })

    provider._embeddings = openai.AsyncOpenAI(
        api_key="mock", base_url=settings.embedding_base_url, max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(embed)),
    )
    try:
        profile = profile_with(registry, embedding_model="alternative")
        selected = registry.make_store(settings, provider, profile)
        summary = await selected.build_index()
        await selected.retrieve("VPN-403 怎么处理？")
        await provider.embed_query("原有问答")
    finally:
        await provider.close()

    assert summary.embedding_model == "alternative"
    assert [request["model"] for request in requests] == ["alternative", "alternative", "test-embedding"]
    assert requests[1]["input"] == ["VPN-403 怎么处理？"]


@pytest.mark.asyncio
async def test_rerank_considers_candidate_beyond_top_k_and_explicitly_falls_back(
    settings, provider, monkeypatch,
):
    # Use distinct cosine distances so Chroma's ordering of ties cannot affect the comparison.
    def vector(text):
        if "vpn-extended" in text:
            return [0.8, 0.6, 0.0, 0.0]
        if "mail-login" in text:
            return [0.1, 0.99, 0.0, 0.0]
        return [1.0, 0.0, 0.0, 0.0]

    monkeypatch.setattr(provider, "vector", vector)
    profile = RetrievalProfile(
        embedding_model=settings.embedding_model, search_mode="vector", top_k=1,
        score_threshold=0, rerank_model=LOCAL_RERANKER, rerank_top_n=3,
    )
    calls = []

    async def rank(_question, docs):
        calls.append(docs)
        return [1 if "mail-login" in doc else 0 for doc in docs]

    legacy = KnowledgeStore(settings, provider)
    await legacy.build_index()
    baseline = (await legacy.retrieve("VPN-4030", top_k=1, strategy="vector")).chunks[0]
    result = await KnowledgeStore(settings, provider, profile=profile, ranker=rank).retrieve("VPN-4030")
    assert len(calls[0]) == 3
    assert result.rerank_status == "applied"
    assert result.chunks[0].doc_id == "mail-login"
    assert result.chunks[0].rerank_score == 1.0

    async def bad_rank(_question, _docs):
        return [float("nan")] * 3

    fallback = await KnowledgeStore(settings, provider, profile=profile, ranker=bad_rank).retrieve("VPN-4030")
    assert fallback.rerank_status == "fallback"
    assert fallback.chunks[0].chunk_id == baseline.chunk_id
    assert fallback.chunks[0].rerank_score is None


@pytest.mark.asyncio
async def test_threshold_and_rerank_preserve_exact_hit_and_neighbor(settings, provider):
    write_fixture_document(
        settings.knowledge_dir, doc_id="vpn-registration", category="vpn", code="VPN-403",
        body="VPN-403 检查连接。" + "设备核查步骤与预期结果。" * 12,
    )
    registry = ModelRegistry(settings)
    profile = profile_with(
        registry, chunk_strategy="fixed", chunk_size=100, chunk_overlap=0,
        rerank_model=LOCAL_RERANKER, top_k=4, score_threshold=1,
    )
    calls = []

    async def rank(_question, docs):
        calls.extend(docs)
        return [1] * len(docs)

    store = KnowledgeStore(settings, provider, profile=profile, ranker=rank)
    await store.build_index()
    result = await store.retrieve("VPN-403")
    first, neighbor, *rest = result.chunks
    assert first.exact_match and first.doc_id == neighbor.doc_id == "vpn-registration"
    assert not neighbor.exact_match and neighbor.rerank_score is None
    assert all("设备核查步骤" not in text and "MAIL-401" not in text for text in calls)
    assert [chunk.doc_id for chunk in rest] == ["vpn-extended"]


@pytest.mark.asyncio
async def test_lexical_rerank_prefers_shared_error_code_and_chinese_terms(settings):
    registry = ModelRegistry(settings)
    scores = await registry.rerank(
        LOCAL_RERANKER, "VPN-403 设备登记", ["VPN-4030 客户端版本", "VPN-403 请检查设备登记"],
    )
    assert scores == [0.0, 1.0]


@pytest.mark.asyncio
async def test_remote_rerank_reuses_explicit_provider_and_maps_scores_by_index(settings, monkeypatch):
    settings = settings.model_copy(update={
        "rerank_use_embedding_provider": True, "rerank_base_url": "https://different.example.test/v1",
        "rerank_models": ["tested-reranker"],
    })
    registry = ModelRegistry(settings)
    assert registry.rerank_models == [LOCAL_RERANKER, "tested-reranker"]
    responses = [
        [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}],
        [{"index": 1, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.2}],
    ]
    original_client = httpx.AsyncClient
    clients = []

    def handle(request):
        assert str(request.url) == settings.embedding_base_url + "/rerank"
        assert request.headers["authorization"] == "Bearer test-embedding-key"
        assert json.loads(request.content) == {
            "model": "tested-reranker", "query": "VPN", "documents": ["one", "two"], "top_n": 2,
        }
        return httpx.Response(200, json={"results": responses.pop(0)})

    def client(**kwargs):
        instance = original_client(transport=httpx.MockTransport(handle), **kwargs)
        clients.append(instance)
        return instance

    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert await registry.rerank("tested-reranker", "VPN", ["one", "two"]) == [0.2, 0.9]
    with pytest.raises(ITDeskError) as error:
        await registry.rerank("tested-reranker", "VPN", ["one", "two"])
    assert error.value.code == "rerank_failed"
    assert all(client.is_closed for client in clients)
