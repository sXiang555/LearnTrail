"""User-owned, explicitly confirmed learning facts, separate from chat/checkpoints."""

from __future__ import annotations

import unicodedata
from uuid import UUID, uuid4

from learntrail_agent.agent.advanced_models import MemoryCreate, MemoryUpdate, MemoryProposal, MemoryInfo, digest, is_memory_request
from learntrail_agent.agent.tools import ToolContext, ToolOutput
from learntrail_agent.errors import AppError
from learntrail_agent.rag.bm25 import tokenize
from learntrail_agent.storage.approvals import ApprovalRepository
from learntrail_agent.storage.pool import transaction
from learntrail_agent.storage.repositories import SessionRepository


def memory_hash(value) -> str:
    return digest({"memory_type":value["memory_type"],"key":value["key"],"content":value["content"]})


class MemoryRepository:
    def __init__(self, sessions: SessionRepository, approvals: ApprovalRepository) -> None:
        self.sessions,self.pool,self.approvals=sessions,sessions.pool,approvals

    async def expire(self, user_id: int) -> None:
        async with transaction(self.pool) as conn:
            await conn.execute("""UPDATE ai_memories SET status='expired',version=version+1,updated_at=clock_timestamp()
                WHERE user_id=%s AND status='active' AND expires_at<=clock_timestamp()""",(user_id,))

    async def _lock_user(self, conn, user_id: int) -> None:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"learntrail-memory:{user_id}",))

    async def _source(self, conn, user_id: int, session_id: UUID | None) -> None:
        if session_id:
            await self.sessions._owned(conn,user_id,session_id)

    async def _future(self, conn, expires_at) -> None:
        if expires_at:
            future=await (await conn.execute("SELECT %s::timestamptz>clock_timestamp() AS valid",(expires_at,))).fetchone()
            if not future["valid"]:raise AppError("INVALID_REQUEST","过期时间必须在未来。",422)

    async def list(self, user_id: int, include_inactive: bool = False) -> list[MemoryInfo]:
        await self.expire(user_id)
        async with transaction(self.pool) as conn:
            rows=await (await conn.execute("""SELECT * FROM ai_memories WHERE user_id=%s AND (%s OR status='active')
                ORDER BY updated_at DESC,id DESC LIMIT 50""",(user_id,include_inactive))).fetchall()
            return [MemoryInfo.model_validate(r) for r in rows]

    async def get(self, user_id: int, memory_id: UUID) -> MemoryInfo:
        await self.expire(user_id)
        async with transaction(self.pool) as conn:
            row=await (await conn.execute("SELECT * FROM ai_memories WHERE user_id=%s AND id=%s",(user_id,memory_id))).fetchone()
            if row is None:raise AppError("MEMORY_NOT_FOUND","记忆不存在。",404)
            return MemoryInfo.model_validate(row)

    async def create(self, user_id: int, body: MemoryCreate) -> MemoryInfo:
        await self.expire(user_id)
        async with transaction(self.pool) as conn:
            await self._lock_user(conn,user_id)
            await self._source(conn,user_id,body.source_session_id)
            await self._future(conn,body.expires_at)
            old=await (await conn.execute("SELECT * FROM ai_memories WHERE user_id=%s AND memory_type=%s AND key=%s AND status='active' FOR UPDATE",
                (user_id,body.memory_type,body.key))).fetchone()
            if old:
                if old["content"]==body.content:return MemoryInfo.model_validate(old)
                raise AppError("MEMORY_CONFLICT","此记忆已有不同内容，请编辑已有版本。",409)
            value=body.model_dump()
            row=await (await conn.execute("""INSERT INTO ai_memories(id,user_id,memory_type,key,content,content_hash,search_terms,
                source_kind,source_session_id,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'manual',%s,%s) RETURNING *""",
                (uuid4(),user_id,body.memory_type,body.key,body.content,memory_hash(value),sorted(set(tokenize(body.content)))[:256],
                 body.source_session_id,body.expires_at))).fetchone()
            return MemoryInfo.model_validate(row)

    async def update(self, user_id: int, memory_id: UUID, body: MemoryUpdate) -> MemoryInfo:
        await self.expire(user_id)
        async with transaction(self.pool) as conn:
            await self._lock_user(conn,user_id)
            await self._source(conn,user_id,body.source_session_id)
            await self._future(conn,body.expires_at)
            row=await (await conn.execute("SELECT * FROM ai_memories WHERE id=%s AND user_id=%s FOR UPDATE",(memory_id,user_id))).fetchone()
            if row is None:raise AppError("MEMORY_NOT_FOUND","记忆不存在。",404)
            if row["status"]!="active" or row["version"]!=body.version:
                raise AppError("MEMORY_CONFLICT","记忆版本已变化或已失效，请刷新。",409)
            other=await (await conn.execute("SELECT id FROM ai_memories WHERE user_id=%s AND memory_type=%s AND key=%s AND status='active' AND id<>%s",
                                            (user_id,body.memory_type,body.key,memory_id))).fetchone()
            if other:raise AppError("MEMORY_CONFLICT","此类型的记忆已存在。",409)
            row=await (await conn.execute("""UPDATE ai_memories SET memory_type=%s,key=%s,content=%s,content_hash=%s,
                search_terms=%s,version=version+1,source_kind='manual',source_session_id=%s,source_message_id=NULL,
                source_quote=NULL,last_approval_id=NULL,expires_at=%s,updated_at=clock_timestamp()
                WHERE id=%s AND user_id=%s RETURNING *""",(body.memory_type,body.key,body.content,memory_hash(body.model_dump()),
                sorted(set(tokenize(body.content)))[:256],body.source_session_id,body.expires_at,memory_id,user_id))).fetchone()
            return MemoryInfo.model_validate(row)

    async def delete(self, user_id: int, memory_id: UUID, version: int) -> MemoryInfo:
        async with transaction(self.pool) as conn:
            await self._lock_user(conn,user_id)
            row=await (await conn.execute("SELECT * FROM ai_memories WHERE id=%s AND user_id=%s FOR UPDATE",(memory_id,user_id))).fetchone()
            if row is None:raise AppError("MEMORY_NOT_FOUND","记忆不存在。",404)
            if row["status"]=="deleted":return MemoryInfo.model_validate(row)
            if row["version"]!=version:raise AppError("MEMORY_CONFLICT","记忆版本已变化，请刷新。",409)
            row=await (await conn.execute("UPDATE ai_memories SET status='deleted',version=version+1,updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                                         (memory_id,))).fetchone()
            return MemoryInfo.model_validate(row)

    async def relevant(self, user_id: int, question: str, limit: int, max_chars: int) -> list[dict]:
        await self.expire(user_id)
        terms=sorted(set(tokenize(question)))[:128]
        async with transaction(self.pool) as conn:
            rows=await (await conn.execute("""SELECT * FROM ai_memories WHERE user_id=%s AND status='active'
                AND (expires_at IS NULL OR expires_at>clock_timestamp())
                AND (memory_type='preference' OR search_terms && %s::text[]) ORDER BY updated_at DESC LIMIT 50""",
                (user_id,terms))).fetchall()
        rows.sort(key=lambda row:(row["memory_type"]=="preference",len(set(row["search_terms"])&set(terms))),reverse=True)
        selected=[];budget=0
        for row in rows:
            item={"memory_id":str(row["id"]),"memory_type":row["memory_type"],"key":row["key"],"content":row["content"],
                  "version":row["version"],"source_kind":row["source_kind"],
                  "source_session_id":str(row["source_session_id"]) if row["source_session_id"] else None}
            size=len(str(item))
            if budget+size>max_chars:continue
            selected.append(item);budget+=size
            if len(selected)>=limit:break
        return selected

    @staticmethod
    def evidence(item: dict) -> dict:
        return {"kind":"long_term_memory","memory_id":item["memory_id"],"version":item["version"],
                "content_hash":memory_hash(item)}

    async def verify(self, user_id: int, item: dict) -> None:
        try:memory=await self.get(user_id,UUID(item["memory_id"]))
        except AppError as error:
            if error.code!="MEMORY_NOT_FOUND":raise
            raise AppError("AGENT_SOURCE_CHANGED","相关长期记忆已不可用。",409) from None
        if memory.status!="active" or memory.version!=item["version"] or memory_hash(memory.model_dump())!=item["content_hash"]:
            raise AppError("AGENT_SOURCE_CHANGED","相关长期记忆已更新、过期或删除。",409)

    async def proposal_resource(self, arguments: MemoryProposal, context: ToolContext) -> dict:
        await self.expire(context.user_id)
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,context.user_id,context.session_id)
            user=await (await conn.execute("SELECT id,content FROM ai_messages WHERE session_id=%s AND run_id=%s AND role='user'",
                                          (context.session_id,context.run_id))).fetchone()
            if not user or not is_memory_request(user["content"]) or arguments.source_quote not in unicodedata.normalize("NFKC",user["content"]):
                raise AppError("MEMORY_UNSAFE","记忆提议必须逐字来自本轮用户原话。",422)
            current=await (await conn.execute("SELECT * FROM ai_memories WHERE user_id=%s AND memory_type=%s AND key=%s AND status='active'",
                                             (context.user_id,arguments.memory_type,arguments.key))).fetchone()
            return {"target":"用户学习记忆","key":arguments.key,"source_message_id":str(user["id"]),
                "source_hash":digest(user["content"]),"current_id":str(current["id"]) if current else None,
                "current_version":current["version"] if current else 0,"previous_content":current["content"] if current else None}

    async def apply_proposal(self, arguments: MemoryProposal, context: ToolContext) -> ToolOutput:
        async with transaction(self.pool) as conn:
            await self.approvals._run(conn,context,active=True)
            approval=await (await conn.execute("SELECT * FROM ai_approvals WHERE id=%s AND user_id=%s FOR UPDATE",
                                              (context.approval_id,context.user_id))).fetchone()
            if not approval or approval["status"] not in {"executing","executed"}:
                raise AppError("APPROVAL_REQUIRED","记忆写入未获确认。",409)
            if approval["status"]=="executed":return self.approvals.result(approval)
            await self._lock_user(conn,context.user_id)
            resource=context.resource_snapshot
            user=await (await conn.execute("SELECT id,content FROM ai_messages WHERE id=%s AND session_id=%s AND run_id=%s AND role='user'",
                (UUID(resource["source_message_id"]),context.session_id,context.run_id))).fetchone()
            if (not user or not is_memory_request(user["content"]) or digest(user["content"])!=resource["source_hash"]
                    or arguments.source_quote not in unicodedata.normalize("NFKC",user["content"])):
                raise AppError("MEMORY_UNSAFE","记忆原话已无法验证。",409)
            old=await (await conn.execute("SELECT * FROM ai_memories WHERE user_id=%s AND memory_type=%s AND key=%s AND status='active' FOR UPDATE",
                                         (context.user_id,arguments.memory_type,arguments.key))).fetchone()
            if (old["version"] if old else 0)!=resource["current_version"] or (str(old["id"]) if old else None)!=resource["current_id"]:
                raise AppError("MEMORY_CONFLICT","记忆内容已变化，需重新确认。",409)
            values=(arguments.content,memory_hash(arguments.model_dump()),sorted(set(tokenize(arguments.content)))[:256],
                    context.session_id,user["id"],arguments.source_quote,context.approval_id)
            if old and old["content"]==arguments.content:
                row=old
            elif old:
                row=await (await conn.execute("""UPDATE ai_memories SET content=%s,content_hash=%s,search_terms=%s,
                    source_session_id=%s,source_message_id=%s,source_quote=%s,last_approval_id=%s,source_kind='user_quote',
                    version=version+1,updated_at=clock_timestamp() WHERE id=%s RETURNING *""",(*values,old["id"]))).fetchone()
            else:
                row=await (await conn.execute("""INSERT INTO ai_memories(id,user_id,memory_type,key,content,content_hash,search_terms,
                    source_session_id,source_message_id,source_quote,last_approval_id,source_kind)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'user_quote') RETURNING *""",
                    (uuid4(),context.user_id,arguments.memory_type,arguments.key,*values))).fetchone()
            output=ToolOutput(data={"memory_id":str(row["id"]),"version":row["version"],"status":"saved_after_confirmation"},
                              evidence=[self.evidence({**row,"memory_id":str(row["id"])})])
            await self.approvals.finish_on(conn,context.approval_id,output)
            return output
