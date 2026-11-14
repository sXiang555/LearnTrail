"""Validated contracts shared by the HTTP API and model client."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "developer", "user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[Message] = Field(min_length=1, max_length=100)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_tokens: StrictInt | None = Field(default=None, ge=1, le=32768)

    @model_validator(mode="after")
    def validate_messages(self) -> "ChatRequest":
        if any(not message.content.strip() for message in self.messages):
            raise ValueError("Message content must not be blank")
        if sum(len(message.content) for message in self.messages) > 100000:
            raise ValueError("Message content exceeds the request limit")
        return self


class Usage(BaseModel):
    prompt_tokens: StrictInt | None = Field(default=None, ge=0)
    completion_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)


class ChatResponse(BaseModel):
    id: str
    model: str
    content: str
    finish_reason: str
    usage: Usage | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app_name: str
    environment: str
    llm_configured: bool


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="新对话", min_length=1, max_length=200)


class SessionChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=20000)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_tokens: StrictInt | None = Field(default=None, ge=1, le=32768)


class SessionInfo(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class SessionList(BaseModel):
    items: list[SessionInfo]
    total: int
    limit: int
    offset: int


class StoredMessage(BaseModel):
    id: UUID
    run_id: UUID
    role: Literal["user", "assistant"]
    content: str
    status: Literal["pending", "completed", "failed"]
    created_at: datetime
    run_kind: Literal["chat", "agent"] = "chat"
    run_evidence: list[dict[str, object]] = Field(default_factory=list, exclude=True, repr=False)
    redacted: bool = False
    citations: list[dict[str, object]] = Field(default_factory=list)


class RunInfo(BaseModel):
    id: UUID
    status: Literal["running", "succeeded", "failed", "cancelled", "paused"]
    model: str | None
    finish_reason: str | None
    usage: Usage | None
    duration_ms: int | None
    error_code: str | None
    created_at: datetime
    expires_at: datetime
    finished_at: datetime | None
    kind: Literal["chat", "agent"] = "chat"
    agent_data: dict[str, object] = Field(default_factory=dict, exclude=True, repr=False)


class HistoryTurn(BaseModel):
    question: str
    answer: str
    kind: Literal["chat", "agent"]
    evidence: list[dict[str, object]] = Field(default_factory=list, repr=False)
    citations: list[dict[str, object]] = Field(default_factory=list, repr=False)


class SessionDetail(SessionInfo):
    messages: list[StoredMessage]
    total_messages: int
    message_limit: int
    message_offset: int
    recent_runs: list[RunInfo]  # Latest 20 runs, independent of message pagination.


class SessionChatResponse(ChatResponse):
    run_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
