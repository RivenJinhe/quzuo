"""请求与响应模型。字段约束对齐需求文档 BE-120 ~ BE-165。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskType = Literal["review", "travel", "activity"]
SourceKind = Literal["web", "courseware", "manual"]
MaterialStatus = Literal["citable", "link_only", "unusable"]


class CamelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- 材料 ----------
class MaterialCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=40)
    excerpt: str = Field(min_length=1, max_length=20000)
    source_kind: SourceKind = "manual"
    source_url: str | None = Field(default=None, max_length=2000)
    source_file: str | None = Field(default=None, max_length=200)
    source_page_start: int | None = Field(default=None, ge=1, le=9999)
    source_page_end: int | None = Field(default=None, ge=1, le=9999)
    note: str | None = Field(default=None, max_length=500)
    status: MaterialStatus = "citable"

    @field_validator("source_page_end")
    @classmethod
    def check_page_range(cls, v: int | None) -> int | None:
        return v

    def model_post_init(self, __context: Any) -> None:
        if self.source_page_start and self.source_page_end and self.source_page_end < self.source_page_start:
            raise ValueError("页码结束不能小于开始")
        if self.source_kind == "web" and not self.source_url:
            raise ValueError("来源为网页时必须给链接")


class MaterialUpdate(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    topic: str | None = Field(default=None, min_length=1, max_length=40)
    excerpt: str | None = Field(default=None, min_length=1, max_length=20000)
    source_kind: SourceKind | None = None
    source_url: str | None = Field(default=None, max_length=2000)
    source_file: str | None = Field(default=None, max_length=200)
    source_page_start: int | None = Field(default=None, ge=1)
    source_page_end: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=500)
    status: MaterialStatus | None = None


class MaterialOut(CamelOut):
    id: uuid.UUID
    title: str
    topic: str
    excerpt: str
    source_kind: str
    source_url: str | None
    source_file: str | None
    source_page_start: int | None
    source_page_end: int | None
    note: str | None
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class MaterialListOut(BaseModel):
    items: list[MaterialOut]
    next_cursor: str | None = None


# ---------- 对话 ----------
class ConversationCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=500)
    task_type: TaskType
    material_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    conditions: dict[str, Any] = Field(default_factory=dict)
    retained: dict[str, Any] = Field(default_factory=dict)


class ConversationOut(CamelOut):
    id: uuid.UUID
    goal: str
    task_type: str
    material_ids: list[uuid.UUID]
    conditions: dict[str, Any]
    retained: dict[str, Any]
    turns: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class RetrieveOut(BaseModel):
    conversation_id: uuid.UUID
    candidates: list[dict[str, Any]]
    total: int


class MaterialRef(BaseModel):
    id: uuid.UUID
    use: bool = True
    reason: str | None = Field(default=None, max_length=200)


class ConfirmMaterialsIn(BaseModel):
    materials: list[MaterialRef] = Field(min_length=1, max_length=20)


class ConditionsUpdate(BaseModel):
    version: int = Field(ge=1)
    conditions: dict[str, Any] | None = None
    retained: dict[str, Any] | None = None


class TurnIn(BaseModel):
    """BE-238 自然语言追问：一句话改条件，可选当场重排。"""
    text: str = Field(min_length=1, max_length=500)
    version: int | None = Field(default=None, ge=1)
    regenerate: bool = True
    model_ids: list[str] | None = Field(default=None, max_length=8)


# ---------- 生成 ----------
class GenerationCreate(BaseModel):
    material_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    conditions: dict[str, Any] | None = None
    retained: dict[str, Any] | None = None
    pending_facts: dict[str, Any] | None = None
    model_ids: list[str] | None = Field(default=None, max_length=8)


class GenerationOut(CamelOut):
    id: uuid.UUID
    conversation_id: uuid.UUID
    conversation_version: int
    status: str
    result: dict[str, Any] | None
    validation: dict[str, Any] | None
    block_reason: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None


# ---------- 方案 ----------
class PlanIn(BaseModel):
    conversation_id: uuid.UUID
    generation_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    body_private_note: str | None = Field(default=None, max_length=2000)
    rationale: str | None = Field(default=None, max_length=2000)
    confirmed_conditions: dict[str, Any] = Field(default_factory=dict)


class PlanOut(CamelOut):
    id: uuid.UUID
    conversation_id: uuid.UUID
    generation_id: uuid.UUID | None
    task_type: str
    title: str
    body: str
    body_private_note: str | None
    rationale: str | None
    confirmed_conditions: dict[str, Any]
    citation_ids: list[uuid.UUID]
    version: int
    created_at: datetime
    updated_at: datetime


class PlanListOut(BaseModel):
    items: list[PlanOut]
    next_cursor: str | None = None


class CopyOut(BaseModel):
    title: str
    text: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


# ---------- 出处 ----------
class CitationOut(CamelOut):
    id: uuid.UUID
    generation_id: uuid.UUID
    material_id: uuid.UUID | None
    material_title: str | None
    claim_text: str
    excerpt_snapshot: str
    source_kind: str
    source_url: str | None
    source_file: str | None
    source_page_start: int | None
    source_page_end: int | None
    jump_url: str | None = None


# ---------- 账号 ----------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
