"""Environment-backed configuration; no credentials are required for health checks."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="LearnTrail AI", min_length=1, max_length=100)
    learntrail_ai_env: Literal["local", "test"] = "local"
    llm_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    llm_base_url: str = ""
    llm_model: str = Field(default="", max_length=200)
    llm_timeout_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    llm_connect_timeout_seconds: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    llm_temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    llm_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    llm_stream_include_usage: bool = False
    database_url: SecretStr = Field(default=SecretStr(""), repr=False)
    database_pool_min_size: int = Field(default=1, ge=1, le=20)
    database_pool_max_size: int = Field(default=5, ge=1, le=20)
    database_timeout_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    database_statement_timeout_ms: int = Field(default=10000, ge=100, le=30000)
    database_schema: str = Field(default="public",pattern=r"^(public|eval_ai_[0-9a-f]{32})$")
    jwt_public_key_file: Path | None = None
    java_gateway_base_url: str = "http://127.0.0.1:10010"
    java_timeout_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    context_max_turns: int = Field(default=10, ge=1, le=20)
    context_max_chars: int = Field(default=60000, ge=20000, le=80000)
    embedding_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    embedding_base_url: str = ""
    embedding_model: str = Field(default="", max_length=200)
    embedding_model_revision: str = Field(default="", max_length=100)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=16000)
    embedding_send_dimensions: bool = False
    embedding_batch_size: int = Field(default=32, ge=1, le=128)
    embedding_timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    rag_chunk_size: int = Field(default=1000, ge=32, le=8000)
    rag_chunk_overlap: int = Field(default=100, ge=0, le=4000)
    rag_index_timeout_seconds: float = Field(default=180, gt=0, le=600, allow_inf_nan=False)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_candidate_k: int = Field(default=20, ge=1, le=100)
    rag_min_similarity: float = Field(default=0.35, ge=-1, le=1, allow_inf_nan=False)
    rag_bm25_k1: float = Field(default=1.2, gt=0, le=3, allow_inf_nan=False)
    rag_bm25_b: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    rag_bm25_min_score: float = Field(default=0, ge=0, allow_inf_nan=False)
    rag_rrf_k: int = Field(default=60, ge=1, le=1000)
    rag_max_articles: int = Field(default=100, ge=1, le=500)
    rag_context_max_chars: int = Field(default=12000, ge=500, le=60000)
    rag_answer_max_tokens: int = Field(default=1500, ge=64, le=8192)
    reranker_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    reranker_base_url: str = ""
    reranker_model: str = Field(default="", max_length=200)
    reranker_protocol: Literal["cohere", "dashscope"] = "cohere"
    reranker_path: str = Field(default="/rerank", pattern=r"^/[A-Za-z0-9/_-]+$", max_length=200)
    reranker_timeout_seconds: float = Field(default=20, gt=0, le=120, allow_inf_nan=False)
    agent_max_rounds: int = Field(default=6, ge=1, le=20)
    agent_max_tool_calls: int = Field(default=24, ge=1, le=128)
    agent_tool_concurrency: int = Field(default=3, ge=1, le=8)
    agent_tool_output_chars: int = Field(default=16000, ge=500, le=30000)
    agent_timeout_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    agent_max_tokens: int = Field(default=1500, ge=1, le=8192)
    agent_thinking_mode: Literal["provider_default", "disabled", "enabled"] = "provider_default"
    checkpoint_key_file: Path = PROJECT_ROOT.parent / ".local/run/learntrail-checkpoint.key"
    approval_ttl_seconds: int = Field(default=900,ge=10,le=86400)
    skills_dir: Path = PROJECT_ROOT / "skills"
    mcp_servers_file: Path | None = None
    mcp_first_party_url: str = "http://127.0.0.1:8100/mcp/"
    memory_context_chars: int = Field(default=2000,ge=200,le=6000)
    memory_max_items: int = Field(default=3,ge=1,le=8)
    agent_memory_enabled: bool = True
    agent_skills_enabled: bool = True
    agent_prompt_suffix: str = Field(default="",max_length=2000)
    observability_enabled: bool = False
    observability_dir: Path = PROJECT_ROOT.parent / ".local/observability"
    observability_sample_rate: float = Field(default=1,ge=0,le=1,allow_inf_nan=False)
    observability_file_max_bytes: int = Field(default=10485760,ge=1024,le=104857600)
    otel_metric_export_interval_ms: int = Field(default=5000,ge=1000,le=60000)
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_headers: SecretStr = Field(default=SecretStr(""),repr=False)
    langfuse_enabled: bool = False
    langfuse_base_url: str = ""
    langfuse_public_key: SecretStr = Field(default=SecretStr(""),repr=False)
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""),repr=False)
    llm_pricing_file: Path | None = None
    judge_model: str = ""
    judge_base_url: str = ""
    judge_api_key: SecretStr = Field(default=SecretStr(""),repr=False)
    # Standalone Judge fallback; formal evaluations derive a run-wide budget from tasks.
    judge_max_calls: int = Field(default=8,ge=1)
    judge_timeout_seconds: float = Field(default=30,gt=0,le=120,allow_inf_nan=False)
    judge_thinking_mode: Literal["auto", "provider_default", "disabled", "enabled"] = "auto"

    @field_validator("otel_exporter_otlp_endpoint","langfuse_base_url")
    @classmethod
    def validate_telemetry_endpoint(cls,value: str) -> str:
        value=value.strip().rstrip("/")
        if not value:return value
        u=urlsplit(value)
        if (u.scheme not in {"https","http"} or not u.hostname or u.username or u.password or u.query or u.fragment
                or u.scheme=="http" and u.hostname not in {"127.0.0.1","localhost","::1"}):
            raise ValueError("Telemetry requires explicit HTTPS or loopback HTTP without URL credentials")
        return value

    @field_validator("llm_base_url", "java_gateway_base_url", "embedding_base_url", "reranker_base_url", "judge_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return value
        try:
            url = urlsplit(value)
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname is not None
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and not any(char.isspace() or ord(char) < 32 for char in value)
            )
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("API base must be HTTP(S) without credentials or query")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if raw and (not raw.startswith(("postgresql://", "postgres://")) or any(c.isspace() for c in raw)):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URI")
        return SecretStr(raw)

    @model_validator(mode="after")
    def validate_pool_sizes(self) -> "Settings":
        if self.database_pool_min_size > self.database_pool_max_size:
            raise ValueError("Database minimum pool size exceeds maximum")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.rag_candidate_k < self.rag_top_k:
            raise ValueError("Candidate count must cover top K")
        return self

    @field_validator("llm_api_key", "embedding_api_key", "reranker_api_key", "judge_api_key", "otel_exporter_otlp_headers")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if any(ord(char) < 32 or ord(char) > 126 for char in raw):
            raise ValueError("API key must contain printable ASCII characters")
        return SecretStr(raw)

    @field_validator("app_name", "llm_model", "embedding_model", "embedding_model_revision", "reranker_model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.get_secret_value() and self.llm_base_url and self.llm_model)

    @property
    def embedding_configured(self) -> bool:
        return bool(self.embedding_api_key.get_secret_value() and self.embedding_base_url
                    and self.embedding_model and self.embedding_dimensions)

    @property
    def reranker_configured(self) -> bool:
        return bool(self.reranker_api_key.get_secret_value() and self.reranker_base_url and self.reranker_model)
