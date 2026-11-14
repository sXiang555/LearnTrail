"""Internal Function Calling protocol; not accepted by ordinary chat endpoints."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from learntrail_agent.schemas import Usage


class FunctionCall(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    arguments: str = Field(max_length=20000, repr=False)


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["function"] = "function"
    function: FunctionCall


class FunctionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(min_length=1, max_length=2000)
    parameters: dict[str, object]


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["function"] = "function"
    function: FunctionDefinition


class ToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["function"] = "function"
    function: dict[Literal["name"], str]


class ToolMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = Field(default=None, max_length=40000, repr=False)
    tool_calls: list[ToolCall] | None = Field(default=None, min_length=1, max_length=16)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=200)
    # Needed for some providers' continuation protocol, never public trace data.
    reasoning_content: SecretStr | None = Field(default=None, repr=False, exclude=True)

    @model_validator(mode="after")
    def validate_role(self) -> "ToolMessage":
        if self.role == "tool":
            if not self.tool_call_id or self.content is None or self.tool_calls:
                raise ValueError("Tool result requires call ID and content")
        elif self.tool_call_id:
            raise ValueError("Only a tool result has a tool call ID")
        if self.role != "assistant" and (self.tool_calls or self.reasoning_content is not None):
            raise ValueError("Only assistant messages may carry tool calls or provider reasoning")
        if self.role != "tool" and not self.tool_calls and not (self.content and self.content.strip()):
            raise ValueError("Message requires content or tool calls")
        if self.tool_calls and len({call.id for call in self.tool_calls}) != len(self.tool_calls):
            raise ValueError("Duplicate tool call IDs")
        return self

    def wire(self) -> dict[str, object]:
        result = self.model_dump(exclude_none=True)
        if self.reasoning_content is not None:
            result["reasoning_content"] = self.reasoning_content.get_secret_value()
        return result


class ToolChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ToolMessage] = Field(min_length=1, max_length=100)
    tools: list[ToolDefinition] = Field(min_length=1, max_length=32)
    tool_choice: Literal["auto", "none", "required"] | ToolChoice = "auto"
    max_tokens: int | None = Field(default=None, ge=1, le=32768, strict=True)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    # Omitted for generic providers. DeepSeek forced choices require disabled thinking.
    thinking: Literal["disabled", "enabled"] | None = None

    @model_validator(mode="after")
    def validate_protocol(self) -> "ToolChatRequest":
        names = [tool.function.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate tool names")
        if isinstance(self.tool_choice, ToolChoice) and self.tool_choice.function.get("name") not in names:
            raise ValueError("Named tool choice is not offered")
        if sum(len(m.content or "") + sum(len(c.function.arguments) for c in m.tool_calls or [])
               + len(m.reasoning_content.get_secret_value() if m.reasoning_content else "")
               for m in self.messages) > 100000:
            raise ValueError("Tool conversation exceeds context budget")
        pending: set[str] = set()
        seen: set[str] = set()
        for message in self.messages:
            if message.role == "tool":
                if message.tool_call_id not in pending:
                    raise ValueError("Unexpected tool result")
                pending.remove(message.tool_call_id)
            else:
                if pending:
                    raise ValueError("Missing tool results")
                if message.tool_calls:
                    ids = {call.id for call in message.tool_calls}
                    if ids & seen:
                        raise ValueError("Reused tool call ID")
                    pending, seen = ids, seen | ids
        if pending:
            raise ValueError("Conversation ends before tool results")
        return self


class ToolChatResponse(BaseModel):
    id: str
    model: str
    content: str | None = Field(default=None, max_length=200000, repr=False)
    refusal: str | None = Field(default=None, max_length=20000, repr=False)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=16)
    reasoning_content: SecretStr | None = Field(default=None, repr=False, exclude=True)
    finish_reason: str
    usage: Usage | None = None

    def assistant_message(self) -> ToolMessage:
        return ToolMessage(role="assistant", content=self.content, tool_calls=self.tool_calls or None,
                           reasoning_content=self.reasoning_content)
