"""Local deterministic model doubles; no test calls a provider API."""

from pathlib import Path

import pytest

from app.config import Settings
from app.schemas import AnswerDraft, EmbeddingBatch, GeneratedAnswer


class FakeProvider:
    def __init__(self):
        self.document_batches = []
        self.queries = []
        self.generation_calls = []
        self.draft_override = None
        self.generation_error = None
        self.embedding_error = None

    @staticmethod
    def vector(text: str) -> list[float]:
        upper = text.upper()
        if "VPN" in upper or "远程接入" in text:
            return [1.0, 0.0, 0.0, 0.0]
        if "MAIL" in upper or "邮箱" in text:
            return [0.0, 1.0, 0.0, 0.0]
        if "DEV" in upper or "打印" in text:
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]

    async def embed_documents(self, texts):
        self.document_batches.append(list(texts))
        if self.embedding_error:
            raise self.embedding_error
        return EmbeddingBatch(vectors=[self.vector(text) for text in texts], tokens=len(texts) * 8)

    async def embed_query(self, text):
        self.queries.append(text)
        if self.embedding_error:
            raise self.embedding_error
        return EmbeddingBatch(vectors=[self.vector(text)], tokens=8)

    async def generate_answer(self, question, evidence):
        self.generation_calls.append((question, list(evidence)))
        if self.generation_error:
            raise self.generation_error
        draft = self.draft_override or AnswerDraft(
            answer="请依据测试手册核实设备注册状态；仍失败时联系 IT。",
            citation_ids=[evidence[0].chunk_id] if evidence else [],
            evidence_sufficient=bool(evidence),
        )
        return GeneratedAnswer(draft=draft, input_tokens=20, output_tokens=15)


def write_fixture_document(root: Path, *, doc_id: str, category: str, code: str, body: str):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{doc_id}.md"
    path.write_text(
        "---\n"
        f"doc_id: {doc_id}\n"
        f"title: {doc_id} 测试手册\n"
        f"category: {category}\n"
        "version: '1.0'\n"
        f"error_codes: [{code}]\n"
        "---\n\n"
        f"# {doc_id} 测试手册\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def settings(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    write_fixture_document(
        knowledge_dir,
        doc_id="vpn-registration",
        category="vpn",
        code="VPN-403",
        body="虚构演示资料。VPN-403 表示测试设备未注册，请联系 IT 核实设备注册状态。",
    )
    write_fixture_document(
        knowledge_dir,
        doc_id="vpn-extended",
        category="vpn",
        code="VPN-4030",
        body="虚构演示资料。VPN-4030 是另一种故障；请核对客户端版本，不应套用其他错误码的处理方法。",
    )
    write_fixture_document(
        knowledge_dir,
        doc_id="mail-login",
        category="email",
        code="MAIL-401",
        body="虚构演示资料。MAIL-401 表示邮箱登录失败，请检查工作账号的登录方式。",
    )
    return Settings(
        _env_file=None,
        knowledge_dir=knowledge_dir,
        data_dir=tmp_path / "data",
        chat_base_url="https://chat.example.test/v1",
        chat_api_key="test-chat-key",
        chat_model="test-chat",
        embedding_base_url="https://embed.example.test/v1",
        embedding_api_key="test-embedding-key",
        embedding_model="test-embedding",
        max_cosine_distance=0.5,
    )
