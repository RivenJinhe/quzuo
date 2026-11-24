"""submit_plan 的参数定义，同时用于声明模型工具和读取候选方案。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import TaskType


class ToolArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


class SourceQuote(ToolArguments):
    material_id: str
    excerpt: str = Field(description="同一段落内连续出现的原文")
    page: int | None = Field(default=None, description="有原文依据的课件页码")


class PlanItem(ToolArguments):
    title: str
    minutes: int
    detail: str
    citations: list[SourceQuote]
    must_keep: bool = False
    kind: str | None = Field(default=None, description="单列出行路程时填 outbound 或 return")
    requires_seat_change: bool = False


class MaterialNeeded(ToolArguments):
    name: str
    qty: int | float | None = None
    item_title: str | None = Field(default=None, description="使用此物料的环节")
    unit_cost: float | None = None
    citations: list[SourceQuote] = Field(default_factory=list)


class PlanCost(ToolArguments):
    title: str
    per_person: float | None = None
    citations: list[SourceQuote] = Field(default_factory=list)


class TravelTimeline(ToolArguments):
    start: str | None = None
    arrive: str | None = None
    leave: str | None = None
    end: str | None = None


class PlanSubmission(ToolArguments):
    task_type: TaskType
    headline: str
    items: list[PlanItem]
    materials_needed: list[MaterialNeeded] = Field(default_factory=list)
    costs: list[PlanCost] = Field(default_factory=list)
    timeline: TravelTimeline | None = None
    pending_facts: list[str] = Field(default_factory=list)
    private_note: str = ""
    rationale: str = ""


SUBMIT_PLAN_NAME = "submit_plan"
SUBMIT_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": SUBMIT_PLAN_NAME,
        "description": "提交一份完整候选方案，由后端复核引用、排程和费用。",
        "parameters": PlanSubmission.model_json_schema(),
    },
}
