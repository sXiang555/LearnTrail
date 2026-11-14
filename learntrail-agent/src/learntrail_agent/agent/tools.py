"""Explicit tool allowlist and bounded asynchronous execution, without reflection."""

from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID
from typing import Literal

import anyio
from pydantic import BaseModel, Field, SecretStr, ValidationError

from learntrail_agent.clients.tool_models import FunctionDefinition, ToolCall, ToolDefinition
from learntrail_agent.errors import AppError
from learntrail_agent.observability import observed

logger = logging.getLogger(__name__)

PUBLIC_TOOL_ERRORS = {
    "UNKNOWN_TOOL": "工具未注册或未启用。",
    "TOOL_FORBIDDEN": "当前身份没有此工具的执行权限。",
    "INVALID_TOOL_ARGUMENTS": "工具参数不符合声明，请按 Schema 调整。",
    "TOOL_OUTPUT_LIMIT": "工具结果过大，请缩小查询范围或分页。",
    "AUTH_REQUIRED": "需要有效登录身份。",
    "INVALID_TOKEN": "当前登录身份无法验证。",
    "TOKEN_EXPIRED": "登录已过期，需要重新登录。",
    "ACCOUNT_DISABLED": "当前账号不可用。",
    "ACCOUNT_STATUS_UNAVAILABLE": "暂时无法核验账号状态。",
    "UNSUPPORTED_CATEGORY": "现有 Java 文章接口没有分类筛选能力，请移除分类参数。",
    "AGENT_SOURCE_CHANGED": "查询来源或学习状态已变化，请重新查询。",
    "JAVA_READ_UNAVAILABLE": "Java 业务查询暂不可用。",
    "JAVA_READ_INVALID_RESPONSE": "Java 业务查询响应格式异常。",
    "RERANKER_NOT_CONFIGURED": "尚未配置精排服务，可关闭精排后重试。",
    "APPROVAL_REQUIRED": "此操作需要本人确认，尚未获得有效审批。",
    "APPROVAL_REJECTED": "用户拒绝了此操作，不得自行重复申请同一操作。",
    "APPROVAL_EXPIRED": "审批已过期，请由用户重新发起。",
    "APPROVAL_CANCELLED": "审批已取消，不能执行。",
    "APPROVAL_TAMPERED": "操作参数、策略或资源版本已变化，不能沿用原审批。",
    "APPROVAL_EXECUTION_UNCERTAIN": "该操作执行结果尚未确认，禁止自动重复执行。",
    "MEMORY_CONFLICT": "记忆内容已变化，需要重新确认。",
    "MEMORY_UNSAFE": "不能将敏感信息或未经用户原话支持的内容保存为记忆。",
    "MCP_UNAVAILABLE": "已授权的 MCP 服务暂不可用。",
    "MCP_TIMEOUT": "已授权的 MCP 工具执行超时。",
    "MCP_TOOL_ERROR": "MCP 工具返回了错误，未视为执行成功。",
    "MCP_SCHEMA_CHANGED": "MCP 工具定义已变化，请重新发起任务。",
}


@dataclass(frozen=True)
class ToolContext:
    user_id: int
    access_token: SecretStr = field(repr=False)
    permissions: frozenset[str]
    verify_identity: Callable[[], Awaitable[None]] = field(repr=False)
    session_id: UUID | None = None
    run_id: UUID | None = None
    verify_sources: Callable[[list[dict[str, object]]], Awaitable[None]] | None = field(default=None, repr=False)
    allowed_tools: frozenset[str] | None = None
    execute_approved: Callable[[ToolCall, Tool, BaseModel, ToolContext], Awaitable[ToolOutput]] | None = field(default=None,repr=False)
    approval_id: UUID | None = None
    resource_snapshot: dict[str, object] = field(default_factory=dict,repr=False)


class ToolOutput(BaseModel):
    data: dict[str, object] = Field(default_factory=dict, repr=False)
    # Server-generated provenance, never accepted as model arguments.
    evidence: list[dict[str, object]] = Field(default_factory=list, repr=False)


class ToolResult(BaseModel):
    call_id: str
    name: str
    ok: bool
    data: dict[str, object] | None = Field(default=None, repr=False)
    error_code: str | None = None
    message: str | None = None
    duration_ms: int
    evidence: list[dict[str, object]] = Field(default_factory=list, repr=False, exclude=True)

    def model_content(self) -> str:
        return json.dumps({"ok": self.ok, "data": self.data, "error":
            {"code":self.error_code, "message":self.message} if not self.ok else None}, ensure_ascii=False)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    execute: Callable[[BaseModel, ToolContext], Awaitable[ToolOutput]]
    permission: str
    timeout_seconds: float = 15
    policy: Literal["read_only","approval_required"] = "read_only"
    idempotent: bool = False
    preflight: Callable[[BaseModel,ToolContext],Awaitable[dict[str,object]]] | None = None
    origin: str = "native"

    def revision(self) -> str:
        value={"definition":self.definition().model_dump(),"policy":self.policy,
               "origin":self.origin,"idempotent":self.idempotent}
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def definition(self) -> ToolDefinition:
        return ToolDefinition(function=FunctionDefinition(name=self.name, description=self.description,
                                                        parameters=self.arguments.model_json_schema()))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError("Duplicate tool name")
        if tool.arguments.model_config.get("extra") != "forbid":
            raise ValueError("Tool arguments must forbid unknown parameters")
        if not inspect.iscoroutinefunction(tool.execute) or not 0 < tool.timeout_seconds <= 120:
            raise ValueError("Tool must be async with a bounded timeout")
        if not tool.permission or {"user_id", "userId", "role", "access_token", "api_key", "password"} & tool.arguments.model_fields.keys():
            raise ValueError("Tool identity/credentials must come from server context")
        if tool.policy not in {"read_only","approval_required"}:
            raise ValueError("Unknown server tool policy")
        tool.definition()  # Validate the provider-facing name and description at startup.
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self, allowed: frozenset[str] | None = None) -> list[ToolDefinition]:
        return [tool.definition() for tool in self._tools.values() if allowed is None or tool.name in allowed]

    def tools(self) -> tuple[Tool,...]:
        return tuple(self._tools.values())

    def clone(self) -> ToolRegistry:
        result=ToolRegistry()
        for tool in self.tools():
            result.register(tool)
        return result


ToolEvent = Callable[[str, dict[str, object]], Awaitable[None]]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, concurrency: int = 3, max_output_chars: int = 16000) -> None:
        if not 1 <= concurrency <= 8 or not 500 <= max_output_chars <= 30000:
            raise ValueError("Invalid executor limits")
        self.registry, self.concurrency, self.max_output_chars = registry, concurrency, max_output_chars

    @observed("tool.execute","tool")
    async def execute(
        self, call: ToolCall, context: ToolContext, emit: ToolEvent | None = None,
    ) -> ToolResult:
        started = time.monotonic()
        output: ToolOutput | None = None
        code, message = None, None
        if emit:
            await emit("tool_start", {"call_id":call.id, "name":call.function.name})
        try:
            tool = self.registry.get(call.function.name)
            if tool is None:
                raise AppError("UNKNOWN_TOOL", "工具未注册或未启用。", 422)
            if tool.permission not in context.permissions:
                raise AppError("TOOL_FORBIDDEN", "当前身份没有此工具的执行权限。", 403)
            if context.allowed_tools is not None and tool.name not in context.allowed_tools:
                raise AppError("TOOL_FORBIDDEN","当前 Skill 未授权此工具。",403)
            try:
                arguments = tool.arguments.model_validate_json(call.function.arguments)
            except (ValueError, ValidationError):
                raise AppError("INVALID_TOOL_ARGUMENTS", "工具参数不符合声明，请按 Schema 调整。", 422) from None
            async with asyncio.timeout(tool.timeout_seconds):
                await context.verify_identity()
                if tool.policy=="approval_required":
                    if context.execute_approved is None:
                        raise AppError("APPROVAL_REQUIRED","需要用户确认。",409)
                    output=await context.execute_approved(call,tool,arguments,context)
                else:
                    if tool.preflight:
                        await tool.preflight(arguments,context)
                    output = await tool.execute(arguments, context)
            serialized = json.dumps(output.data, ensure_ascii=False, allow_nan=False)
            if len(serialized) > self.max_output_chars:
                raise AppError("TOOL_OUTPUT_LIMIT", "工具结果过大，请缩小查询范围或分页。", 422)
        except asyncio.CancelledError:
            if emit:
                with anyio.CancelScope(shield=True):
                    try:
                        await emit("tool_end", {"call_id":call.id, "name":call.function.name, "ok":False,
                            "duration_ms":max(0,int((time.monotonic()-started)*1000)), "error_code":"RUN_CANCELLED"})
                    except Exception as error:
                        logger.error("Tool cancellation trace failed: %s", type(error).__name__)
            raise
        except TimeoutError:
            code, message = "TOOL_TIMEOUT", "工具执行超时，可以缩小查询范围后重试。"
        except AppError as error:
            # Even an accidentally sensitive AppError message is not forwarded.
            code = error.code if error.code in PUBLIC_TOOL_ERRORS else "TOOL_EXECUTION_FAILED"
            message = PUBLIC_TOOL_ERRORS.get(code, "工具暂时无法完成查询。")
        except Exception:
            code, message = "TOOL_EXECUTION_FAILED", "工具暂时无法完成查询。"
        result = ToolResult(call_id=call.id, name=call.function.name, ok=code is None,
            data=output.data if output is not None and code is None else None,
            evidence=output.evidence if output is not None and code is None else [],
            error_code=code, message=message, duration_ms=max(0, int((time.monotonic()-started)*1000)))
        if emit:
            await emit("tool_end", {"call_id":result.call_id, "name":result.name, "ok":result.ok,
                                    "duration_ms":result.duration_ms, "error_code":result.error_code})
        return result

    async def execute_batch(
        self, calls: list[ToolCall], context: ToolContext, emit: ToolEvent | None = None,
    ) -> list[ToolResult]:
        if len(calls) > 16 or len({c.id for c in calls}) != len(calls):
            raise AppError("INVALID_TOOL_BATCH", "工具调用批次超出限制或 ID 重复。", 422)
        semaphore = asyncio.Semaphore(self.concurrency)
        async def execute(call: ToolCall) -> ToolResult:
            async with semaphore:
                return await self.execute(call, context, emit)
        tasks = [asyncio.create_task(execute(call)) for call in calls]
        try:
            return list(await asyncio.gather(*tasks))
        finally:
            # An event/storage failure must not leave sibling tools executing.
            for task in tasks:
                if not task.done():
                    task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
