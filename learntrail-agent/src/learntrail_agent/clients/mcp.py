"""Explicitly trusted MCP profiles adapted into the existing Tool Registry."""

import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager, AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator, field_validator

from learntrail_agent.agent.advanced_models import canonical, digest
from learntrail_agent.agent.tools import Tool, ToolContext, ToolOutput, ToolRegistry
from learntrail_agent.errors import AppError


def controlled_error(error: BaseException) -> AppError | None:
    if isinstance(error,AppError):return error
    if isinstance(error,BaseExceptionGroup):
        for nested in error.exceptions:
            found=controlled_error(nested)
            if found:return found
    return None


class MCPProfile(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    id: str=Field(pattern=r"^[a-z][a-z0-9_]{0,15}$")
    trusted: Literal[True]
    transport: Literal["stdio","http"]
    allow_tools: dict[str,Literal["read_only","approval_required"]]=Field(min_length=1,max_length=8)
    command: str | None=None
    args: list[str]=Field(default_factory=list,max_length=16)
    url: str | None=None
    auth: Literal["none","learntrail"]="none"
    token_env: str | None=Field(default=None,pattern=r"^LEARNTRAIL_MCP_[A-Z0-9_]+_TOKEN$")
    timeout_seconds: int=Field(default=15,ge=1,le=30)

    @field_validator("trusted",mode="before")
    @classmethod
    def explicit_trust(cls,value):
        if value is not True:raise ValueError("Explicit server trust is required")
        return value

    @model_validator(mode="after")
    def endpoint(self) -> "MCPProfile":
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,44}",n) or len(f"mcp_{self.id}_{n}")>64 for n in self.allow_tools):
            raise ValueError("MCP allowlist names exceed limits")
        if any(len(a)>500 or "\x00" in a for a in self.args):raise ValueError("Invalid stdio argument")
        if self.transport=="stdio":
            if not self.command or not Path(self.command).is_absolute() or not os.access(self.command,os.X_OK) or self.url or self.token_env:
                raise ValueError("Stdio needs an explicitly trusted executable, not a shell or URL")
            if Path(self.command).name in {"sh","bash","zsh","cmd","powershell"} or any(a in {"-c","-e","--eval"} for a in self.args):
                raise ValueError("Shell/eval MCP commands are disabled")
        else:
            u=urlsplit(self.url or "")
            if (u.scheme not in {"http","https"} or not u.hostname or u.username or u.password or u.query or u.fragment
                    or u.scheme=="http" and u.hostname not in {"127.0.0.1","localhost","::1"} or self.command or self.args):
                raise ValueError("MCP HTTP endpoint must be explicit HTTPS or loopback HTTP without credentials")
        return self


class RemoteArguments(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)

    def model_dump(self, *args, **kwargs):
        kwargs.setdefault("exclude_unset",True)
        return super().model_dump(*args,**kwargs)


def arguments_model(profile: str, name: str, schema: dict) -> type[BaseModel]:
    schema=json.loads(canonical(schema))
    if len(canonical(schema))>24000 or schema.get("type")!="object" or schema.get("additionalProperties") not in (None,False):
        raise ValueError("Only bounded explicit object schemas are supported")
    schema["additionalProperties"]=False
    jsonschema.Draft202012Validator.check_schema(schema)
    forbidden={"user","userid","owner","ownerid","accountid","identity","principal","credentials",
               "role","roles","roleid","accesstoken","apikey","password","authorization","databaseurl"}
    def inspect(value: Any, depth: int = 0) -> None:
        if depth>10:raise ValueError("MCP schema too deep")
        if isinstance(value,dict):
            kind=value.get("type")
            if kind=="object" or isinstance(kind,list) and "object" in kind:
                if value.get("additionalProperties") not in (None,False):
                    raise ValueError("Dynamic nested MCP arguments are unsupported")
                value["additionalProperties"]=False
            if "$ref" in value:
                # Avoid remote fetching and recursive object construction entirely.
                raise ValueError("MCP references are unsupported; publish an inline schema")
            if "pattern" in value and value["pattern"] not in {r"^[a-z0-9-]{1,80}$",r"^[A-Za-z0-9_-]+$"}:
                raise ValueError("Unreviewed remote regex is unsupported")
            properties=value.get("properties",{})
            # A property map may itself contain a field named "properties".
            for k,v in (properties.items() if isinstance(properties,dict) else []):
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}",k):raise ValueError("Unsupported argument name")
                if k.startswith(("model_","__")):raise ValueError("Reserved argument name")
                if re.sub(r"[^A-Za-z0-9]","",k).lower() in forbidden:raise ValueError("Identity/credentials are runtime-only")
            for v in value.values():inspect(v,depth+1)
        elif isinstance(value,list):
            if len(value)>64:raise ValueError("MCP schema array too large")
            for v in value:inspect(v,depth+1)
    inspect(schema)
    properties=schema.get("properties",{})
    if len(properties)>24:raise ValueError("Too many MCP fields")
    jsonschema.Draft202012Validator.check_schema(schema)
    validator=jsonschema.Draft202012Validator(schema)
    def argument_keys(value: Any, depth: int = 0) -> None:
        if depth>10:raise ValueError("MCP arguments too deep")
        if isinstance(value,dict):
            for key,item in value.items():
                if re.sub(r"[^A-Za-z0-9]","",key).lower() in forbidden:
                    raise ValueError("Identity/credentials are runtime-only")
                argument_keys(item,depth+1)
        elif isinstance(value,list):
            if len(value)>64:raise ValueError("MCP argument array too large")
            for item in value:argument_keys(item,depth+1)
    @model_validator(mode="before")
    def validate(cls,value):
        if not isinstance(value,dict) or len(canonical(value))>8000:raise ValueError("MCP arguments too large")
        argument_keys(value)
        try:validator.validate(value)
        except jsonschema.ValidationError:raise ValueError("Arguments do not match MCP schema") from None
        return value
    fields={k:(Any,... if k in schema.get("required",[]) else None) for k in properties}
    model=create_model(f"MCP_{profile}_{name}",__base__=RemoteArguments,__validators__={"validate_remote":validate},**fields)
    model.model_json_schema=classmethod(lambda cls,*a,**kw:json.loads(canonical(schema)))
    return model


class MCPClient:
    def __init__(self, profiles: list[MCPProfile], first_party_url: str) -> None:
        if len(profiles)>3 or len({p.id for p in profiles})!=len(profiles):raise ValueError("MCP profile limit/duplicate")
        self.profiles={p.id:p for p in profiles}
        self.first_party_url=first_party_url
        for p in profiles:
            if p.auth=="learntrail":
                trusted=(p.transport=="http" and p.url==first_party_url and urlsplit(p.url).hostname in {"127.0.0.1","localhost"}
                    or p.transport=="stdio" and Path(p.command).resolve()==Path(sys.executable).resolve()
                    and p.args==["-m","learntrail_agent.mcp_server"])
                if not trusted or p.token_env:raise ValueError("User JWT can only reach the exact first-party MCP server")

    @classmethod
    def from_file(cls, path: Path | None, first_party_url: str) -> "MCPClient":
        if path is None:return cls([],first_party_url)
        try:
            raw=path.read_bytes()
            if len(raw)>16000:raise ValueError()
            values=json.loads(raw)
            if not isinstance(values,list):raise ValueError()
            return cls([MCPProfile.model_validate(v) for v in values],first_party_url)
        except (OSError,ValueError):
            raise RuntimeError("Invalid trusted MCP server configuration") from None

    @asynccontextmanager
    async def session(self, profile: MCPProfile, context: ToolContext):
        headers={};env={"LANGSMITH_TRACING":"false","LANGCHAIN_TRACING_V2":"false"}
        if profile.auth=="learntrail":
            await context.verify_identity()
            if profile.transport=="http":headers["Authorization"]="Bearer "+context.access_token.get_secret_value()
            else:env["LEARNTRAIL_MCP_ACCESS_TOKEN"]=context.access_token.get_secret_value()
        elif profile.token_env:
            token=os.environ.get(profile.token_env,"")
            if not token or any(ord(c)<33 or ord(c)>126 for c in token):
                raise AppError("MCP_UNAVAILABLE","缺少专用于此 MCP Server 的凭据。",503)
            headers["Authorization"]="Bearer "+token
        async with AsyncExitStack() as stack:
            if profile.transport=="stdio":
                transport=stdio_client(StdioServerParameters(command=profile.command,args=profile.args,env=env))
            else:
                http=await stack.enter_async_context(httpx.AsyncClient(headers=headers,
                    timeout=httpx.Timeout(profile.timeout_seconds),follow_redirects=False,trust_env=False))
                transport=streamable_http_client(profile.url,http_client=http)
            streams=await stack.enter_async_context(transport)
            session=await stack.enter_async_context(ClientSession(streams[0],streams[1],
                read_timeout_seconds=timedelta(seconds=profile.timeout_seconds)))
            await session.initialize()
            yield session

    @staticmethod
    async def listed(session: ClientSession) -> list:
        tools=[];cursor=None
        for _ in range(3):
            page=await session.list_tools(cursor=cursor);tools.extend(page.tools)
            if len(tools)>16:raise ValueError("Tool discovery limit")
            cursor=page.nextCursor
            if not cursor:break
        if cursor:raise ValueError("Tool discovery pagination limit")
        if len({t.name for t in tools})!=len(tools):raise ValueError("Duplicate MCP tools")
        return tools

    async def discover(self, profile: MCPProfile, context: ToolContext) -> list:
        try:
            async with asyncio.timeout(profile.timeout_seconds):
                async with self.session(profile,context) as session:
                    return await self.listed(session)
        except asyncio.CancelledError:raise
        except AppError:raise
        except Exception as error:
            known=controlled_error(error)
            if known:raise known from None
            raise AppError("MCP_UNAVAILABLE","MCP 初始化或发现失败。",503,True) from None

    async def invoke(self, profile: MCPProfile, name: str, arguments: dict, context: ToolContext,
                     schema_hash: str, read_only: bool) -> ToolOutput:
        if name not in profile.allow_tools:raise AppError("TOOL_FORBIDDEN","未授权 MCP 工具。",403)
        try:
            async with asyncio.timeout(profile.timeout_seconds):
                for attempt in range(2 if read_only else 1):
                    try:
                        async with self.session(profile,context) as session:
                            items=await self.listed(session)
                            current=next((t for t in items if t.name==name),None)
                            if current is None or digest(current.model_dump(mode="json"))!=schema_hash:
                                raise AppError("MCP_SCHEMA_CHANGED","MCP 定义已变化。",409)
                            result=await session.call_tool(name,arguments,meta={"operation_id":str(context.approval_id)} if context.approval_id else None)
                            if result.isError:raise AppError("MCP_TOOL_ERROR","远端工具执行失败。",502)
                            value=result.structuredContent
                            if value is None:
                                if any(c.type!="text" for c in result.content):raise ValueError("Only text MCP results are supported")
                                value={"text":"\n".join(c.text for c in result.content)}
                            if len(canonical(value))>16000:raise AppError("TOOL_OUTPUT_LIMIT","MCP 返回过大。",422)
                            if profile.auth=="learntrail":
                                output=ToolOutput.model_validate(value)
                                if context.verify_sources:await context.verify_sources(output.evidence)
                                return output
                            return ToolOutput(data={"server":profile.id,"result":value},evidence=[{
                                "kind":"mcp_public_observation","server":profile.id,"profile":digest(profile.model_dump()),
                                "tool":name,"expires_at":time.time()+300}])
                    except AppError:raise
                    except asyncio.CancelledError:raise
                    except Exception as error:
                        known=controlled_error(error)
                        if known:raise known from None
                        if attempt+1 >= (2 if read_only else 1):raise
        except asyncio.CancelledError:raise
        except TimeoutError:
            raise AppError("MCP_TIMEOUT","MCP 请求超过时限。",504,True) from None
        except AppError:raise
        except Exception as error:
            known=controlled_error(error)
            if known:raise known from None
            raise AppError("MCP_UNAVAILABLE","MCP 连接中断或响应不合法。",503,True) from None

    async def register(self, registry: ToolRegistry, context: ToolContext) -> None:
        for profile in self.profiles.values():
            discovered=await self.discover(profile,context)
            for item in discovered:
                if item.name not in profile.allow_tools:continue
                readonly=item.annotations is not None and item.annotations.readOnlyHint is True
                policy=profile.allow_tools[item.name]
                if policy=="read_only" and not readonly:
                    raise AppError("MCP_POLICY_INVALID","未确认只读的远端工具不能以只读策略注册。",503)
                try:model=arguments_model(profile.id,item.name,item.inputSchema)
                except (ValueError,jsonschema.SchemaError):
                    raise AppError("MCP_SCHEMA_INVALID","远端工具 Schema 不在受支持的安全子集中。",503) from None
                schema_hash=digest(item.model_dump(mode="json"))
                remote=[t for t in registry.tools() if t.origin.startswith("mcp:")]
                if len(remote)>=16 or sum(len(canonical(t.arguments.model_json_schema())) for t in remote)+len(canonical(item.inputSchema))>50000:
                    raise AppError("MCP_SCHEMA_INVALID","MCP 工具数量或 Schema 总量超限。",503)
                def handler(p=profile,n=item.name,h=schema_hash,ro=policy=="read_only"):
                    async def execute(args,ctx):return await self.invoke(p,n,args.model_dump(mode="json"),ctx,h,ro)
                    return execute
                registry.register(Tool(f"mcp_{profile.id}_{item.name}",f"已授权 MCP {profile.id}: "+(item.description or item.name)[:1000],
                    model,handler(),f"mcp:{profile.id}",min(profile.timeout_seconds+2,120),policy=policy,
                    origin=f"mcp:{profile.id}:{digest(profile.model_dump())}:{schema_hash}"))

    def verify_observation(self, context: ToolContext, item: dict) -> None:
        p=self.profiles.get(item["server"])
        if (p is None or f"mcp:{p.id}" not in context.permissions or digest(p.model_dump())!=item["profile"]
                or item["tool"] not in p.allow_tools or time.time()>=item["expires_at"]):
            raise AppError("AGENT_SOURCE_CHANGED","远端工具授权或观测已失效。",409)
