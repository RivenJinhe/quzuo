"""请求与响应模型。字段约束对齐需求文档 BE-120 ~ BE-165。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.services.plan_text import public_sections

TaskType = Literal["review", "travel", "activity"]
SourceKind = Literal["web", "courseware", "manual"]
MaterialStatus = Literal["citable", "link_only", "unusable"]


class CamelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- 材料 ----------
def material_status(excerpt: str, source_url: str | None) -> MaterialStatus:
    if excerpt.strip():
        return "citable"
    return "link_only" if source_url else "unusable"


def validate_material_content(
    excerpt: str, source_kind: SourceKind, source_url: str | None, source_file: str | None, status: MaterialStatus,
) -> None:
    if source_url:
        url = urlsplit(source_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username is not None:
            raise ValueError("来源链接必须是有效的 HTTP 或 HTTPS 网址")
    if source_kind == "web" and not source_url:
        raise ValueError("来源为网页时必须给链接")
    if status == "citable" and not excerpt.strip():
        raise ValueError("能直接引用的材料必须提供摘录")
    if status == "link_only" and not source_url:
        raise ValueError("只能打开看的材料必须提供链接")
    if not excerpt.strip() and not source_url and not (source_file and status == "unusable"):
        raise ValueError("请提供摘录、网址或文件来源")


def validate_material_pages(start: int | None, end: int | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("页码结束不能小于开始")


class MaterialCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=40)
    excerpt: str = Field(default="", max_length=2000000)
    source_kind: SourceKind = "manual"
    source_url: str | None = Field(default=None, max_length=2000)
    source_file: str | None = Field(default=None, max_length=200)
    source_page_start: int | None = Field(default=None, ge=1, le=9999)
    source_page_end: int | None = Field(default=None, ge=1, le=9999)
    note: str | None = Field(default=None, max_length=500)
    status: MaterialStatus | None = None

    @model_validator(mode="after")
    def check_content(self) -> MaterialCreate:
        self.status = self.status or material_status(self.excerpt, self.source_url)
        validate_material_content(self.excerpt, self.source_kind, self.source_url, self.source_file, self.status)
        validate_material_pages(self.source_page_start, self.source_page_end)
        return self


class MaterialUpdate(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    topic: str | None = Field(default=None, min_length=1, max_length=40)
    excerpt: str | None = Field(default=None, max_length=2000000)
    source_kind: SourceKind | None = None
    source_url: str | None = Field(default=None, max_length=2000)
    source_file: str | None = Field(default=None, max_length=200)
    source_page_start: int | None = Field(default=None, ge=1, le=9999)
    source_page_end: int | None = Field(default=None, ge=1, le=9999)
    note: str | None = Field(default=None, max_length=500)
    status: MaterialStatus | None = None

    @field_validator("title", "topic", "excerpt", "source_kind", "status")
    @classmethod
    def require_value_if_present(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("不能设为空值")
        return value


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


SUMMARY_CHARS = 200


class MaterialSummaryOut(MaterialOut):
    """列表用的摘要：正文只给开头一段，全文只在详情与生成时取。"""

    chars: int

    @classmethod
    def of(cls, material):
        data = MaterialOut.model_validate(material).model_dump()
        text = material.excerpt or ""
        data["chars"] = len(text)
        data["excerpt"] = text[:SUMMARY_CHARS]
        return cls(**data)


class MaterialListOut(BaseModel):
    items: list[MaterialSummaryOut]
    next_cursor: str | None = None


class MaterialBatchOut(BaseModel):
    """按 ids 批量取材料：生成要用全文，这里不截断。"""

    items: list[MaterialOut]


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
    material_limit: int = 20


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
    base_generation_id: uuid.UUID | None = None
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
    usage: dict[str, Any] | None = None
    changes: list[Any] | None = None
    options: list[Any] | None = None
    created_at: datetime
    finished_at: datetime | None

    @computed_field
    @property
    def result_sections(self) -> list[dict]:
        if self.status == "succeeded" and self.result:
            return public_sections(self.result, self.validation or {})
        return []


# ---------- 方案 ----------
class PlanIn(BaseModel):
    conversation_id: uuid.UUID
    generation_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
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
