"""Validated approval and non-sensitive learning memory contracts."""

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator, field_validator


def canonical(value: object) -> str:
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def is_memory_request(question: str) -> bool:
    return bool(re.search(r"记住|记下|保存.{0,12}(?:偏好|习惯|目标|记忆)|(?:以后|今后).{0,20}(?:请|回答|用|讲解)",question))


def safe_learning_text(value: str) -> str:
    value=unicodedata.normalize("NFKC",value).strip()
    sensitive=(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|\b1[3-9]\d{9}\b|\b\d{17}[\dXx]\b|"
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{12,}|"
        r"(?i:bearer\s|api[_ -]?key|password|database_url)|身份证|银行卡|家庭住址|病史|诊断|"
        r"(?:我的|账号|登录|用户).{0,6}(?:密码|密钥)")
    if not value or re.search(sensitive,value) or any(ord(c)<32 and c not in "\n\t" for c in value):
        raise ValueError("Only non-sensitive learning preferences, goals and habits are supported")
    return value


MemoryType=Literal["preference","learning_goal","study_habit"]
MemoryKey=Literal["preferred_language","answer_style","learning_goal","study_habit"]


class MemoryValue(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True,str_strip_whitespace=True)
    memory_type: MemoryType
    key: MemoryKey
    content: str=Field(min_length=2,max_length=500)

    @model_validator(mode="after")
    def learning_only(self) -> "MemoryValue":
        expected="preference" if self.key in {"preferred_language","answer_style"} else self.key
        if self.memory_type!=expected:
            raise ValueError("Memory type and key do not match")
        self.content=safe_learning_text(self.content)
        return self


class MemoryCreate(MemoryValue):
    confirmed: Literal[True]
    source_session_id: UUID | None=Field(default=None,strict=False)
    expires_at: AwareDatetime | None=Field(default=None,strict=False)

    @field_validator("confirmed",mode="before")
    @classmethod
    def explicit_confirmation(cls,value):
        if value is not True:raise ValueError("Explicit user confirmation is required")
        return value


class MemoryUpdate(MemoryCreate):
    version: int=Field(ge=1)


class MemoryProposal(MemoryValue):
    source_quote: str=Field(min_length=2,max_length=1000)

    @model_validator(mode="after")
    def grounded(self) -> "MemoryProposal":
        self.source_quote=safe_learning_text(self.source_quote)
        if self.content not in self.source_quote:
            raise ValueError("Memory content must be a literal part of the user's quote")
        return self


class DemoPlan(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True,str_strip_whitespace=True)
    title: str=Field(min_length=2,max_length=80)
    outline: str=Field(min_length=5,max_length=1200)

    @model_validator(mode="after")
    def non_sensitive(self) -> "DemoPlan":
        safe_learning_text(self.title);safe_learning_text(self.outline)
        return self


class ApprovalDecision(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    action: Literal["approve","reject","cancel"]
    expected_hash: str=Field(pattern=r"^[0-9a-f]{64}$")


class MemoryInfo(BaseModel):
    id: UUID
    memory_type: MemoryType
    key: MemoryKey
    content: str
    version: int
    status: Literal["active","deleted","expired"]
    source_kind: Literal["manual","user_quote"]
    source_session_id: UUID | None
    source_message_id: UUID | None
    source_quote: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
