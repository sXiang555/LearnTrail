"""Encrypted PostgreSQL checkpoints behind a session ownership boundary."""

import os
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from learntrail_agent.errors import AppError
from learntrail_agent.storage.pool import transaction
from learntrail_agent.storage.repositories import SessionRepository
from learntrail_agent.observability import observed


def thread_id(session_id: UUID) -> str:
    return f"learntrail:{session_id}"


def load_checkpoint_key(path: Path) -> bytes:
    """Create once, never replace a missing/invalid key for existing ciphertext."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as out:
            out.write(os.urandom(32))
            out.flush()
            os.fsync(out.fileno())
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        key = source.read(33)
    if len(key) != 32 or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise RuntimeError("Checkpoint key must be a private 32-byte file owned by this user")
    return key


class CheckpointCipher:
    """Only graph values are serialized; credentials belong to runtime closures."""

    def __init__(self, key: bytes) -> None:
        self.cipher = AESGCM(key)
        # Explicitly disable permissive object loading and pickle fallback.
        self.json = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=None)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        kind, raw = self.json.dumps_typed(obj)
        nonce = os.urandom(12)
        label = "aesgcm-v1:" + kind
        return label, nonce + self.cipher.encrypt(nonce, raw, label.encode())

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        label, raw = data
        if not label.startswith("aesgcm-v1:") or len(raw) < 28:
            raise ValueError("Unsupported checkpoint encryption format")
        clear = self.cipher.decrypt(raw[:12], raw[12:], label.encode())
        return self.json.loads_typed((label.removeprefix("aesgcm-v1:"), clear))


class SessionCheckpointer(BaseCheckpointSaver):
    """Every saver operation checks the authenticated owner's fixed session."""

    def __init__(self, saver: BaseCheckpointSaver, sessions: SessionRepository,
                 user_id: int, session_id: UUID) -> None:
        super().__init__(serde=saver.serde)
        self.saver, self.sessions = saver, sessions
        self.user_id, self.session_id = user_id, session_id

    async def _authorize(self, config: dict | None) -> None:
        supplied = (config or {}).get("configurable", {})
        if supplied.get("thread_id") != thread_id(self.session_id) or supplied.get("checkpoint_ns", "") != "":
            raise AppError("CHECKPOINT_FORBIDDEN", "工作流标识不属于此会话。", 403)
        async with transaction(self.sessions.pool) as conn:
            await self.sessions._owned(conn, self.user_id, self.session_id)

    @observed("checkpoint.read")
    async def aget_tuple(self, config: dict) -> Any:
        await self._authorize(config)
        try:
            return await self.saver.aget_tuple(config)
        except psycopg.Error:
            raise AppError("CHECKPOINT_UNAVAILABLE", "工作流存储暂不可用，请检查迁移状态。", 503, True) from None

    async def alist(self, config: dict | None, *, filter: dict | None = None,
                    before: dict | None = None, limit: int | None = None) -> Any:
        await self._authorize(config)
        if before:
            await self._authorize(before)
        async for item in self.saver.alist(config, filter=filter, before=before, limit=limit):
            await self._authorize(config)
            yield item

    @observed("checkpoint.save")
    async def aput(self, config: dict, checkpoint: dict, metadata: dict, new_versions: dict) -> dict:
        await self._authorize(config)
        try:
            return await self.saver.aput(config, checkpoint, metadata, new_versions)
        except psycopg.Error:
            raise AppError("CHECKPOINT_UNAVAILABLE", "无法保存工作流状态。", 503, True) from None

    @observed("checkpoint.writes")
    async def aput_writes(self, config: dict, writes: Any, task_id: str, task_path: str = "") -> None:
        await self._authorize(config)
        try:
            await self.saver.aput_writes(config, writes, task_id, task_path)
        except psycopg.Error:
            raise AppError("CHECKPOINT_UNAVAILABLE", "无法保存工作流节点结果。", 503, True) from None

    def get_next_version(self, current: Any, channel: Any) -> Any:
        return self.saver.get_next_version(current, channel)
