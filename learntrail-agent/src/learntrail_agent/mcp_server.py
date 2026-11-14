"""Authenticated first-party MCP export of the existing three read-only tools."""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from contextlib import asynccontextmanager

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from learntrail_agent.agent.tools import ToolContext, ToolExecutor, ToolRegistry
from learntrail_agent.clients.tool_models import ToolCall, FunctionCall
from learntrail_agent.errors import AppError

READ_TOOLS=frozenset({"search_knowledge_base","search_articles","get_learning_progress"})
http_context: ContextVar[ToolContext | None]=ContextVar("learntrail_mcp_identity",default=None)


def tool_server(registry: Callable[[],ToolRegistry], context: Callable[[],Awaitable[ToolContext]]) -> Server:
    server=Server("LearnTrail read-only tools")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        await context()
        return [types.Tool(name=t.name,description=t.description,inputSchema=t.arguments.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True))
                for t in registry().tools() if t.name in READ_TOOLS and t.policy=="read_only"]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        ctx=await context()
        if name not in READ_TOOLS:
            return types.CallToolResult(isError=True,content=[types.TextContent(type="text",text="未授权工具")])
        result=await ToolExecutor(registry()).execute(ToolCall(id="mcp-read",function=FunctionCall(
            name=name,arguments=json.dumps(arguments,ensure_ascii=False,allow_nan=False))),ctx)
        if not result.ok:
            return types.CallToolResult(isError=True,content=[types.TextContent(type="text",text=json.dumps({
                "error":{"code":result.error_code,"message":result.message}},ensure_ascii=False))])
        value={"data":result.data,"evidence":result.evidence}
        return types.CallToolResult(structuredContent=value,
            content=[types.TextContent(type="text",text=json.dumps(value,ensure_ascii=False))])
    return server


class AuthenticatedMCP:
    def __init__(self, app) -> None:
        self.app=app
        async def current() -> ToolContext:
            value=http_context.get()
            if value is None:raise AppError("AUTH_REQUIRED","需要登录。",401)
            await value.verify_identity()
            return value
        self.server=tool_server(lambda:app.state.agent_tools.registry(),current)
        self.manager=StreamableHTTPSessionManager(self.server,stateless=True,json_response=True,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1:*","localhost:*","[::1]:*"],
                allowed_origins=["http://localhost:*","http://127.0.0.1:*"]))

    @asynccontextmanager
    async def lifespan(self):
        ready=asyncio.get_running_loop().create_future()
        stop=asyncio.Event()
        async def own_manager():
            try:
                async with self.manager.run():
                    ready.set_result(None)
                    await stop.wait()
            except BaseException as error:
                if not ready.done():ready.set_exception(error)
                raise
        task=asyncio.create_task(own_manager())
        try:
            await ready
            yield
        finally:
            stop.set()
            await task

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"]!="http":return
        request=Request(scope)
        try:
            parts=request.headers.get("authorization","").split()
            if len(parts)!=2 or parts[0].lower()!="bearer":raise AppError("AUTH_REQUIRED","需要 Bearer 身份。",401)
            identity=await self.app.state.agent_tools.context(parts[1])
        except AppError as error:
            await JSONResponse(error.payload(),status_code=error.status_code,
                headers={"WWW-Authenticate":"Bearer"} if error.status_code==401 else None)(scope,receive,send)
            return
        saved=http_context.set(identity)
        try:await self.manager.handle_request(scope,receive,send)
        finally:http_context.reset(saved)


async def run_stdio() -> None:
    # This trusted first-party process binds one existing JWT, never accepts userId.
    from learntrail_agent.main import create_app
    token=os.environ.get("LEARNTRAIL_MCP_ACCESS_TOKEN","")
    if not token:raise RuntimeError("First-party stdio MCP requires a private access token environment variable")
    app=create_app()
    async with app.router.lifespan_context(app):
        async def current() -> ToolContext:
            return await app.state.agent_tools.context(token)
        server=tool_server(lambda:app.state.agent_tools.registry(),current)
        async with stdio_server() as (read,write):
            await server.run(read,write,server.create_initialization_options())


if __name__=="__main__":
    asyncio.run(run_stdio())
