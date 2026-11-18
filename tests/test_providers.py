"""Transport and parsing contracts for the real API adapter, without network calls."""

import json
from types import SimpleNamespace

import httpx2
import openai
import pytest

from app.errors import ITDeskError
from app.providers import OpenAIProvider, api_error
from app.schemas import RetrievedChunk


@pytest.mark.asyncio
async def test_embedding_api_receives_raw_strings_instead_of_token_identifiers(settings):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["input"] == ["VPN-403 中文故障", "邮箱登录"]
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
                    {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                ],
                "model": "test-embedding",
                "usage": {"prompt_tokens": 12, "total_tokens": 12},
            },
        )

    provider = OpenAIProvider(settings)
    provider._embeddings = openai.AsyncOpenAI(
        api_key="transport-test-key",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    )
    try:
        result = await provider.embed_documents(["VPN-403 中文故障", "邮箱登录"])
    finally:
        await provider.close()

    assert len(requests) == 1
    assert result.vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert result.tokens == 12


@pytest.mark.asyncio
async def test_generation_adapter_rejects_non_json_and_retains_measured_usage(settings):
    class InvalidChat:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content="not json", usage_metadata={"input_tokens": 7, "output_tokens": 2})

    provider = OpenAIProvider(settings)
    provider._chat = InvalidChat()
    evidence = RetrievedChunk(
        chunk_id="doc:001",
        doc_id="doc",
        title="测试",
        category="vpn",
        source="vpn.md",
        version="1.0",
        content="VPN-403 测试证据",
    )

    with pytest.raises(ITDeskError) as caught:
        await provider.generate_answer("VPN-403 怎么处理？", [evidence])

    assert caught.value.code == "invalid_model_output"
    assert caught.value.input_tokens == 7
    assert caught.value.output_tokens == 2


def test_provider_error_does_not_echo_upstream_body():
    request = httpx2.Request("POST", "https://example.test/v1/chat/completions")
    error = openai.AuthenticationError(
        "upstream exposed never-show-this-secret",
        response=httpx2.Response(401, request=request),
        body={"secret": "never-show-this-secret"},
    )

    safe = api_error(error)

    assert safe.code == "provider_auth"
    assert "never-show-this-secret" not in safe.message
