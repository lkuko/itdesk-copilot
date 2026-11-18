from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class KnowledgeDocument(BaseModel):
    doc_id: str
    title: str
    category: Literal["vpn", "email", "device"]
    version: str
    error_codes: list[str] = Field(default_factory=list)
    source: str
    content: str


class KnowledgeChunk(KnowledgeDocument):
    chunk_id: str


class RetrievedChunk(KnowledgeChunk):
    distance: float | None = None
    exact_match: bool = False
    rerank_score: float | None = None


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    source: str
    version: str
    content: str


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=8000)
    citation_ids: list[str] = Field(default_factory=list, max_length=10)
    evidence_sufficient: bool


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("问题不能为空")
        return text.strip()


class Metrics(BaseModel):
    retrieval_ms: float = 0
    generation_ms: float = 0
    total_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    cost_usd: float | None = None


class ErrorInfo(BaseModel):
    code: str
    message: str


class AskResponse(BaseModel):
    run_id: str
    status: Literal["answered", "no_evidence", "failed"]
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    error: ErrorInfo | None = None


class IndexSummary(BaseModel):
    document_count: int
    chunk_count: int
    fingerprint: str
    embedding_model: str
    embedding_tokens: int = 0


@dataclass
class EmbeddingBatch:
    vectors: list[list[float]]
    tokens: int = 0


@dataclass
class GeneratedAnswer:
    draft: AnswerDraft
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    embedding_tokens: int = 0
    rerank_status: Literal["disabled", "applied", "fallback", "no_candidates"] = "disabled"


class TicketProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=5, max_length=2000)
    category: Literal["vpn", "email", "device"]

    @field_validator("title", "description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("草稿字段不能为空")
        return value.strip()


class AgentFinal(AnswerDraft):
    ticket_draft: TicketProposal | None = None


@dataclass
class GeneratedAgentAnswer:
    draft: AgentFinal
    input_tokens: int = 0
    output_tokens: int = 0


class MessageRequest(AskRequest):
    question: str = Field(min_length=1, max_length=2000, validation_alias=AliasChoices("text", "question"))
    request_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-zA-Z0-9_-]{8,64}$")


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Triage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal["vpn", "email", "device", "other"]
    intent: Literal["troubleshooting", "service_status", "ticket_status", "create_ticket", "other"]
    priority: Literal["low", "normal", "high"]
    missing_fields: list[str] = Field(default_factory=list, max_length=5)
    needs_info: bool = False

    @field_validator("missing_fields")
    @classmethod
    def meaningful_fields(cls, fields: list[str]) -> list[str]:
        if any(not field.strip() or len(field) > 120 for field in fields):
            raise ValueError("缺失信息必须为1–120字符")
        return fields

    @model_validator(mode="after")
    def consistent_missing_info(self):
        if self.needs_info != bool(self.missing_fields):
            raise ValueError("需要澄清时必须列出缺失信息")
        return self


@dataclass
class GeneratedTriage:
    draft: Triage
    input_tokens: int = 0
    output_tokens: int = 0
