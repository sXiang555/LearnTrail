"""Local-only MCP transport fixture and optional safe outline demonstration."""

import argparse
import asyncio
import os
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations
from pydantic import Field, BaseModel


class OutlinePreview(BaseModel):
    topic: str
    sections: list[str]
    preview_only: bool = True


class DemoReceipt(BaseModel):
    operation_id: str
    note: str
    demo_receipt: bool = True
    real_content_published: bool = False


class EnvironmentFlags(BaseModel):
    LLM_API_KEY: bool
    DATABASE_URL: bool
    LEARNTRAIL_MCP_ACCESS_TOKEN: bool


def create_demo(port: int = 8121, disconnect_once_file: Path | None = None) -> FastMCP:
    server=FastMCP("LearnTrail local MCP demo",host="127.0.0.1",port=port,
                   stateless_http=True,json_response=True)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True))
    async def preview_learning_outline(topic: Annotated[str,Field(min_length=2,max_length=200)],
                                       delay_ms: Annotated[int,Field(ge=0,le=2000)] = 0) -> OutlinePreview:
        """仅生成本地演示大纲预览，不读取用户身份或修改业务。"""
        if disconnect_once_file and not disconnect_once_file.exists():
            disconnect_once_file.write_text("one intentional test disconnect")
            os._exit(73)
        if topic=="demo-error":raise ValueError("Controlled demo tool failure")
        if delay_ms:await asyncio.sleep(delay_ms/1000)
        return OutlinePreview(topic=topic,sections=["明确学习目标","核对知识依据","安排练习和复盘"])

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False))
    async def publish_demo_note(note: Annotated[str,Field(min_length=2,max_length=300)],ctx: Context) -> DemoReceipt:
        """审批演示回执，不发布真实文章或写入积分、订单、学习记录。"""
        meta=ctx.request_context.meta
        operation=(meta.model_dump().get("operation_id") if meta else None)
        if not operation:raise ValueError("Approval operation identifier is required")
        return DemoReceipt(operation_id=operation,note=note)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True))
    async def server_env_flags() -> EnvironmentFlags:
        """测试隔离所用的变量存在性，不返回任何凭据值。默认不会被客户端注册。"""
        return EnvironmentFlags(**{name:bool(os.environ.get(name)) for name in ("LLM_API_KEY","DATABASE_URL","LEARNTRAIL_MCP_ACCESS_TOKEN")})
    return server


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--http",action="store_true")
    parser.add_argument("--port",type=int,default=8121)
    parser.add_argument("--disconnect-once-file",type=Path)
    options=parser.parse_args()
    create_demo(options.port,options.disconnect_once_file).run(transport="streamable-http" if options.http else "stdio")
