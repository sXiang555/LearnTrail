"""Public Agent endpoints exclude private prompts, credentials and provider reasoning."""

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from uuid import UUID

from learntrail_agent.agent.assistants import Assistant
from learntrail_agent.schemas import SessionChatResponse


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1,max_length=20000)
    pause_before_tools: bool = Field(default=False,strict=True)
    skill: str | None = Field(default=None,pattern=r"^[a-z][a-z0-9-]{1,48}$")
    assistant: Assistant = "learning"

    @model_validator(mode="after")
    def validate_skill_assistant(self) -> "AgentChatRequest":
        if self.skill is not None and self.assistant != "learning":
            raise ValueError("显式选择技能时请使用学习助手。")
        return self


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentChatResponse(SessionChatResponse):
    assistant_message_id: UUID | None
    status: Literal["succeeded","paused"] = "succeeded"
    model_calls: int
    tool_calls: int
    usage_complete: bool
    duration_ms: int
    tools: list[dict[str, object]]
    checkpoint: dict[str, object] | None = None
    citations: list[dict[str, object]] = Field(default_factory=list)
