"""Persistent chat orchestration; no database transaction spans a model call."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

import anyio

from learntrail_agent.clients.llm import LLMClient
from learntrail_agent.errors import AppError, LLMError
from learntrail_agent.schemas import ChatRequest, ChatResponse, Message, SessionChatRequest, SessionChatResponse, Usage
from learntrail_agent.settings import Settings
from learntrail_agent.storage.repositories import SessionRepository, StartedRun
from learntrail_agent.streaming import StreamEvent

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class PreparedStream:
    run: StartedRun
    request: ChatRequest
    started: bool = False


def build_context(history: list[tuple[str, str]], content: str, settings: Settings) -> list[Message]:
    """Keep a contiguous suffix of whole turns, bounded by turns and characters."""
    retained: list[tuple[str, str]] = []
    size = len(content)
    for question, answer in reversed(history[-settings.context_max_turns:]):
        if (not question.strip() or not answer.strip() or max(len(question), len(answer)) > 20000
                or size + len(question) + len(answer) > settings.context_max_chars):
            break
        retained.append((question, answer))
        size += len(question) + len(answer)
    messages: list[Message] = []
    for question, answer in reversed(retained):
        messages.extend([Message(role="user", content=question), Message(role="assistant", content=answer)])
    messages.append(Message(role="user", content=content))
    return messages


class ChatService:
    def __init__(self, repository: SessionRepository, llm: LLMClient, settings: Settings):
        self.repository = repository
        self.llm = llm
        self.settings = settings

    async def prepare_stream(self, user_id: int, session_id: UUID, body: SessionChatRequest) -> PreparedStream:
        lease = (self.settings.llm_timeout_seconds + 30
                 + 8 * self.settings.database_statement_timeout_ms / 1000
                 + 4 * self.settings.database_timeout_seconds)
        run = await self.repository.start_run(user_id, session_id, body.content, self.settings.llm_model, lease)
        try:
            history = await self.repository.history(user_id, session_id, self.settings.context_max_turns)
            return PreparedStream(run, ChatRequest(
                messages=build_context(history, body.content, self.settings),
                temperature=body.temperature, max_tokens=body.max_tokens,
            ))
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                await self.settle_failure(user_id, session_id, run.id, error, 0, None)
            raise

    async def watch_stop(
        self, user_id: int, session_id: UUID, run_id: UUID,
        disconnected: Callable[[], Awaitable[bool]] | None,
    ) -> None:
        while True:
            if disconnected is not None and await disconnected():
                raise AppError("CLIENT_DISCONNECTED", "连接已断开，生成已停止。", 409)
            run = await self.repository.get_run(user_id, session_id, run_id)
            if run.status == "cancelled":
                raise AppError("RUN_CANCELLED", "已停止生成。", 409)
            if run.status != "running":
                raise AppError("RUN_NOT_ACTIVE", "生成任务已结束。", 409)
            await asyncio.sleep(0.5)

    async def while_active(self, pending: Awaitable[T], watcher: asyncio.Task[None]) -> T:
        task = asyncio.ensure_future(pending)
        try:
            await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher.done():
                watcher.result()
            return await task
        finally:
            if not task.done():
                task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(task, return_exceptions=True)

    async def settle_failure(
        self, user_id: int, session_id: UUID, run_id: UUID, error: BaseException,
        duration_ms: int, usage: Usage | None,
    ) -> AppError | None:
        cancelled = isinstance(error, (asyncio.CancelledError, GeneratorExit)) or (
            isinstance(error, AppError) and error.code in {"RUN_CANCELLED", "CLIENT_DISCONNECTED"}
        )
        code = error.code if isinstance(error, AppError) else "RUN_CANCELLED" if cancelled else "INTERNAL_ERROR"
        try:
            await self.repository.fail_run(
                user_id, session_id, run_id, code, status="cancelled" if cancelled else "failed",
                duration_ms=duration_ms, usage=usage,
            )
        except Exception as failure:
            logger.error("Stream state persistence failed: %s", type(failure).__name__)
            return AppError("RUN_STATE_UNCERTAIN", "任务状态暂时无法保存，请稍后查看会话。", 503, True)
        return None

    async def stream(
        self, user_id: int, session_id: UUID, prepared: PreparedStream,
        disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        prepared.started = True
        run = prepared.run
        started = time.monotonic()
        watcher: asyncio.Task[None] | None = None
        handle = None
        usage: Usage | None = None
        parts: list[str] = []
        settled = False
        try:
            yield "meta", {"session_id": str(session_id), "run_id": str(run.id),
                           "user_message_id": str(run.user_message_id), "model": self.settings.llm_model}
            watcher = asyncio.create_task(self.watch_stop(user_id, session_id, run.id, disconnected))
            opening = asyncio.create_task(self.llm.open_stream(prepared.request))
            try:
                handle = await self.while_active(opening, watcher)
            finally:
                # An upstream connection and a stop signal can complete together.
                if handle is None and opening.done() and not opening.cancelled() and opening.exception() is None:
                    handle = opening.result()
            async with aclosing(self.llm.stream_events(handle)) as events:
                while True:
                    event, data = await self.while_active(anext(events), watcher)
                    if event == "delta":
                        parts.append(data["content"])
                        yield event, data
                    elif event == "usage":
                        usage = Usage.model_validate(data)
                        yield event, data
                    elif event == "done":
                        content = "".join(parts)
                        if data["finish_reason"] != "stop" or not content.strip():
                            raise LLMError("LLM_INCOMPLETE_RESPONSE", "模型未返回完整的有效回复。", 502, True)
                        elapsed = max(0, int((time.monotonic() - started) * 1000))
                        reply = ChatResponse(
                            id=handle.provider_id or "", model=handle.provider_model or self.settings.llm_model,
                            content=content, finish_reason="stop", usage=usage,
                        )
                        assistant_id = await self.repository.complete_run(user_id, session_id, run.id, reply, elapsed)
                        settled = True
                        yield "done", {"run_id": str(run.id), "assistant_message_id": str(assistant_id),
                                       "status": "succeeded", "finish_reason": "stop", "model": reply.model,
                                       "duration_ms": elapsed, "usage": usage.model_dump() if usage else None}
                        return
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as error:
            public = error if isinstance(error, AppError) else AppError("INTERNAL_ERROR", "流式生成失败。", 500)
            if not isinstance(error, AppError):
                logger.error("Session stream failed: %s", type(error).__name__)
            with anyio.CancelScope(shield=True):
                failure = await self.settle_failure(
                    user_id, session_id, run.id, error, max(0, int((time.monotonic() - started) * 1000)), usage,
                )
            settled = True
            if public.code != "CLIENT_DISCONNECTED":
                yield "error", {**(failure or public).payload(), "run_id": str(run.id)}
        finally:
            with anyio.CancelScope(shield=True):
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                if handle is not None:
                    await handle.response.aclose()
                if not settled:
                    await self.settle_failure(
                        user_id, session_id, run.id, asyncio.CancelledError(),
                        max(0, int((time.monotonic() - started) * 1000)), usage,
                    )

    async def chat(self, user_id: int, session_id: UUID, body: SessionChatRequest) -> SessionChatResponse:
        # Includes headroom for history reads and final persistence. A crashed task
        # is failed on the next chat after this lease, never promoted to success.
        lease = (self.settings.llm_timeout_seconds + 30
                 + 8 * self.settings.database_statement_timeout_ms / 1000
                 + 4 * self.settings.database_timeout_seconds)
        run = await self.repository.start_run(user_id, session_id, body.content, self.settings.llm_model, lease)
        started = time.monotonic()
        reply: ChatResponse | None = None
        try:
            history = await self.repository.history(user_id, session_id, self.settings.context_max_turns)
            request = ChatRequest(
                messages=build_context(history, body.content, self.settings),
                temperature=body.temperature, max_tokens=body.max_tokens,
            )
            started = time.monotonic()
            reply = await self.llm.complete(request)
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            if reply.finish_reason != "stop" or not reply.content.strip() or len(reply.content) > 200000:
                raise LLMError("LLM_INCOMPLETE_RESPONSE", "模型未返回完整的有效回复，请重新提问。", 502, True)
            assistant_id = await self.repository.complete_run(user_id, session_id, run.id, reply, elapsed_ms)
            return SessionChatResponse(
                **reply.model_dump(), run_id=run.id, user_message_id=run.user_message_id,
                assistant_message_id=assistant_id,
            )
        except asyncio.CancelledError:
            try:
                with anyio.CancelScope(shield=True):
                    await self.repository.fail_run(
                        user_id, session_id, run.id, "RUN_CANCELLED", status="cancelled",
                        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                        usage=reply.usage if reply else None,
                    )
            except Exception as error:
                logger.error("Cancelled run persistence failed: %s", type(error).__name__)
            raise
        except Exception as error:
            code = error.code if isinstance(error, AppError) else "INTERNAL_ERROR"
            try:
                await self.repository.fail_run(
                    user_id, session_id, run.id, code,
                    duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                    usage=reply.usage if reply else None,
                )
            except Exception as cleanup_error:
                logger.error("Run failure persistence failed: %s", type(cleanup_error).__name__)
                raise AppError("RUN_STATE_UNCERTAIN", "任务状态暂时无法保存，请稍后查看会话。", 503, True) from None
            if isinstance(error, AppError):
                raise
            logger.error("Session chat failed: %s", type(error).__name__)
            raise AppError("INTERNAL_ERROR", "会话生成失败，请稍后重试。", 500) from None
