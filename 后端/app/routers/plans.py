"""方案接口（BE-250 ~ BE-257）：保存 UPSERT、回看、复制公开版。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import errors
from app.db import get_db
from app.deps import require_readable, require_writable
from app.models import Citation, Conversation, Generation, Material, Plan, TrialUser
from app.schemas import ConversationOut, CopyOut, PlanIn, PlanListOut, PlanOut
from app.services.plan_text import public_body

router = APIRouter(prefix="/api/v1/plans", tags=["plans"])


def _now():
    return datetime.now(timezone.utc)


def get_owned_plan(db: Session, trial: TrialUser, pid: uuid.UUID, *, lock: bool = False) -> Plan:
    p = db.get(Plan, pid, with_for_update=lock, populate_existing=lock)
    if p is None or p.deleted_at is not None or p.owner_id != trial.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "方案不存在")
    return p


def _citation_public(c: Citation) -> dict:
    page = None
    if c.source_page_start:
        page = c.source_page_start if not c.source_page_end or c.source_page_end == c.source_page_start             else "%s-%s" % (c.source_page_start, c.source_page_end)
    jump = c.source_url if (c.source_url or "").startswith(("http://", "https://")) else None
    return {
        "id": str(c.id),
        "material_id": str(c.material_id) if c.material_id else None,
        "material_title": c.material_title,
        "claim_text": c.claim_text,
        "excerpt": c.excerpt_snapshot,
        "source_kind": c.source_kind,
        "source_url": c.source_url,
        "source_file": c.source_file,
        "source_page": page,
        "jump_url": jump,
    }


@router.post("", response_model=PlanOut)
def save_plan(payload: PlanIn, response: Response, trial: TrialUser = Depends(require_writable),
              db: Session = Depends(get_db)):
    """BE-250 / BE-251 保存：同一对话重复保存 = 更新原记录。"""
    conv = db.get(Conversation, payload.conversation_id, with_for_update=True, populate_existing=True)
    if conv is None or conv.deleted_at is not None or conv.owner_id != trial.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "对话不存在")
    gen = db.get(Generation, payload.generation_id)
    if gen is None or gen.owner_id != trial.id or gen.conversation_id != conv.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "生成结果不存在")
    if gen.status != "succeeded":
        raise errors.ApiError(409, errors.STALE_GENERATION, "这份结果已失效（被取消或旧版本），请用最新一次生成再保存",
                              "generation_id", {"status": gen.status})
    if gen.conversation_version != conv.version:
        raise errors.ApiError(409, errors.STALE_GENERATION, "对话条件改过了，请用最新一次生成再保存",
                              "generation_id", {"generation_version": gen.conversation_version,
                                                 "conversation_version": conv.version})

    result = gen.result or {}
    body = payload.body if payload.body is not None else public_body(result, gen.validation or {})
    if not body.strip():
        raise errors.ApiError(422, errors.VALIDATION_ERROR, "没有可保存的方案正文", "body")
    note = payload.body_private_note if "body_private_note" in payload.model_fields_set else result.get("private_note")
    rationale = payload.rationale if "rationale" in payload.model_fields_set else result.get("rationale")
    conditions = (payload.confirmed_conditions if "confirmed_conditions" in payload.model_fields_set
                  else (gen.request or {}).get("conditions", conv.conditions))
    citation_ids = []
    for c in db.scalars(select(Citation).where(Citation.generation_id == gen.id).order_by(Citation.ordinal)).all():
        citation_ids.append(c.id)

    existing = db.scalar(
        select(Plan).where(Plan.owner_id == trial.id, Plan.conversation_id == conv.id,
                           Plan.deleted_at.is_(None)).with_for_update()
    )
    if existing is None:
        plan = Plan(
            owner_id=trial.id, conversation_id=conv.id, generation_id=gen.id, task_type=conv.task_type,
            title=payload.title, body=body, body_private_note=note,
            rationale=rationale, confirmed_conditions=conditions or {},
            citation_ids=citation_ids,
        )
        db.add(plan)
        response.status_code = 201
    else:
        plan = existing
        plan.generation_id = gen.id
        plan.task_type = conv.task_type
        plan.title = payload.title
        plan.body = body
        plan.body_private_note = note
        plan.rationale = rationale
        plan.confirmed_conditions = conditions or {}
        plan.citation_ids = citation_ids
        plan.version += 1
        plan.updated_at = _now()
        response.status_code = 200
    conv.status = "saved"
    db.commit()
    db.refresh(plan)
    return plan


@router.get("", response_model=PlanListOut)
def list_plans(task_type: str | None = Query(default=None), limit: int = Query(default=20, ge=1, le=50),
               offset: int = Query(default=0, ge=0),
               trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    stmt = select(Plan).where(Plan.owner_id == trial.id, Plan.deleted_at.is_(None))
    if task_type:
        stmt = stmt.where(Plan.task_type == task_type)
    rows = db.scalars(stmt.order_by(Plan.updated_at.desc()).offset(offset).limit(limit + 1)).all()
    has_more = len(rows) > limit
    return PlanListOut(items=rows[:limit], next_cursor=str(offset + limit) if has_more else None)


@router.get("/{pid}", response_model=PlanOut)
def get_plan(pid: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    return get_owned_plan(db, trial, pid)


@router.post("/{pid}/resume", response_model=ConversationOut)
def resume_plan(pid: uuid.UUID, trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """从显式保存的版本恢复编辑条件，后来的未保存草稿不改变方案正文。"""
    plan = get_owned_plan(db, trial, pid)
    conv = db.get(Conversation, plan.conversation_id, with_for_update=True, populate_existing=True)
    if conv is None or conv.owner_id != trial.id or conv.deleted_at:
        raise errors.ApiError(404, errors.NOT_FOUND, "原对话不存在")
    db.refresh(plan, with_for_update=True)
    if plan.deleted_at:
        raise errors.ApiError(404, errors.NOT_FOUND, "方案不存在")
    gen = db.get(Generation, plan.generation_id) if plan.generation_id else None
    if gen is None or gen.owner_id != trial.id or gen.conversation_id != conv.id:
        raise errors.ApiError(409, errors.STALE_GENERATION, "找不到这份方案保存时的生成记录")
    snapshot = gen.request or {}
    ids = [uuid.UUID(str(mid)) for mid in snapshot.get("material_ids", conv.material_ids or [])]
    versions = dict(db.execute(select(Material.id, Material.version).where(
        Material.owner_id == trial.id, Material.id.in_(ids),
        Material.deleted_at.is_(None), Material.status == "citable",
    )).all())
    stored_versions = snapshot.get("material_versions") or {}
    sources_unchanged = all(mid in versions and versions[mid] == stored_versions.get(str(mid), versions[mid])
                            for mid in ids)
    conv.goal = snapshot.get("goal") or conv.goal
    conv.task_type = plan.task_type
    conv.material_ids = [mid for mid in ids if mid in versions]
    conv.conditions = (snapshot.get("conditions") or {}) if plan.confirmed_conditions is None else plan.confirmed_conditions
    conv.version += 1
    conv.retained = {**(snapshot.get("retained") or {}), "base_generation_id": str(gen.id),
                     "base_generation_version": conv.version}
    conv.status = "confirmed" if ids and sources_unchanged else "retrieved"
    conv.updated_at = _now()
    db.execute(update(Generation).where(Generation.conversation_id == conv.id,
               Generation.status.in_(["pending", "running"])).values(status="superseded", finished_at=_now()))
    db.commit()
    db.refresh(conv)
    return conv


@router.patch("/{pid}", response_model=PlanOut)
def update_plan(pid: uuid.UUID, payload: dict, trial: TrialUser = Depends(require_writable),
                db: Session = Depends(get_db)):
    plan = get_owned_plan(db, trial, pid, lock=True)
    version = payload.get("version")
    if version is None or int(version) != plan.version:
        raise errors.ApiError(409, errors.VERSION_CONFLICT, "这份方案刚被改过，请刷新后再改", "version",
                              {"current_version": plan.version})
    for k in ("title", "body", "body_private_note", "rationale"):
        if k in payload and payload[k] is not None:
            setattr(plan, k, payload[k])
    plan.version += 1
    plan.updated_at = _now()
    db.commit()
    db.refresh(plan)
    return plan


@router.post("/{pid}/copy", response_model=CopyOut)
def copy_plan(pid: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    """预览和复制使用同一份已存正文，出处元数据仍供个人页面查阅。"""
    plan = get_owned_plan(db, trial, pid)
    cids = [uuid.UUID(str(c)) for c in (plan.citation_ids or [])]
    rows = []
    if cids:
        rows = db.scalars(select(Citation).where(Citation.id.in_(cids)).order_by(Citation.ordinal)).all()
    cites = [_citation_public(c) for c in rows]
    return CopyOut(title=plan.title, text=plan.body, citations=cites)


@router.delete("/{pid}", status_code=204)
def delete_plan(pid: uuid.UUID, trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    plan = get_owned_plan(db, trial, pid, lock=True)
    plan.deleted_at = _now()
    plan.version += 1
    db.commit()
    return Response(status_code=204)
