"""Native HTTPX tool streams, including fragmented Chinese and custom Graph events."""

import asyncio
import json

import httpx
import pytest

from learntrail_agent.agent.loop import AgentLoop, AgentState
from learntrail_agent.agent.tools import ToolExecutor
from learntrail_agent.clients.llm import LLMClient
from learntrail_agent.clients.tool_models import ToolChatRequest, ToolMessage
from learntrail_agent.errors import AppError
from test_tool_core import registry, context


def frame(delta=None, finish=None, usage=None):
    value={"id":"stream-id","model":"test-model","choices":[] if usage is not None else [
        {"index":0,"delta":delta or {},"finish_reason":finish}]}
    if usage is not None:value["usage"]=usage
    return 'data: '+json.dumps(value,ensure_ascii=False)+'\n\n'


class Packets(httpx.AsyncByteStream):
    def __init__(self, wire: str, delay: float = 0):
        self.wire,self.delay,self.closed=wire,delay,False
    async def __aiter__(self):
        data=self.wire.encode()
        for offset in range(0,len(data),7):
            if self.delay:await asyncio.sleep(self.delay)
            yield data[offset:offset+7]
    async def aclose(self):self.closed=True


def request():
    return ToolChatRequest(messages=[ToolMessage(role="user",content="问题")],tools=registry().definitions())


@pytest.mark.asyncio
async def test_tool_fragments_multiple_ids_reasoning_and_usage_are_assembled(settings,respx_mock):
    wire=frame({"reasoning_content":"PRIVATE-THOUGHT","tool_calls":[
        {"index":0,"id":"call-a","type":"function","function":{"name":"echo","arguments":'{"text":"你'}},
        {"index":1,"id":"call-b","type":"function","function":{"name":"echo","arguments":'{"text":"再'}}]})
    wire+=frame({"tool_calls":[{"index":1,"function":{"arguments":'见"}'}},
                               {"index":0,"function":{"arguments":'好"}'}}]})
    wire+=frame(finish="tool_calls")+frame(usage={"total_tokens":12})+'data: [DONE]\n\n'
    packets=Packets(wire)
    route=respx_mock.post("https://model.example.test/v1/chat/completions").mock(return_value=httpx.Response(
        200,headers={"content-type":"text/event-stream"},stream=packets))
    async with httpx.AsyncClient() as http:
        events=[e async for e in LLMClient(http,settings).stream_with_tools(request())]
    assert [kind for kind,_ in events]==["complete"]
    final=events[-1][1]
    assert [json.loads(c.function.arguments)["text"] for c in final.tool_calls]==["你好","再见"]
    assert [c.id for c in final.tool_calls]==["call-a","call-b"] and final.usage.total_tokens==12
    assert final.assistant_message().wire()["reasoning_content"]=="PRIVATE-THOUGHT"
    assert "PRIVATE-THOUGHT" not in final.model_dump_json()
    assert json.loads(route.calls[0].request.content)["stream"] is True and packets.closed


@pytest.mark.asyncio
async def test_content_is_delivered_before_stream_ends(settings,respx_mock):
    packets=Packets(frame({"content":"你好"})+frame({"content":"，学途"})+frame(finish="stop")+'data: [DONE]\n\n')
    respx_mock.post("https://model.example.test/v1/chat/completions").mock(return_value=httpx.Response(
        200,headers={"content-type":"text/event-stream"},stream=packets))
    async with httpx.AsyncClient() as http:
        events=LLMClient(http,settings).stream_with_tools(request())
        first=await anext(events)
        assert first==("delta",{"content":"你好"}) and not packets.closed
        rest=[e async for e in events]
    assert rest[-1][0]=="complete" and rest[-1][1].content=="你好，学途"
    assert packets.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("wire,code", [
    (frame({"content":"half"}),"LLM_STREAM_INTERRUPTED"),
    (frame({"tool_calls":[{"index":16}]}),"LLM_INVALID_RESPONSE"),
    (frame({"tool_calls":[{"index":0,"function":{"arguments":{}}}]}),"LLM_INVALID_RESPONSE"),
    (frame(finish="tool_calls")+'data: [DONE]\n\n',"LLM_INVALID_RESPONSE"),
])
async def test_invalid_tool_stream_is_closed_and_rejected(settings,respx_mock,wire,code):
    packets=Packets(wire)
    respx_mock.post("https://model.example.test/v1/chat/completions").mock(return_value=httpx.Response(
        200,headers={"content-type":"text/event-stream"},stream=packets))
    async with httpx.AsyncClient() as http:
        with pytest.raises(AppError) as caught:
            _=[e async for e in LLMClient(http,settings).stream_with_tools(request())]
    assert caught.value.code==code and packets.closed


@pytest.mark.asyncio
async def test_real_graph_custom_stream_replays_tool_result_and_emits_actual_content(settings,respx_mock):
    wires=[frame({"tool_calls":[{"index":0,"id":"call-a","function":{"name":"echo","arguments":'{"text":"你好"}'}}]})+
        frame(finish="tool_calls")+frame(usage={"total_tokens":3})+'data: [DONE]\n\n',
        frame({"content":"工具返回"})+frame({"content":"你好"})+frame(finish="stop")+frame(usage={"total_tokens":5})+'data: [DONE]\n\n']
    sent=[]
    def respond(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200,headers={"content-type":"text/event-stream"},stream=Packets(wires[len(sent)-1]))
    respx_mock.post("https://model.example.test/v1/chat/completions").mock(side_effect=respond)
    observed=[]
    async def emit(kind,data):observed.append((kind,data))
    async with httpx.AsyncClient() as http:
        tools=registry()
        result=await AgentLoop(LLMClient(http,settings),tools,ToolExecutor(tools)).run(
            "请查",context(),AgentState(),emit,stream_tokens=True)
    assert result.answer=="工具返回你好" and result.usage.total_tokens==8
    assert [e[1]["content"] for e in observed if e[0]=="delta"]==["工具返回","你好"]
    assert json.loads(sent[1]["messages"][-1]["content"])["data"]["text"]=="你好"
    assert {e[0] for e in observed}>={"node","tool_start","tool_end","model_start","model_end"}
