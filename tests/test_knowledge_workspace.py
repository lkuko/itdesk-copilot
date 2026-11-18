"""Workspace publication, request validation, and active retrieval with local substitutes."""

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from app.business import BusinessError
from app.errors import ITDeskError
from app.main import create_app
from app.retrieval.store import KnowledgeStore
from app.retrieval.workspace import ApplyRequest, KnowledgeWorkspace

ROOT = "/api/knowledge/workspace"


def _upload() -> dict:
    return {
        "name": "测试设备.txt",
        "title": "打印设备 — 演示---检查",
        "category": "device",
        "content": ("😀 DEV-991 的设备测试。保留日志时间。\r\n\r\n" * 12),
    }


def test_import_profile_publish_rank_edit_and_remove_across_restart(settings, provider):
    application = create_app(settings, provider)
    asyncio.run(application.state.store.build_index())
    original_manifest = application.state.store.manifest_path.read_bytes()
    with TestClient(application) as client:
        defaults = client.get(ROOT).json()
        assert defaults["revision"] == "seed" and defaults["index"]["status"] == "ready"
        profile = {**defaults["profile"], "chunk_strategy": "fixed", "chunk_size": 120, "chunk_overlap": 10}
        payload = {"profile": profile, "files": [_upload()]}
        batches = len(provider.document_batches)
        preview = client.post(f"{ROOT}/preview", json=payload)
        assert preview.status_code == 200
        imported = preview.json()["documents"][0]
        assert imported["chunks"][0]["end"] == 120
        assert imported["chunks"][0]["content"] == imported["content"][:120]
        assert not application.state.knowledge_workspace.pointer_path.exists()
        assert len(provider.document_batches) == batches
        applied = client.post(f"{ROOT}/apply", json={**payload, "revision": defaults["revision"]})
        assert applied.status_code == 200, applied.text
        published = applied.json()
        assert published["rebuilt"] and published["index"]["status"] == "ready"
        assert published["managed_ids"] == [imported["doc_id"]]
        check = client.get(f"/api/knowledge/documents/{imported['doc_id']}")
        assert check.json() == imported
        assert len(client.get("/api/knowledge").json()["documents"]) == 4
        assert len(provider.document_batches) > batches
        assert not (settings.knowledge_dir / "imported").exists()
        assert application.state.store.manifest_path.read_bytes() == original_manifest

    with TestClient(create_app(settings, provider)) as restarted:
        state = restarted.get(ROOT).json()
        assert state["revision"] == published["revision"] and state["managed_ids"] == published["managed_ids"]
        previous_batches = len(provider.document_batches)
        rank_change = {**profile, "search_mode": "vector", "top_k": 1, "rerank_model": "local-lexical"}
        tuned = restarted.post(f"{ROOT}/apply", json={"profile": rank_change, "revision": state["revision"]})
        assert tuned.status_code == 200 and not tuned.json()["rebuilt"]
        assert len(provider.document_batches) == previous_batches
        result = restarted.post(f"{ROOT}/search", json={"query": "DEV-991"})
        assert result.status_code == 200, result.text
        assert result.json()["rerank_status"] == "applied"
        (hit,) = result.json()["hits"]
        assert hit["doc_id"] == imported["doc_id"] and hit["rerank_score"] is not None
        base = {"profile": rank_change, "revision": tuned.json()["revision"]}
        assert (
            restarted.post(f"{ROOT}/apply", json={**base, "remove_ids": ["vpn-registration"]}).status_code
            == 422
        )
        stale = restarted.post(f"{ROOT}/apply", json={**base, "revision": state["revision"]})
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "workspace_conflict"
        removed = restarted.post(f"{ROOT}/apply", json={**base, "remove_ids": [imported["doc_id"]]})
        assert removed.status_code == 200 and removed.json()["managed_ids"] == []
        assert restarted.get(f"/api/knowledge/documents/{imported['doc_id']}").status_code == 404
        assert restarted.get("/api/knowledge/documents/vpn-registration").status_code == 200


def test_failed_build_and_in_flight_build_keep_the_published_generation(settings, provider):
    application = create_app(settings, provider)
    with TestClient(application) as client:
        state = client.get(ROOT).json()
        first = client.post(f"{ROOT}/apply", json={**state_subset(state), "files": [_upload()]})
        assert first.status_code == 200, first.text
        published = first.json()
        before = application.state.knowledge_workspace.pointer_path.read_bytes()
        reached_build, resume = threading.Event(), threading.Event()

        async def fail_staging(_texts):
            reached_build.set()
            assert await asyncio.to_thread(resume.wait, 5)
            raise ITDeskError("provider_timeout", "internal provider details")

        provider.embed_documents = fail_staging
        candidate = {
            **state_subset(published),
            "profile": {**published["profile"], "chunk_size": 600},
        }
        responses = []
        thread = threading.Thread(
            target=lambda: responses.append(client.post(f"{ROOT}/apply", json=candidate))
        )
        thread.start()
        try:
            assert reached_build.wait(5)
            assert client.get(ROOT).json()["revision"] == published["revision"]
            assert client.post(f"{ROOT}/search", json={"query": "DEV-991"}).status_code == 200
            result = client.post(f"{ROOT}/apply", json=candidate)
            assert result.status_code == 409 and result.json()["detail"]["code"] == "workspace_busy"
        finally:
            resume.set()
            thread.join(5)
        assert responses[0].status_code == 504 and "internal provider" not in responses[0].text
        assert application.state.knowledge_workspace.pointer_path.read_bytes() == before
        assert client.get(ROOT).json()["revision"] == published["revision"]


def state_subset(state: dict) -> dict:
    return {"profile": state["profile"], "revision": state["revision"]}


def test_import_rejects_paths_credentials_unknown_model_and_untyped_body(settings, provider):
    with TestClient(create_app(settings, provider)) as client:
        profile = client.get(ROOT).json()["profile"]
        for path in ["../unsafe.txt", r"..\unsafe.md", "bad.html"]:
            assert (
                client.post(
                    f"{ROOT}/preview",
                    json={
                        "profile": profile,
                        "files": [{**_upload(), "name": path}],
                    },
                ).status_code
                == 422
            )
        sample = "sk-no-display-0123456789"
        gated = client.post(
            f"{ROOT}/preview",
            json={
                "profile": profile,
                "files": [{**_upload(), "content": f"密钥：{sample}"}],
            },
        )
        assert gated.status_code == 422 and sample not in gated.text
        unknown = client.post(
            f"{ROOT}/preview", json={"profile": {**profile, "embedding_model": "unapproved"}}
        )
        assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "invalid_embedding_model"
        assert client.post(f"{ROOT}/preview", content='{"profile":{}}').status_code == 415
        assert not provider.document_batches and not provider.queries


async def test_task_cancellation_preserves_current_and_releases_writer(settings, provider):
    legacy = KnowledgeStore(settings, provider)
    workspace = KnowledgeWorkspace(settings, provider, legacy)
    initial = workspace.state()
    published = await workspace.apply(
        ApplyRequest(profile=initial.profile, revision=initial.revision, files=[_upload()])
    )
    before = workspace.pointer_path.read_bytes()
    entered = asyncio.Event()
    original_embed = provider.embed_documents

    async def block_embeddings(_texts):
        entered.set()
        await asyncio.Event().wait()

    provider.embed_documents = block_embeddings
    change = ApplyRequest(
        profile=published.profile.model_copy(update={"chunk_size": 600}), revision=published.revision
    )
    building = asyncio.create_task(workspace.apply(change))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        # A second instance has its own asyncio lock but must still use the SQLite writer lock.
        peer = KnowledgeWorkspace(settings, provider, legacy)
        with pytest.raises(BusinessError) as second_writer:
            await peer.apply(change)
        assert second_writer.value.code == "workspace_busy"
    finally:
        building.cancel()
        with pytest.raises(asyncio.CancelledError):
            await building
    assert workspace.pointer_path.read_bytes() == before
    provider.embed_documents = original_embed
    assert (await peer.apply(change)).rebuilt
