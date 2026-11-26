"""请求与响应模型。字段约束对齐需求文档 BE-120 ~ BE-165。"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.services.plan_text import public_sections

TaskType = Literal["review", "travel", "activity"]
SourceKind = Literal["web", "courseware", "manual"]
MaterialStatus = Literal["citable", "link_only", "unusable"]


class CamelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- 日历 ----------
class CalendarEventIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    start_date: date
    end_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    location: str | None = Field(default=None, max_length=240)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("start_time", "end_time")
    @classmethod
    def calendar_clock(cls, value: time | None) -> time | None:
        if value is not None and (value.tzinfo or value.second or value.microsecond):
            raise ValueError("时间请使用本地小时和分钟")
        return value

    @model_validator(mode="after")
    def calendar_range(self):
        end_date = self.end_date or self.start_date
        if end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        if self.end_time is not None and (
            self.start_time is None or (end_date == self.start_date and self.end_time <= self.start_time)
        ):
            raise ValueError("请输入开始时间，同一天的结束时间须晚于开始时间")
        return self


class CalendarEventUpdate(CalendarEventIn):
    version: int = Field(ge=1)


class CalendarEventOut(CalendarEventIn, CamelOut):
    id: uuid.UUID
    end_date: date
    version: int


class CalendarImportEvent(CalendarEventIn):
    previous_date: date | None = None
    previous_time: time | None = None
    previous_title: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("previous_time")
    @classmethod
    def previous_clock(cls, value: time | None) -> time | None:
        return cls.calendar_clock(value)

    @model_validator(mode="after")
    def previous_event_has_date(self):
        if self.previous_date is None and (self.previous_title is not None or self.previous_time is not None):
            raise ValueError("修改已有日程时需要原日期")
        return self


class CalendarImportResult(BaseModel):
    created: int
    updated: int
    unchanged: int
    dates: list[date]
    warnings: list[str]


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
