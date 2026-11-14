"""Native HTTPX Chat Completions; ordinary chat and internal Function Calling."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from learntrail_agent.errors import LLMError, protocol_error, timeout_error, upstream_error
from learntrail_agent.schemas import ChatRequest, ChatResponse, Usage
from learntrail_agent.settings import Settings
from learntrail_agent.streaming import StreamEvent, iter_sse_data
from learntrail_agent.clients.tool_models import ToolCall, ToolChatRequest, ToolChatResponse, ToolChoice
from learntrail_agent.observability import observed


class ProviderMessage(BaseModel):
    content: str | None = None
    refusal: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: SecretStr | None = Field(default=None, repr=False, exclude=True)


class CompletionChoice(BaseModel):
    index: Literal[0] = 0
    message: ProviderMessage
    finish_reason: str = Field(min_length=1)


class Completion(BaseModel):
    id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    choices: list[CompletionChoice] = Field(min_length=1, max_length=1)
    usage: Usage | None = None


class StreamChoice(BaseModel):
    index: Literal[0] = 0
    delta: ProviderMessage
    finish_reason: str | None = Field(default=None, min_length=1)


class ProviderChunk(BaseModel):
    id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    choices: list[StreamChoice] = Field(max_length=1)
    usage: Usage | None = None


@dataclass
class OpenStream:
    response: httpx.Response
    deadline: float
    provider_id: str | None = None
    provider_model: str | None = None


class LLMClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings

    def _payload(self, request: ChatRequest | ToolChatRequest, *, stream: bool) -> dict[str, Any]:
        if not self.settings.llm_configured:
            raise LLMError("LLM_NOT_CONFIGURED", "请先配置本机模型地址、模型名称和密钥。", 503)
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [message.wire() if isinstance(request, ToolChatRequest) else message.model_dump()
                         for message in request.messages],
            "stream": stream,
            "n": 1,
        }
        temperature = request.temperature if request.temperature is not None else self.settings.llm_temperature
        max_tokens = request.max_tokens if request.max_tokens is not None else self.settings.llm_max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stream and self.settings.llm_stream_include_usage:
            payload["stream_options"] = {"include_usage": True}
        if isinstance(request, ToolChatRequest):
            payload["tools"] = [tool.model_dump() for tool in request.tools]
            payload["tool_choice"] = (request.tool_choice.model_dump() if isinstance(request.tool_choice, ToolChoice)
                                      else request.tool_choice)
            if request.thinking is not None:
                payload["thinking"] = {"type": request.thinking}
        return payload

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.llm_api_key.get_secret_value()}"}

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        if not 200 <= response.status_code < 300:
            raise upstream_error(response.status_code)

    @observed("llm.completion","generation")
    async def _completion(
        self, request: ChatRequest | ToolChatRequest, *,
        thinking: Literal["disabled", "enabled"] | None = None, json_mode: bool = False,
    ) -> Completion:
        payload = self._payload(request, stream=False)
        if thinking is not None:
            if thinking not in {"disabled", "enabled"}:
                raise ValueError("Unsupported internal thinking mode")
            payload["thinking"] = {"type": thinking}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                response = await self.http.post(
                    f"{self.settings.llm_base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                    timeout=httpx.Timeout(self.settings.llm_timeout_seconds,
                        connect=min(self.settings.llm_connect_timeout_seconds,self.settings.llm_timeout_seconds)),
                )
                self._check_status(response)
                try:
                    completion = Completion.model_validate(response.json())
                except (ValueError, ValidationError):
                    raise protocol_error() from None
                return completion
        except (httpx.TimeoutException, TimeoutError):
            raise timeout_error() from None
        except httpx.HTTPError:
            raise LLMError("LLM_CONNECTION_ERROR", "无法连接模型服务。", 502, True) from None

    async def complete(
        self, request: ChatRequest, *,
        thinking: Literal["disabled", "enabled"] | None = None, json_mode: bool = False,
    ) -> ChatResponse:
        # Server-only provider options; never accepted by the browser ChatRequest.
        completion = await self._completion(request,thinking=thinking,json_mode=json_mode)
        choice = completion.choices[0]
        content = choice.message.content
        if content is None:
            content = choice.message.refusal
        if content is None or choice.message.tool_calls or choice.finish_reason == "tool_calls":
            raise protocol_error()
        return ChatResponse(id=completion.id, model=completion.model, content=content,
                            finish_reason=choice.finish_reason, usage=completion.usage)

    async def complete_with_tools(self, request: ToolChatRequest) -> ToolChatResponse:
        completion = await self._completion(request)
        return self._tool_response(completion)

    @staticmethod
    def _tool_response(completion: Completion) -> ToolChatResponse:
        choice = completion.choices[0]
        message = choice.message
        try:
            calls = [ToolCall.model_validate(call) for call in message.tool_calls or []]
            if len(calls) != len({call.id for call in calls}):
                raise ValueError("Duplicate call IDs")
            if bool(calls) != (choice.finish_reason == "tool_calls"):
                raise ValueError("Inconsistent tool finish reason")
            if (not calls and message.content is None and message.refusal is None
                    and choice.finish_reason not in {"content_filter", "refusal"}):
                raise ValueError("Empty assistant turn")
            if calls and message.refusal:
                raise ValueError("Refusal cannot execute tools")
            return ToolChatResponse(id=completion.id, model=completion.model, content=message.content,
                refusal=message.refusal, tool_calls=calls, reasoning_content=message.reasoning_content,
                finish_reason=choice.finish_reason, usage=completion.usage)
        except (ValueError, ValidationError):
            raise protocol_error() from None

    @observed("llm.tool_stream","generation")
    async def stream_with_tools(self, request: ToolChatRequest) -> AsyncIterator[tuple[str, Any]]:
        """Stream real content chunks; assemble tool fragments before execution."""
        handle = await self.open_stream(request)
        content, reasoning = [], []
        calls: dict[int, dict[str, Any]] = {}
        finish, usage, provider_id, model = None, None, None, None
        size = 0
        events = iter_sse_data(handle.response.aiter_lines())
        try:
            while True:
                try:
                    async with asyncio.timeout_at(handle.deadline):
                        data = await anext(events)
                except StopAsyncIteration:
                    break
                if data.strip() == "[DONE]":
                    if not finish or not provider_id or not model:
                        raise protocol_error()
                    message = {"content":"".join(content) or None,
                               "reasoning_content":"".join(reasoning) or None,
                               "tool_calls":[calls[i] for i in sorted(calls)] or None}
                    try:
                        value = Completion.model_validate({"id":provider_id,"model":model,
                            "choices":[{"index":0,"message":message,"finish_reason":finish}],"usage":usage})
                    except (ValueError, ValidationError):
                        raise protocol_error() from None
                    yield "complete", self._tool_response(value)
                    return
                try:
                    raw = json.loads(data)
                    if not isinstance(raw, dict):
                        raise ValueError()
                    provider_id = raw.get("id") or provider_id
                    model = raw.get("model") or model
                    if raw.get("usage") is not None:
                        usage = Usage.model_validate(raw["usage"])
                    choices = raw.get("choices")
                    if not isinstance(choices, list) or len(choices)>1:
                        raise ValueError()
                    if not choices:
                        if raw.get("usage") is None:
                            raise ValueError()
                        continue
                    choice = choices[0]
                    if choice.get("index",0) != 0 or isinstance(choice.get("index"),bool):
                        raise ValueError()
                    delta = choice.get("delta",{})
                    if not isinstance(delta,dict) or finish is not None:
                        raise ValueError()
                    text = delta.get("content")
                    thought = delta.get("reasoning_content")
                    for part in (text,thought):
                        if part is not None and not isinstance(part,str):
                            raise ValueError()
                        size += len(part or "")
                    if thought:
                        reasoning.append(thought)
                    if delta.get("refusal"):
                        raise LLMError("AGENT_MODEL_REFUSED","模型拒绝了本次请求。",422)
                    fragments = delta.get("tool_calls") or []
                    if not isinstance(fragments,list):
                        raise ValueError()
                    for fragment in fragments:
                        index = fragment["index"]
                        if type(index) is not int or not 0<=index<16:
                            raise ValueError()
                        target = calls.setdefault(index,{"id":"","type":"function",
                                                       "function":{"name":"","arguments":""}})
                        if fragment.get("type","function") != "function":
                            raise ValueError()
                        if fragment.get("id"):
                            if not isinstance(fragment["id"],str):
                                raise ValueError()
                            target["id"] += fragment["id"]
                        function = fragment.get("function") or {}
                        if not isinstance(function,dict):
                            raise ValueError()
                        for key in ("name","arguments"):
                            part = function.get(key)
                            if part is not None:
                                if not isinstance(part,str):
                                    raise ValueError()
                                target["function"][key] += part
                                size += len(part)
                        if (len(target["id"])>200 or len(target["function"]["name"])>64
                                or len(target["function"]["arguments"])>20000):
                            raise ValueError()
                    if size>200000:
                        raise ValueError()
                    if text:
                        content.append(text)
                        yield "delta", {"content":text}
                    if choice.get("finish_reason") is not None:
                        if not isinstance(choice["finish_reason"],str) or not choice["finish_reason"]:
                            raise ValueError()
                        finish = choice["finish_reason"]
                except (ValueError,TypeError,KeyError,AttributeError,ValidationError):
                    raise protocol_error() from None
            raise LLMError("LLM_STREAM_INTERRUPTED","模型工具流提前中断。",502,True)
        except (httpx.TimeoutException,TimeoutError):
            raise timeout_error() from None
        except httpx.HTTPError:
            raise LLMError("LLM_STREAM_INTERRUPTED","模型工具流连接中断。",502,True) from None
        finally:
            await events.aclose()
            await asyncio.shield(handle.response.aclose())

    @observed("llm.connect")
    async def open_stream(self, request: ChatRequest | ToolChatRequest) -> OpenStream:
        payload = self._payload(request, stream=True)
        response: httpx.Response | None = None
        handed_off = False
        deadline = asyncio.get_running_loop().time() + self.settings.llm_timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                upstream = self.http.build_request(
                    "POST", f"{self.settings.llm_base_url}/chat/completions",
                    json=payload, headers=self._headers(),
                )
                response = await self.http.send(upstream, stream=True)
                self._check_status(response)
                if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
                    raise protocol_error()
                response.encoding = "utf-8"
                handed_off = True
                return OpenStream(response, deadline)
        except (httpx.TimeoutException, TimeoutError):
            raise timeout_error() from None
        except httpx.HTTPError:
            raise LLMError("LLM_CONNECTION_ERROR", "无法连接模型服务。", 502, True) from None
        finally:
            if response is not None and not handed_off:
                await asyncio.shield(response.aclose())

    @observed("llm.stream","generation")
    async def stream_events(self, stream: OpenStream) -> AsyncIterator[StreamEvent]:
        finish_reason: str | None = None
        output_size = 0
        data_events = iter_sse_data(stream.response.aiter_lines())
        try:
            while True:
                # Timeout scopes must not span yields or changes of consumer task.
                if asyncio.get_running_loop().time() >= stream.deadline:
                    raise timeout_error()
                try:
                    async with asyncio.timeout_at(stream.deadline):
                        data = await anext(data_events)
                except StopAsyncIteration:
                    break
                if data.strip() == "[DONE]":
                    if finish_reason is None:
                        raise protocol_error()
                    yield "done", {"finish_reason": finish_reason}
                    return
                try:
                    chunk = ProviderChunk.model_validate(json.loads(data))
                except (ValueError, ValidationError):
                    raise protocol_error() from None
                if chunk.id is not None:
                    stream.provider_id = chunk.id
                if chunk.model is not None:
                    stream.provider_model = chunk.model
                if not chunk.choices and chunk.usage is None:
                    raise protocol_error()
                if chunk.choices:
                    choice = chunk.choices[0]
                    content = choice.delta.content or choice.delta.refusal
                    if choice.delta.tool_calls or choice.finish_reason == "tool_calls":
                        raise protocol_error()
                    if finish_reason is not None and (content or choice.finish_reason is not None):
                        raise protocol_error()
                    if content:
                        output_size += len(content)
                        if output_size > 200000:
                            raise protocol_error()
                        yield "delta", {"content": content}
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
                if chunk.usage is not None:
                    yield "usage", chunk.usage.model_dump()
            raise LLMError("LLM_STREAM_INTERRUPTED", "模型流式响应提前中断，请重新发起请求。", 502, True)
        except (httpx.TimeoutException, TimeoutError):
            raise timeout_error() from None
        except httpx.HTTPError:
            raise LLMError("LLM_STREAM_INTERRUPTED", "模型流式连接中断。", 502, True) from None
        finally:
            await data_events.aclose()
            await asyncio.shield(stream.response.aclose())
