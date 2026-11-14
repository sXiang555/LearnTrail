"""RAG JSON contracts; request IDs and stage results can later become SSE events."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from learntrail_agent.clients.embedding import EmbeddingUsage
from learntrail_agent.schemas import Usage


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["vector", "bm25", "hybrid"] = "hybrid"
    top_k: int | None = Field(default=None, ge=1, le=20, strict=True)
    article_slugs: list[str] | None = Field(default=None, min_length=1, max_length=20)
    rerank: bool = False

    @field_validator("article_slugs")
    @classmethod
    def validate_slugs(cls, slugs: list[str] | None) -> list[str] | None:
        import re
        if slugs and any(not re.fullmatch(r"[a-z0-9-]{1,80}", slug) for slug in slugs):
            raise ValueError("Invalid article slug")
        return list(dict.fromkeys(slugs)) if slugs else None


class SearchHit(BaseModel):
    article_id: int
    slug: str
    generation: UUID
    ordinal: int
    title: str
    section: str
    start_char: int
    end_char: int
    content: str = Field(repr=False)
    content_hash: str
    source_type: str
    source_url: str = ""
    score: float
    vector_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    reranker_score: float | None = None

    @field_serializer("article_id")
    def serialize_java_id(self, value: int) -> str:
        return str(value)

    @property
    def key(self) -> tuple[int, UUID, int]:
        return self.article_id, self.generation, self.ordinal


class SearchResponse(BaseModel):
    request_id: UUID
    mode: str
    vector: list[SearchHit]
    bm25: list[SearchHit]
    fused: list[SearchHit]
    results: list[SearchHit]
    reranked: bool
    warnings: list[str] = Field(default_factory=list)
    embedding_usage: EmbeddingUsage | None = None
    duration_ms: int


class Citation(BaseModel):
    id: str
    article_id: str
    slug: str
    title: str
    section: str
    ordinal: int
    generation: UUID
    content_hash: str
    start_char: int
    end_char: int
    quote: str
    position_basis: Literal["normalized_unicode_codepoints"] = "normalized_unicode_codepoints"
    url: str
    source_type: str
    original_url: str = ""


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["answered", "insufficient_context"]
    answer: str = Field(min_length=1, max_length=20000)
    citation_ids: list[str] = Field(max_length=20)


class RagResponse(BaseModel):
    request_id: UUID
    session_id: UUID
    status: Literal["answered", "insufficient_context"]
    answer: str
    citations: list[Citation]
    retrieval: SearchResponse
    model: str | None = None
    usage: Usage | None = None
    duration_ms: int
