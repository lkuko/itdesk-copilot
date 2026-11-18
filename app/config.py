from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ITDESK_", env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    knowledge_dir: Path = PROJECT_ROOT / "knowledge"
    data_dir: Path = PROJECT_ROOT / "data"
    chat_base_url: str = "https://api.openai.com/v1"
    chat_api_key: SecretStr = SecretStr("")
    chat_model: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = ""
    embedding_models: list[str] = Field(default_factory=list)
    rerank_base_url: str = ""
    rerank_api_key: SecretStr = SecretStr("")
    rerank_models: list[str] = Field(default_factory=list)
    rerank_use_embedding_provider: bool = False
    telegram_webhook_secret: SecretStr | None = None
    telegram_allowed_chat_id: int | None = None
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:5173", "http://127.0.0.1:5173",
    ])
    max_cosine_distance: float = Field(default=0.50, ge=0, le=2)
    top_k: int = Field(default=5, ge=1, le=10)
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=80, ge=0, lt=100)
    request_timeout_s: float = Field(default=20, gt=0)
    embedding_index_timeout_s: float = Field(default=60, gt=0)
    chat_input_price_per_million: float | None = Field(default=None, ge=0)
    chat_output_price_per_million: float | None = Field(default=None, ge=0)
    embedding_price_per_million: float | None = Field(default=None, ge=0)

    @field_validator("cors_origins")
    @classmethod
    def explicit_origins(cls, values: list[str]) -> list[str]:
        origins = []
        for value in values:
            origin = value.strip().rstrip("/")
            try:
                parsed = urlsplit(origin)
                parsed.port  # Reject invalid or out-of-range ports.
            except ValueError as exc:
                raise ValueError("CORS origins must be explicit HTTP(S) origins") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or "*" in origin
                or any(char.isspace() for char in value)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without credentials or paths")
            if origin not in origins:
                origins.append(origin)
        return origins

    @field_validator(
        "chat_input_price_per_million",
        "chat_output_price_per_million",
        "embedding_price_per_million",
        mode="before",
    )
    @classmethod
    def blank_price(cls, value):
        return None if value == "" else value

    @field_validator("telegram_allowed_chat_id", mode="before")
    @classmethod
    def blank_chat_id(cls, value):
        return None if value == "" else value

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    def missing_configuration(self) -> list[str]:
        required = ("chat_model", "chat_api_key", "embedding_model", "embedding_api_key")
        return [
            name
            for name in required
            if not (
                getattr(self, name).get_secret_value()
                if isinstance(getattr(self, name), SecretStr)
                else getattr(self, name)
            )
        ]
