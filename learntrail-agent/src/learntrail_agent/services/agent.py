"""Persistent Agent runs with shared chat cancellation and freshly checked history."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

import anyio

from learntrail_agent.agent.learntrail_tools import LearnTrailTools
from learntrail_agent.agent.loop import AgentLoop, AgentState
from learntrail_agent.agent.schemas import AgentChatRequest, AgentChatResponse
from learntrail_agent.agent.advanced_models import is_memory_request
from learntrail_agent.agent.tools import ToolContext, ToolExecutor
from learntrail_agent.clients.llm import LLMClient
from learntrail_agent.errors import AppError
from learntrail_agent.schemas import ChatResponse, SessionDetail, Usage
from learntrail_agent.services.chat import ChatService, build_context
from learntrail_agent.settings import Settings
from learntrail_agent.storage.agent_repository import AgentRepository
from learntrail_agent.storage.checkpoints import SessionCheckpointer, thread_id
from learntrail_agent.storage.repositories import SessionRepository, StartedRun
from learntrail_agent.observability import observed

logger = logging.getLogger(__name__)
REDACTED = "此 Agent 回答的来源或学习状态已变化，或暂时无法核验，请重新查询。"


@dataclass
class PreparedAgent:
    run: StartedRun
    context: ToolContext
    content: str
    history: list[tuple[str,str]]
    state: AgentState
    started_at: float
    deadline: float
    started: bool = False
    executing: bool = False
    settled: bool = False
    checkpointer: Any = None
    resume: bool = False
    pause_before_tools: bool = False
    stream_tokens: bool = False
    history_citations: list[dict] = field(default_factory=list)
    capabilities: Any = None


class AgentService:
    def __init__(self, sessions: SessionRepository, traces: AgentRepository, tools: LearnTrailTools,
                 llm: LLMClient, control: ChatService, settings: Settings, checkpointer: Any = None, advanced: Any = None) -> None:
        self.sessions,self.traces,self.tools,self.control,self.settings = sessions,traces,tools,control,settings
        self.checkpointer = checkpointer
        self.advanced=advanced
        registry = tools.registry()
        if advanced:advanced.extend(registry)
        executor = ToolExecutor(registry,settings.agent_tool_concurrency,settings.agent_tool_output_chars)
        thinking = None if settings.agent_thinking_mode=="provider_default" else settings.agent_thinking_mode
        self.loop = AgentLoop(llm,registry,executor,max_rounds=settings.agent_max_rounds,
            max_tool_calls=settings.agent_max_tool_calls,timeout_seconds=settings.agent_timeout_seconds,
            max_tokens=settings.agent_max_tokens,thinking=thinking,prompt_suffix=settings.agent_prompt_suffix)

    async def _context(self, token: str, session_id: UUID | None = None, run_id: UUID | None = None) -> ToolContext:
        context=await self.tools.context(token,session_id,run_id)
        return self.advanced.context(context) if self.advanced else context

    def _saver(self, context: ToolContext) -> SessionCheckpointer | None:
        if self.checkpointer is None:
            return None
        return SessionCheckpointer(self.checkpointer,self.sessions,context.user_id,context.session_id)

    @staticmethod
    def _citations(events: list[dict]) -> list[dict]:
        citations=[]
        for event in events:
            item=event["payload"]
            if event["kind"]=="history_sources":
                rows=item["items"]
            elif event["kind"]=="tool_result" and item.get("ok"):
                data=item.get("data") or {}
                name=item.get("name","")
                rows=data.get("matches",[]) if name=="search_knowledge_base" or name.endswith("_search_knowledge_base") else (
                    data.get("articles",[]) if name=="search_articles" or name.endswith("_search_articles") else [])
            else:
                continue
            for row in rows:
                citation={k:row[k] for k in ("article_id","slug","title","section","ordinal","quote","start_char",
                    "end_char","position_basis","content_hash","source_type") if k in row}
                if citation not in citations:
                    citations.append(citation)
        return citations[:20]

    async def workflow_state(self, user_id: int, session_id: UUID, run_id: UUID, token: str) -> dict:
        run,_=await self.traces.trace(user_id,session_id,run_id)
        context=await self._context(token,session_id,run_id)
        if context.user_id!=user_id:
            raise AppError("INVALID_TOKEN","登录身份不一致。",401)
        saver=self._saver(context)
        if saver is None:
            raise AppError("CHECKPOINT_NOT_CONFIGURED","尚未配置工作流存储。",503)
        snapshot=await self.loop.snapshot(context,saver)
        if not snapshot.values or snapshot.values.get("run_id")!=str(run_id):
            raise AppError("GRAPH_RUN_SUPERSEDED","此任务没有当前可恢复状态，或已被后续执行替代。",409)
        output={"run_id":str(run_id),"thread_id":thread_id(session_id),"checkpoint_ns":"",
            "checkpoint_id":snapshot.config["configurable"]["checkpoint_id"],"next":list(snapshot.next),
            "status":run.status,"model_calls":snapshot.values.get("model_calls",0),
            "tool_calls":snapshot.values.get("tool_calls",0)}
        output["skill"]={k:v for k,v in (snapshot.values.get("skill") or {}).items() if k!="guide"} or None
        output["memory_count"]=len(snapshot.values.get("long_term_memory",[]))
        try:
            await context.verify_sources(run.agent_data.get("evidence",[])+snapshot.values.get("evidence",[]))
        except AppError as error:
            if error.code in {"INVALID_TOKEN","TOKEN_EXPIRED","ACCOUNT_DISABLED"}:
                raise
            return {**output,"redacted":True,"messages":[]}
        return {**output,"redacted":False,"messages":[{"id":m.id,"role":m.type,"content":m.content}
            for m in snapshot.values.get("messages",[])][-100:]}

    async def prepare_resume(self, user_id: int, session_id: UUID, run_id: UUID, token: str) -> PreparedAgent:
        started=time.monotonic()
        run,events=await self.traces.trace(user_id,session_id,run_id)
        context=await self._context(token,session_id,run_id)
        if context.user_id!=user_id:
            raise AppError("INVALID_TOKEN","登录身份不一致。",401)
        saver=self._saver(context)
        if saver is None:
            raise AppError("CHECKPOINT_NOT_CONFIGURED","尚未配置工作流存储。",503)
        snapshot=await self.loop.snapshot(context,saver)
        if not snapshot.values or snapshot.values.get("run_id")!=str(run_id):
            raise AppError("GRAPH_RUN_SUPERSEDED","此任务没有当前可恢复状态，或已被后续执行替代。",409)
        await self.loop._verify(context,run.agent_data.get("evidence",[])+snapshot.values.get("evidence",[]))
        capabilities=None
        if self.advanced:
            approvals=await self.advanced.approvals.list(user_id,session_id,run_id)
            if any(a["status"]=="pending" for a in approvals):
                raise AppError("APPROVAL_REQUIRED","请先处理本人待审批操作。",409)
            capabilities=await self.advanced.bind(self.loop.registry,context,"",snapshot.values.get("requested_skill"))
            await capabilities.restore(snapshot.values)
        remaining=self.settings.agent_timeout_seconds-(run.duration_ms or 0)/1000
        if remaining<=0:
            raise AppError("AGENT_TIMEOUT","此任务已用尽运行时间，请重新提问。",504)
        attempts=[e for e in events if e["kind"]=="model_start"]
        usages=[None for _ in attempts]
        for e in events:
            if e["kind"]=="model_end" and 0<e["step"]<=len(usages):
                usages[e["step"]-1]=Usage.model_validate(e["payload"]["usage"]) if e["payload"].get("usage") else None
        state=AgentState(model_calls=len(attempts),tool_calls=sum(e["kind"]=="tool_start" for e in events),
            trace=[{k:v for k,v in e.items() if k!="created_at"} for e in events],
            evidence=run.agent_data.get("evidence",[]),usages=usages)
        claimed,content=await self.traces.resume(user_id,session_id,run_id,remaining+60)
        if capabilities:capabilities.question=content
        return PreparedAgent(claimed,context,content,[],state,started-(run.duration_ms or 0)/1000,
            started+remaining,checkpointer=saver,resume=True,capabilities=capabilities)

    async def _safe_history(self, context: ToolContext, session_id: UUID, question: str) -> tuple[list[tuple[str,str]],list[dict],list[dict]]:
        turns = await self.sessions.agent_history(context.user_id,session_id,self.settings.context_max_turns)
        permitted = []
        for turn in turns:
            if self.advanced and is_memory_request(question) and any(e["kind"]=="long_term_memory" for e in turn.evidence):
                continue
            if turn.kind=="agent":
                try:
                    await context.verify_sources(turn.evidence)
                except AppError as error:
                    if error.code in {"INVALID_TOKEN","TOKEN_EXPIRED","ACCOUNT_DISABLED"}:
                        raise
                    continue
            permitted.append(turn)
        bounded = self.settings.model_copy(update={"context_max_chars":min(self.settings.context_max_chars,30000)})
        selected = build_context([(t.question,t.answer) for t in permitted],question,bounded)
        count = (len(selected)-1)//2
        retained = permitted[-count:] if count else []
        return ([(t.question,t.answer) for t in retained], [e for t in retained for e in t.evidence],
                [c for t in retained for c in t.citations][:20])

    async def prepare(self, user_id: int, session_id: UUID, body: AgentChatRequest, token: str) -> PreparedAgent:
        started = time.monotonic();deadline=started+self.settings.agent_timeout_seconds
        run = None
        try:
            async with asyncio.timeout_at(deadline):
                context = await self._context(token,session_id)
                if context.user_id != user_id:
                    raise AppError("INVALID_TOKEN","登录身份不一致。",401)
                lease = self.settings.agent_timeout_seconds+30+8*self.settings.database_statement_timeout_ms/1000+4*self.settings.database_timeout_seconds
                run = await self.sessions.start_run(user_id,session_id,body.content,self.settings.llm_model,lease,kind="agent")
                context = replace(context,session_id=session_id,run_id=run.id)
                capabilities=None
                if self.advanced:
                    await self.advanced.approvals.cancel_superseded(user_id,session_id,run.id)
                    capabilities=await self.advanced.bind(self.loop.registry,context,body.content,body.skill)
                history,evidence,citations = await self._safe_history(context,session_id,body.content)
                return PreparedAgent(run,context,body.content,history,AgentState(evidence=evidence),started,deadline,
                    checkpointer=self._saver(context),pause_before_tools=body.pause_before_tools,history_citations=citations,
                    capabilities=capabilities)
        except BaseException as error:
            public = AppError("AGENT_TIMEOUT","Agent 准备阶段超时。",504,True) if isinstance(error,TimeoutError) else error
            if run is not None:
                with anyio.CancelScope(shield=True):
                    failure = await self.control.settle_failure(user_id,session_id,run.id,public,
                        max(0,int((time.monotonic()-started)*1000)),None)
                    if failure:
                        raise failure from None
            if public is not error:
                raise public from None
            raise

    @observed("agent.run","agent")
    async def execute(
        self, prepared: PreparedAgent, emit: Callable[[str,dict],Awaitable[None]] | None = None,
        disconnected: Callable[[],Awaitable[bool]] | None = None,
    ) -> AgentChatResponse:
        prepared.executing = True
        context,state = prepared.context,prepared.state
        watcher = None
        try:
            async with asyncio.timeout_at(prepared.deadline):
                watcher = asyncio.create_task(self.control.watch_stop(context.user_id,context.session_id,prepared.run.id,disconnected))
                async def write(event: dict, evidence: list[dict]) -> None:
                    await self.traces.append(context.user_id,context.session_id,prepared.run.id,event,evidence)
                if prepared.history_citations:
                    event={"sequence":len(state.trace)+1,"kind":"history_sources","step":state.model_calls,
                           "duration_ms":0,"payload":{"items":prepared.history_citations}}
                    state.trace.append(event)
                    await write(event,list(state.evidence))
                result = await self.control.while_active(self.loop.run(prepared.content,context,state,emit,
                    history=prepared.history,write_trace=write,checkpointer=prepared.checkpointer,
                    resume=prepared.resume,pause_before_tools=prepared.pause_before_tools,
                    stream_tokens=prepared.stream_tokens,capabilities=prepared.capabilities),watcher)
                duration = max(0,int((time.monotonic()-prepared.started_at)*1000))
                checkpoint = {"thread_id":thread_id(context.session_id),"checkpoint_id":result.checkpoint_id,
                              "checkpoint_ns":""} if result.checkpoint_id else None
                if result.status=="paused":
                    await self.traces.pause(context.user_id,context.session_id,prepared.run.id,duration,result.usage)
                    return AgentChatResponse(id="",model=self.settings.llm_model,content="",finish_reason="interrupt",
                        usage=result.usage,run_id=prepared.run.id,user_message_id=prepared.run.user_message_id,
                        assistant_message_id=None,status="paused",model_calls=result.model_calls,tool_calls=result.tool_calls,
                        usage_complete=result.usage_complete,duration_ms=duration,tools=[],checkpoint=checkpoint)
                reply = ChatResponse(id=result.provider_response_id,model=result.model,content=result.answer,
                                     finish_reason="stop",usage=result.usage)
                assistant_id = await self.sessions.complete_run(context.user_id,context.session_id,prepared.run.id,reply,duration)
                summary = [dict(e["payload"]) for e in state.trace if e["kind"]=="tool_end"]
                return AgentChatResponse(**reply.model_dump(),run_id=prepared.run.id,user_message_id=prepared.run.user_message_id,
                    assistant_message_id=assistant_id,model_calls=result.model_calls,tool_calls=result.tool_calls,
                    usage_complete=result.usage_complete,duration_ms=duration,tools=summary,checkpoint=checkpoint,
                    citations=self._citations(state.trace))
        except BaseException as error:
            public = AppError("AGENT_TIMEOUT","Agent 执行超过总时限。",504,True) if isinstance(error,TimeoutError) else error
            with anyio.CancelScope(shield=True):
                failure = await self.control.settle_failure(context.user_id,context.session_id,prepared.run.id,public,
                    max(0,int((time.monotonic()-prepared.started_at)*1000)),state.usage())
            if isinstance(error,(asyncio.CancelledError,GeneratorExit)):
                raise
            if failure:
                raise failure from None
            if isinstance(public,AppError):
                raise public
            logger.error("Agent execution failed: %s",type(error).__name__)
            raise AppError("AGENT_EXECUTION_FAILED","Agent 暂时无法完成请求。",500) from None
        finally:
            prepared.settled = True
            if watcher:
                watcher.cancel()
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(watcher,return_exceptions=True)

    async def abandon(self, prepared: PreparedAgent) -> None:
        if not prepared.executing and not prepared.settled:
            with anyio.CancelScope(shield=True):
                await self.control.settle_failure(prepared.context.user_id,prepared.context.session_id,
                    prepared.run.id,asyncio.CancelledError(),0,None)
                prepared.settled=True

    async def stream(self, prepared: PreparedAgent, disconnected: Callable[[],Awaitable[bool]]) -> AsyncIterator[tuple[str,dict]]:
        prepared.started=True
        prepared.stream_tokens=True
        queue: asyncio.Queue[tuple[str,dict]] = asyncio.Queue(maxsize=64)
        async def emit(kind: str, data: dict) -> None:
            try:
                queue.put_nowait((kind,data))
            except asyncio.QueueFull:
                raise AppError("AGENT_CLIENT_TOO_SLOW","客户端接收过慢，请重新查询。",503,True) from None
        worker = asyncio.create_task(self.execute(prepared,emit,disconnected))
        try:
            yield "meta", {"run_id":str(prepared.run.id),"session_id":str(prepared.context.session_id),
                           "user_message_id":str(prepared.run.user_message_id),"mode":"agent",
                           "resumed":prepared.resume}
            while True:
                while not queue.empty():
                    yield queue.get_nowait()
                if worker.done():
                    result = await worker
                    if result.status=="paused":
                        yield "paused", {"run_id":str(result.run_id),"status":"paused","checkpoint":result.checkpoint,
                                         "duration_ms":result.duration_ms}
                        return
                    yield "usage", {**(result.usage.model_dump() if result.usage else {}),
                                    "usage":result.usage.model_dump() if result.usage else None,
                                    "complete":result.usage_complete,"model_calls":result.model_calls}
                    yield "citations", {"items":result.citations}
                    yield "done", {"run_id":str(result.run_id),"assistant_message_id":str(result.assistant_message_id),
                        "status":"succeeded","model":result.model,"duration_ms":result.duration_ms,
                        "model_calls":result.model_calls,"tool_calls":result.tool_calls,
                        "usage":result.usage.model_dump() if result.usage else None,
                        "citations":result.citations,"checkpoint":result.checkpoint}
                    return
                await asyncio.sleep(.02)
        except AppError as error:
            yield "error", {**error.payload(),"run_id":str(prepared.run.id),"discard_answer":True}
        finally:
            worker.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(worker,return_exceptions=True)
                await self.abandon(prepared)

    async def sanitize_detail(self, user_id: int, detail: SessionDetail, token: str) -> SessionDetail:
        messages=[m for m in detail.messages if m.run_kind=="agent" and m.role=="assistant"]
        if not messages:
            return detail
        context=await self._context(token)
        if context.user_id!=user_id:
            raise AppError("INVALID_TOKEN","登录身份不一致。",401)
        for message in messages:
            try:
                await context.verify_sources(message.run_evidence)
            except AppError as error:
                if error.code in {"INVALID_TOKEN","TOKEN_EXPIRED","ACCOUNT_DISABLED"}:
                    raise
                message.content, message.redacted, message.citations = REDACTED, True, []
        return detail

    async def trace(self, user_id: int, session_id: UUID, run_id: UUID, token: str) -> dict:
        run,events=await self.traces.trace(user_id,session_id,run_id)
        context=await self._context(token)
        if context.user_id!=user_id:
            raise AppError("INVALID_TOKEN","登录身份不一致。",401)
        try:
            await context.verify_sources(run.agent_data.get("evidence",[]))
        except AppError as error:
            if error.code in {"INVALID_TOKEN","TOKEN_EXPIRED","ACCOUNT_DISABLED"}:
                raise
            return {"run":run.model_dump(),"redacted":True,"events":[
                {k:v for k,v in event.items() if k!="payload"} for event in events]}
        return {"run":run.model_dump(),"redacted":False,"events":events}
