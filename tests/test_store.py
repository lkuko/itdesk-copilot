"""Integration tests use a real persistent Chroma collection and tiny fake vectors."""

import pytest

from app.errors import ITDeskError
from app.retrieval.ingestion import chunk_documents, load_documents
from app.retrieval.store import KnowledgeStore


@pytest.mark.asyncio
async def test_index_build_is_idempotent_and_persists_across_store_instances(settings, provider):
    store = KnowledgeStore(settings, provider)
    first = await store.build_index()
    second = await store.build_index()
    reopened = KnowledgeStore(settings, provider)

    assert first.document_count == 3
    assert first.chunk_count >= 3
    assert first.fingerprint == second.fingerprint
    assert first.chunk_count == second.chunk_count
    assert reopened.health()["status"] == "ready"
    hits = await reopened.retrieve("MAIL-401 邮箱登录失败")
    assert hits.chunks[0].doc_id == "mail-login"
    assert hits.chunks[0].exact_match is True


@pytest.mark.asyncio
async def test_complete_error_code_does_not_match_a_longer_identifier(settings, provider):
    store = KnowledgeStore(settings, provider)
    await store.build_index()

    short = await store.retrieve("请解释 VPN-403", top_k=5)
    long = await store.retrieve("请解释 VPN-4030", top_k=5)

    assert short.chunks[0].doc_id == "vpn-registration"
    assert {chunk.doc_id for chunk in short.chunks if chunk.exact_match} == {"vpn-registration"}
    assert long.chunks[0].doc_id == "vpn-extended"
    assert {chunk.doc_id for chunk in long.chunks if chunk.exact_match} == {"vpn-extended"}
    assert len({chunk.chunk_id for chunk in long.chunks}) == len(long.chunks)


@pytest.mark.asyncio
async def test_low_similarity_returns_no_evidence(settings, provider):
    store = KnowledgeStore(settings, provider)
    await store.build_index()

    result = await store.retrieve("公司绩效奖金怎么算？")

    assert result.chunks == []


@pytest.mark.asyncio
async def test_changed_embedding_model_requires_explicit_rebuild(settings, provider):
    store = KnowledgeStore(settings, provider)
    await store.build_index()
    changed = settings.model_copy(update={"embedding_model": "different-embedding"})
    stale = KnowledgeStore(changed, provider)

    assert stale.health()["status"] == "stale"
    with pytest.raises(ITDeskError):
        await stale.retrieve("VPN-403")

    rebuilt = await stale.build_index(rebuild=True)
    assert rebuilt.embedding_model == "different-embedding"
    assert stale.health()["status"] == "ready"
    assert (await stale.retrieve("VPN-403")).chunks[0].doc_id == "vpn-registration"


@pytest.mark.asyncio
async def test_changed_source_content_is_detected(settings, provider):
    store = KnowledgeStore(settings, provider)
    original = await store.build_index()
    source = settings.knowledge_dir / "vpn-registration.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n新增：联系支持前记录客户端版本。", encoding="utf-8"
    )
    stale = KnowledgeStore(settings, provider)

    assert stale.health()["status"] == "stale"
    with pytest.raises(ITDeskError):
        await stale.retrieve("VPN-403")
    updated = await stale.build_index(rebuild=True)
    assert updated.fingerprint != original.fingerprint


@pytest.mark.asyncio
async def test_unbuilt_index_cannot_be_queried(settings, provider):
    store = KnowledgeStore(settings, provider)

    with pytest.raises(ITDeskError):
        await store.retrieve("VPN-403")
    assert provider.queries == []


@pytest.mark.asyncio
async def test_failed_rebuild_does_not_replace_active_index(settings, provider):
    store = KnowledgeStore(settings, provider)
    await store.build_index()
    active_manifest = store.manifest_path.read_text(encoding="utf-8")
    provider.embedding_error = ITDeskError("provider_timeout", "Embedding 超时")

    with pytest.raises(ITDeskError, match="Embedding"):
        await store.build_index(rebuild=True)

    assert store.manifest_path.read_text(encoding="utf-8") == active_manifest
    provider.embedding_error = None
    assert store.health()["status"] == "ready"
    assert (await store.retrieve("VPN-403")).chunks[0].doc_id == "vpn-registration"


@pytest.mark.asyncio
async def test_query_dimension_drift_is_an_explicit_error(settings, provider):
    store = KnowledgeStore(settings, provider)
    await store.build_index()
    provider.vector = lambda _text: [1.0, 0.0]

    with pytest.raises(ITDeskError) as caught:
        await store.retrieve("VPN-403")

    assert caught.value.code == "embedding_dimension_changed"


@pytest.mark.asyncio
async def test_exact_code_retains_neighboring_verification_without_false_exact_label(settings, provider):
    source = settings.knowledge_dir / "vpn-registration.md"
    source.write_text(
        "---\n"
        "doc_id: vpn-registration\n"
        "title: VPN 设备登记手册\n"
        "category: vpn\n"
        "version: '1.0'\n"
        "error_codes: [VPN-403]\n"
        "---\n\n"
        "VPN-403 表示测试设备未登记。\n\n"
        + "排查时先核对设备登记状态。" * 12
        + "\n\n## 如何验证\n\n验证唯一标记：登录成功后必须能够打开演示内部门户。"
        + "若登记仍异常应转交 IT 人工核查，不自行修改白名单。" * 3,
        encoding="utf-8",
    )
    settings = settings.model_copy(update={"chunk_size": 200, "chunk_overlap": 40})
    chunks = [
        chunk
        for chunk in chunk_documents(load_documents(settings.knowledge_dir), 200, 40)
        if chunk.doc_id == "vpn-registration"
    ]
    assert "VPN-403" in chunks[0].content
    assert "验证唯一标记" not in chunks[0].content
    assert "验证唯一标记" in chunks[1].content
    assert "VPN-403" not in chunks[1].content
    store = KnowledgeStore(settings, provider)
    await store.build_index()
    # Make vector results irrelevant; the neighbor must come from exact-hit expansion.
    provider.vector = lambda _text: [0.0, 0.0, 0.0, 1.0]

    result = await store.retrieve("VPN-403 如何验证？", top_k=2)

    assert len(result.chunks) == 2
    assert result.chunks[0].chunk_id == chunks[0].chunk_id
    assert result.chunks[0].exact_match is True
    assert result.chunks[1].chunk_id == chunks[1].chunk_id
    assert result.chunks[1].exact_match is False
    assert "验证唯一标记" in result.chunks[1].content
