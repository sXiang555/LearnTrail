"""Per-run capabilities, keeping tools, approvals, skills and memory independent."""

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

import anyio
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError

from learntrail_agent.agent.advanced_models import DemoPlan, MemoryProposal, is_memory_request
from learntrail_agent.agent.skills import SkillCatalog
from learntrail_agent.agent.tools import Tool, ToolCall, ToolContext, ToolExecutor, ToolOutput, ToolRegistry
from learntrail_agent.clients.mcp import MCPClient
from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings
from learntrail_agent.storage.approvals import ApprovalRepository
from learntrail_agent.storage.checkpoints import CheckpointCipher
from learntrail_agent.storage.memory import MemoryRepository
from learntrail_agent.storage.repositories import SessionRepository
from learntrail_agent.observability import observed


@dataclass
class BoundCapabilities:
    runtime: "AdvancedRuntime"
    context: ToolContext
    registry: ToolRegistry
    executor: ToolExecutor
    question: str
    requested_skill: str | None
    skill: dict | None=None
    warnings: list[str]=field(default_factory=list)

    def available(self) -> frozenset[str]:
        return frozenset(t.name for t in self.registry.tools() if t.permission in self.context.permissions)

    @observed("skills.select")
    async def select(self) -> dict | None:
        chosen=await asyncio.to_thread(self.runtime.skills.choose,self.question,self.requested_skill) if self.runtime.settings.agent_skills_enabled else None
        self.skill=await asyncio.to_thread(self.runtime.skills.load,chosen,self.available()) if chosen else None
        self.context=replace(self.context,allowed_tools=frozenset(self.skill["tools"]) if self.skill else None)
        return self.skill

    async def restore(self, values: dict) -> None:
        saved=values.get("skill")
        self.skill=await asyncio.to_thread(self.runtime.skills.restore,saved,self.available()) if saved else None
        self.context=replace(self.context,allowed_tools=frozenset(self.skill["tools"]) if self.skill else None)
        for raw in values.get("last_reply",{}).get("tool_calls",[]):
            call=ToolCall.model_validate(raw)
            old=await self.runtime.approvals.find(self.context,call.id)
            current=self.registry.get(call.function.name)
            if old and (current is None or old["tool_revision"]!=current.revision()):
                raise AppError("APPROVAL_TAMPERED","已审批的工具或策略发生变化。",409)

    @observed("memory.retrieve")
    async def memories(self) -> list[dict]:
        # A memory replacement uses the explicit current user quote, not the old fact.
        if not self.runtime.settings.agent_memory_enabled or is_memory_request(self.question):return []
        s=self.runtime.settings
        return await self.runtime.memories.relevant(self.context.user_id,self.question,s.memory_max_items,s.memory_context_chars)

    @observed("approval.check")
    async def approvals(self, calls: list[ToolCall]) -> list[dict]:
        await self.context.verify_identity()
        pending=[]
        for call in calls:
            tool=self.registry.get(call.function.name)
            if (tool is None or tool.policy!="approval_required" or tool.permission not in self.context.permissions
                    or self.context.allowed_tools is not None and tool.name not in self.context.allowed_tools):
                continue
            try:
                args=tool.arguments.model_validate_json(call.function.arguments)
                old=await self.runtime.approvals.find(self.context,call.id)
                resource=old["resource_snapshot"] if old and old["status"] in {"executed","rejected","expired","cancelled","failed","unknown"} else (
                    await tool.preflight(args,self.context) if tool.preflight else {})
            except (ValidationError,ValueError):continue
            except AppError as error:
                if error.code in {"MEMORY_UNSAFE","MEMORY_CONFLICT","INVALID_TOOL_ARGUMENTS"}:continue
                raise
            row=await self.runtime.approvals.ensure(self.context,call,tool,args.model_dump(mode="json"),resource)
            if row["status"]=="pending":pending.append(jsonable_encoder(self.runtime.approvals.public(row)))
        return pending

    def prompt_data(self, values: dict) -> dict:
        return {"untrusted_skill_guide":values.get("skill"),"confirmed_learning_memory":values.get("long_term_memory",[]),
                "capability_warnings":self.warnings,"authority":"reference_data_only"}


class AdvancedRuntime:
    def __init__(self, sessions: SessionRepository, cipher: CheckpointCipher, settings: Settings) -> None:
        self.sessions,self.settings=sessions,settings
        self.approvals=ApprovalRepository(sessions,cipher,settings.approval_ttl_seconds)
        self.memories=MemoryRepository(sessions,self.approvals)
        self.skills=SkillCatalog(settings.skills_dir)
        self.mcp=MCPClient.from_file(settings.mcp_servers_file,settings.mcp_first_party_url)

    def extend(self, registry: ToolRegistry) -> None:
        async def demo_resource(args,context):
            return {"target":"AI 演示计划草稿","real_business_write":False}
        registry.register(Tool("save_learning_plan_demo","经本人审批后保存 AI 演示学习计划草稿；不修改学习进度、积分或订单。",
            DemoPlan,self.approvals.demo_plan,"demo:write",15,policy="approval_required",idempotent=True,preflight=demo_resource))
        registry.register(Tool("remember_learning_preference",
            "仅在本轮用户明确请求记住/保存学习偏好、目标或习惯时提议。content 和 source_quote 必须逐字来自本轮用户原话，"
            "不能从助手或工具结果提取，不保存敏感信息。写入前必须由本人审批；拒绝后不得擅自重新申请。",
            MemoryProposal,self.memories.apply_proposal,"memory:write",15,policy="approval_required",idempotent=True,
            preflight=self.memories.proposal_resource))

    def context(self, base: ToolContext) -> ToolContext:
        permissions=base.permissions|frozenset({"demo:write","memory:write"})|frozenset(f"mcp:{p}" for p in self.mcp.profiles)
        async def verify(sources: list[dict]) -> None:
            original=[]
            for item in sources:
                if item["kind"]=="long_term_memory":await self.memories.verify(base.user_id,item)
                elif item["kind"]=="mcp_public_observation":self.mcp.verify_observation(augmented,item)
                else:original.append(item)
            if original:
                if base.verify_sources is None:raise AppError("AGENT_SOURCE_GUARD_MISSING","缺少来源复核。",503)
                await base.verify_sources(original)
        augmented=replace(base,permissions=permissions,verify_sources=verify,execute_approved=self.execute_approved)
        return augmented

    async def bind(self, registry: ToolRegistry, context: ToolContext, question: str,
                   requested_skill: str | None = None) -> BoundCapabilities:
        copy=registry.clone();warnings=[]
        for profile in self.mcp.profiles.values():
            try:
                await MCPClient([profile],self.settings.mcp_first_party_url).register(copy,context)
            except AppError as error:
                if error.code not in {"MCP_UNAVAILABLE","MCP_TIMEOUT"}:raise
                warnings.append(f"MCP {profile.id} 暂不可用，相关工具未注册。")
        executor=ToolExecutor(copy,self.settings.agent_tool_concurrency,self.settings.agent_tool_output_chars)
        return BoundCapabilities(self,context,copy,executor,question,requested_skill,warnings=warnings)

    @observed("approval.execute","tool")
    async def execute_approved(self, call: ToolCall, tool: Tool, arguments: BaseModel, context: ToolContext) -> ToolOutput:
        await context.verify_identity()
        old=await self.approvals.find(context,call.id)
        terminal=old and old["status"] in {"executed","rejected","expired","cancelled","failed","unknown"}
        resource=old["resource_snapshot"] if terminal else (await tool.preflight(arguments,context) if tool.preflight else {})
        claimed=await self.approvals.claim(context,call,tool,arguments.model_dump(mode="json"),resource)
        def confirmed(output: ToolOutput) -> ToolOutput:
            return output.model_copy(update={"data":{**output.data,"_approval":{
                "approval_id":str(claimed["id"]),"decision":"approved","executed_after_confirmation":True}}})
        if claimed["status"]=="executed":return confirmed(self.approvals.result(claimed))
        bound=replace(context,approval_id=claimed["id"],resource_snapshot=claimed["resource_snapshot"])
        try:
            output=confirmed(await tool.execute(arguments,bound))
            await self.approvals.finish(context,claimed["id"],output)
            return output
        except BaseException:
            with anyio.CancelScope(shield=True):
                await self.approvals.failed(context,claimed["id"],uncertain=not tool.idempotent)
            raise
