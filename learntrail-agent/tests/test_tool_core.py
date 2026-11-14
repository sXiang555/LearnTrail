"""Protocol and executor tests; model HTTP is mocked, no Java or paid calls."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from learntrail_agent.agent.tools import Tool, ToolContext, ToolExecutor, ToolOutput, ToolRegistry
from learntrail_agent.agent.loop import AgentLoop, AgentState
from learntrail_agent.clients.llm import LLMClient
from learntrail_agent.clients.tool_models import (
    FunctionCall, ToolCall, ToolChatRequest, ToolChatResponse, ToolMessage,
)
from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings
from learntrail_agent.schemas import Usage


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=50)


async def echo(args: BaseModel, context: ToolContext) -> ToolOutput:
    return ToolOutput(data={"text":args.text})


def context() -> ToolContext:
    return ToolContext(41, SecretStr("test-context-private-token"), frozenset({"read"}), AsyncMock())


def registry(handler=echo, timeout: float = 1) -> ToolRegistry:
    result = ToolRegistry()
    result.register(Tool("echo", "Echo bounded text", EchoArgs, handler, "read", timeout))
    return result


def call(name: str = "echo", arguments: str = '{"text":"你好"}', identifier: str = "call-1") -> ToolCall:
    return ToolCall(id=identifier, function=FunctionCall(name=name, arguments=arguments))


def test_registry_generates_schema_and_rejects_duplicates_identity_and_sync() -> None:
    registered = registry()
    definition = registered.definitions()[0].model_dump()
    assert definition["function"]["parameters"]["additionalProperties"] is False
    assert "text" in definition["function"]["parameters"]["required"]
    with pytest.raises(ValueError):
        registered.register(Tool("echo", "duplicate", EchoArgs, echo, "read"))
    with pytest.raises(ValueError):
        registered.register(Tool("sync", "sync", EchoArgs, lambda a,c:None, "read"))
    class IdentityArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        user_id: int
    with pytest.raises(ValueError):
        registered.register(Tool("identity", "unsafe", IdentityArgs, echo, "read"))


@pytest.mark.asyncio
async def test_executor_success_uses_server_context_and_omits_credentials() -> None:
    ctx = context()
    result = await ToolExecutor(registry()).execute(call(), ctx)
    assert result.ok and result.data == {"text":"你好"} and result.duration_ms >= 0
    ctx.verify_identity.assert_awaited_once()
    assert "test-context-private-token" not in repr(ctx) + result.model_dump_json() + result.model_content()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,code", [(call("unknown"), "UNKNOWN_TOOL"),
    (call(arguments='{"text":"x","userId":99}'), "INVALID_TOOL_ARGUMENTS"),
    (call(arguments='{"text":123}'), "INVALID_TOOL_ARGUMENTS"),
    (call(arguments='not-json-secret'), "INVALID_TOOL_ARGUMENTS")])
async def test_unknown_and_invalid_arguments_are_controlled(tool: ToolCall, code: str) -> None:
    result = await ToolExecutor(registry()).execute(tool, context())
    assert not result.ok and result.error_code == code
    assert "not-json-secret" not in result.model_content()


@pytest.mark.asyncio
async def test_permission_check_precedes_execution() -> None:
    handler = AsyncMock(return_value=ToolOutput())
    ctx = ToolContext(41, SecretStr("private"), frozenset(), AsyncMock())
    result = await ToolExecutor(registry(handler)).execute(call(), ctx)
    assert result.error_code == "TOOL_FORBIDDEN"
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_and_internal_exception_do_not_leak() -> None:
    async def slow(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        await asyncio.Event().wait()
    result = await ToolExecutor(registry(slow, .01)).execute(call(), context())
    assert result.error_code == "TOOL_TIMEOUT"
    async def failed(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        raise RuntimeError("PRIVATE-SQL-AND-PASSWORD")
    result = await ToolExecutor(registry(failed)).execute(call(), context())
    assert result.error_code == "TOOL_EXECUTION_FAILED" and "PRIVATE" not in result.model_content()


@pytest.mark.asyncio
async def test_parallel_batch_is_bounded_and_preserves_call_order() -> None:
    active, maximum = 0, 0
    async def handler(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1
        return ToolOutput(data={"text":args.text})
    calls = [call(arguments=json.dumps({"text":str(i)}), identifier=f"call-{i}") for i in range(5)]
    results = await ToolExecutor(registry(handler), concurrency=2).execute_batch(calls, context())
    assert maximum == 2 and [r.data["text"] for r in results] == list(map(str,range(5)))
    with pytest.raises(AppError):
        await ToolExecutor(registry(handler)).execute_batch([calls[0],calls[0]], context())


@pytest.mark.asyncio
async def test_function_call_protocol_preserves_multiple_ids_and_private_provider_state(settings: Settings, respx_mock) -> None:
    definitions = registry().definitions()
    route = respx_mock.post("https://model.example.test/v1/chat/completions").respond(200, json={
        "id":"provider-id", "model":"test-model", "choices":[{"index":0,"finish_reason":"tool_calls", "message":{
            "content":None, "reasoning_content":"private-provider-state", "tool_calls":[
                call(identifier="one").model_dump(), call(identifier="two").model_dump()]}}],
        "usage":{"total_tokens":11}})
    async with httpx.AsyncClient() as http:
        client = LLMClient(http,settings)
        request = ToolChatRequest(messages=[ToolMessage(role="user",content="测试")], tools=definitions)
        response = await client.complete_with_tools(request)
        assert len(response.tool_calls) == 2 and response.usage.total_tokens == 11
        assert "private-provider-state" not in response.model_dump_json() + repr(response)
        messages = [*request.messages, response.assistant_message(),
                    ToolMessage(role="tool",tool_call_id="one",content='{"ok":true}'),
                    ToolMessage(role="tool",tool_call_id="two",content='{"ok":true}')]
        follow = ToolChatRequest(messages=messages,tools=definitions,tool_choice="none")
        payload = client._payload(follow,stream=False)
        assert payload["messages"][1]["reasoning_content"] == "private-provider-state"
        assert [m["tool_call_id"] for m in payload["messages"] if m["role"]=="tool"] == ["one","two"]
        with pytest.raises(ValidationError):
            ToolChatRequest(messages=messages[:-1],tools=definitions)


@pytest.mark.asyncio
async def test_duplicate_provider_ids_are_rejected(settings: Settings, respx_mock) -> None:
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(200, json={
        "id":"provider-id","model":"test-model","choices":[{"index":0,"finish_reason":"tool_calls",
            "message":{"tool_calls":[call().model_dump(),call().model_dump()]}}]})
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            await LLMClient(http,settings).complete_with_tools(ToolChatRequest(
                messages=[ToolMessage(role="user",content="测试")],tools=registry().definitions()))
    assert caught.value.code == "LLM_INVALID_RESPONSE"


def reply(content: str | None = None, calls: list[ToolCall] | None = None, tokens: int = 1) -> ToolChatResponse:
    return ToolChatResponse(id="provider-id", model="test", content=content, tool_calls=calls or [],
        finish_reason="tool_calls" if calls else "stop", usage=Usage(prompt_tokens=tokens, completion_tokens=1,
                                                                     total_tokens=tokens+1))


@pytest.mark.asyncio
async def test_loop_retries_invalid_arguments_and_accumulates_all_model_usage() -> None:
    llm = AsyncMock(spec=LLMClient)
    llm.complete_with_tools.side_effect = [reply(calls=[call(arguments='{"text":123}',identifier="one")],tokens=2),
        reply(calls=[call(identifier="two")],tokens=3), reply(content="查询完成",tokens=4)]
    tools = registry();state = AgentState()
    result = await AgentLoop(llm,tools,ToolExecutor(tools)).run("请查询",context(),state)
    assert result.answer == "查询完成" and result.model_calls == 3 and result.tool_calls == 2
    assert result.usage.total_tokens == 12 and result.usage_complete
    third_request = llm.complete_with_tools.await_args_list[2].args[0]
    outputs = [json.loads(m.content) for m in third_request.messages if m.role == "tool"]
    assert outputs[0]["error"]["code"] == "INVALID_TOOL_ARGUMENTS" and outputs[1]["ok"] is True
    assert [e["kind"] for e in state.trace].count("model_end") == 3
    assert [e["kind"] for e in state.trace].count("tool_result") == 2
    assert "test-context-private-token" not in json.dumps(state.trace,ensure_ascii=False)
    assert state.trace[-1]["kind"] == "run_end" and state.trace[-1]["payload"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_loop_can_answer_without_tools_and_missing_usage_stays_unknown() -> None:
    llm = AsyncMock(spec=LLMClient)
    direct = reply(content="你好");direct.usage=None
    llm.complete_with_tools.return_value = direct
    tools = registry();state = AgentState()
    result = await AgentLoop(llm,tools,ToolExecutor(tools)).run("你好",context(),state)
    assert result.tool_calls == 0 and result.usage is None and not result.usage_complete


@pytest.mark.asyncio
async def test_loop_enforces_round_and_total_tool_limits() -> None:
    for rounds, maximum, expected in [(2,10,"AGENT_ROUND_LIMIT"),(5,1,"AGENT_TOOL_LIMIT")]:
        llm = AsyncMock(spec=LLMClient)
        llm.complete_with_tools.side_effect = [reply(calls=[call(identifier=str(i))]) for i in range(6)]
        tools = registry();state = AgentState()
        with pytest.raises(AppError) as caught:
            await AgentLoop(llm,tools,ToolExecutor(tools),max_rounds=rounds,max_tool_calls=maximum).run("循环",context(),state)
        assert caught.value.code == expected and state.status == "failed"
        assert state.error_code == expected and state.model_calls <= rounds


@pytest.mark.asyncio
async def test_loop_total_deadline_cancels_inflight_tool() -> None:
    stopped = asyncio.Event()
    async def slow(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    llm = AsyncMock(spec=LLMClient);llm.complete_with_tools.return_value = reply(calls=[call()])
    tools=registry(slow);state=AgentState()
    with pytest.raises(AppError) as caught:
        await AgentLoop(llm,tools,ToolExecutor(tools),timeout_seconds=.03).run("超时",context(),state)
    assert caught.value.code == "AGENT_TIMEOUT" and stopped.is_set()
    assert any(e["payload"].get("error_code")=="RUN_CANCELLED" for e in state.trace if e["kind"]=="tool_end")


@pytest.mark.asyncio
async def test_loop_rejects_refusal_and_incomplete_final_answer() -> None:
    for value, code in [(reply(content="部分回答"),"AGENT_INCOMPLETE_ANSWER"),
                        (reply(content="拒绝"),"AGENT_MODEL_REFUSED")]:
        if code == "AGENT_INCOMPLETE_ANSWER":value.finish_reason="length"
        else:value.refusal="refused"
        llm=AsyncMock(spec=LLMClient);llm.complete_with_tools.return_value=value
        tools=registry();state=AgentState()
        with pytest.raises(AppError) as caught:
            await AgentLoop(llm,tools,ToolExecutor(tools)).run("问题",context(),state)
        assert caught.value.code == code and state.status=="failed"


@pytest.mark.asyncio
async def test_tool_errors_do_not_forward_sensitive_app_error_text() -> None:
    async def failed(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        raise AppError("PRIVATE_CODE", "PASSWORD-INTERNAL-DETAIL", 500)
    result = await ToolExecutor(registry(failed)).execute(call(),context())
    assert result.error_code=="TOOL_EXECUTION_FAILED" and "PASSWORD" not in result.model_content()


@pytest.mark.asyncio
async def test_provider_content_filter_preserves_refusal_and_usage(settings: Settings, respx_mock) -> None:
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(200,json={
        "id":"blocked","model":"test","choices":[{"index":0,"finish_reason":"content_filter",
            "message":{"content":None}}],"usage":{"total_tokens":7}})
    tools=registry();state=AgentState()
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            await AgentLoop(LLMClient(http,settings),tools,ToolExecutor(tools)).run("question",context(),state)
    assert caught.value.code=="AGENT_MODEL_REFUSED" and state.usage().total_tokens==7
    assert state.trace[-1]["payload"]["error_code"]=="AGENT_MODEL_REFUSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,code", [("unknown","UNKNOWN_TOOL"),
    ("failure","TOOL_EXECUTION_FAILED"),("timeout","TOOL_TIMEOUT")])
async def test_loop_returns_tool_failures_to_model_and_can_finish(scenario: str, code: str) -> None:
    async def handler(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        if scenario=="timeout":
            await asyncio.Event().wait()
        raise RuntimeError("PRIVATE-INTERNAL-DETAIL")
    tools=registry(handler,timeout=.02)
    llm=AsyncMock(spec=LLMClient)
    llm.complete_with_tools.side_effect=[reply(calls=[call(name="missing" if scenario=="unknown" else "echo")]),
                                        reply(content="查询未能完成，不能编造结果。")]
    state=AgentState()
    result=await AgentLoop(llm,tools,ToolExecutor(tools)).run("查询",context(),state)
    returned=llm.complete_with_tools.await_args_list[1].args[0].messages[-1]
    assert json.loads(returned.content)["error"]["code"]==code
    assert result.model_calls==2 and result.tool_calls==1
    assert "PRIVATE-INTERNAL-DETAIL" not in json.dumps(state.trace,ensure_ascii=False)


@pytest.mark.asyncio
async def test_loop_parallel_tools_are_all_returned_before_next_model_turn() -> None:
    entered=0
    both_entered=asyncio.Event()
    async def handler(args: BaseModel, ctx: ToolContext) -> ToolOutput:
        nonlocal entered
        entered+=1
        if entered==2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(),1)
        return ToolOutput(data={"text":args.text})
    tools=registry(handler);llm=AsyncMock(spec=LLMClient)
    llm.complete_with_tools.side_effect=[reply(calls=[call(identifier="a"),call(identifier="b")]),
                                        reply(content="两个独立查询已完成。")]
    state=AgentState()
    result=await AgentLoop(llm,tools,ToolExecutor(tools,concurrency=2)).run("并行查询",context(),state)
    messages=llm.complete_with_tools.await_args_list[1].args[0].messages
    assert result.tool_calls==2 and [m.tool_call_id for m in messages if m.role=="tool"]==["a","b"]
    assert all(json.loads(m.content)["ok"] for m in messages if m.role=="tool")


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [{"id":"x","type":"function"},
    {"id":"x","type":"function","function":{"name":"echo","arguments":{}}}])
async def test_malformed_provider_tool_call_never_reaches_executor(settings: Settings, respx_mock, malformed: dict) -> None:
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(200,json={
        "id":"invalid","model":"test","choices":[{"index":0,"finish_reason":"tool_calls",
            "message":{"tool_calls":[malformed]}}]})
    handler=AsyncMock(return_value=ToolOutput());tools=registry(handler)
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            await AgentLoop(LLMClient(http,settings),tools,ToolExecutor(tools)).run("查询",context(),AgentState())
    assert caught.value.code=="LLM_INVALID_RESPONSE"
    handler.assert_not_called()
