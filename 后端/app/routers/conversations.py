"""对话与生成接口（BE-230 ~ BE-237）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import errors
from app.services import providers
from app.config import settings
from app.db import get_db
from app.deps import require_readable, require_writable
from app.models import Conversation, Generation, Material, TrialUser
from app.schemas import (ConditionsUpdate, ConfirmMaterialsIn, ConversationCreate, ConversationOut,
                         GenerationCreate, GenerationOut, RetrieveOut, TurnIn)
from app.services import retrieval
from app.services import turns as turns_svc
from app.services.generate import run_generation

router = APIRouter(prefix="/api/v1", tags=["conversations"])

_rate: dict[str, list[float]] = {}
_IDEMPOTENT: dict[str, tuple[str, dict]] = {}


def _now():
    return datetime.now(timezone.utc)


def _rate_limit(owner_id, per_minute: int):
    import time

    key = str(owner_id)
    now = time.time()
    bucket = [t for t in _rate.get(key, []) if now - t < 60]
    if len(bucket) >= per_minute:
        raise errors.ApiError(429, errors.RATE_LIMITED, "生成太频繁，请稍后再试")
    bucket.append(now)
    _rate[key] = bucket


def get_owned_conv(db: Session, trial: TrialUser, cid: uuid.UUID) -> Conversation:
    conv = db.get(Conversation, cid)
    if conv is None or conv.deleted_at is not None or conv.owner_id != trial.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "对话不存在")
    return conv


def get_owned_gen(db: Session, trial: TrialUser, gid: uuid.UUID) -> Generation:
    gen = db.get(Generation, gid)
    if gen is None or gen.owner_id != trial.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "生成任务不存在")
    return gen


def _load_materials(db: Session, trial: TrialUser, ids: list[uuid.UUID]) -> list[Material]:
    if not ids:
        return []
    rows = db.scalars(
        select(Material).where(Material.owner_id == trial.id, Material.deleted_at.is_(None), Material.id.in_(ids))
    ).all()
    order = {m: i for i, m in enumerate(ids)}
    rows = sorted(rows, key=lambda m: order.get(m.id, 99))
    missing = [str(i) for i in ids if all(str(r.id) != str(i) for r in rows)]
    if missing:
        raise errors.ApiError(404, errors.NOT_FOUND, "有材料不存在或不属于你", "material_ids", {"missing": missing})
    return list(rows)


def _candidates(db: Session, trial: TrialUser, goal: str, conditions: dict) -> list[dict]:
    rows = db.scalars(
        select(Material).where(Material.owner_id == trial.id, Material.deleted_at.is_(None))
    ).all()
    mats = [
        {
            "id": m.id, "title": m.title, "topic": m.topic, "excerpt": m.excerpt,
            "status": m.status, "note": m.note, "source_kind": m.source_kind,
            "source_url": m.source_url, "source_file": m.source_file,
            "source_page_start": m.source_page_start, "source_page_end": m.source_page_end,
        }
        for m in rows
    ]
    return retrieval.retrieve(mats, goal, conditions)


def _mkdir():
    return None


@router.post("/conversations", status_code=201)
def create_conversation(payload: ConversationCreate, trial: TrialUser = Depends(require_writable),
                        db: Session = Depends(get_db)):
    """BE-230 创建对话即检索：立刻给出拟采用材料供确认。"""
    if payload.material_ids:
        _load_materials(db, trial, payload.material_ids)
    conv = Conversation(
        owner_id=trial.id,
        goal=payload.goal,
        task_type=payload.task_type,
        material_ids=payload.material_ids,
        conditions=payload.conditions or {},
        retained=payload.retained or {},
        status="retrieved",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    cands = _candidates(db, trial, conv.goal, conv.conditions or {})
    return {
        "conversation": ConversationOut.model_validate(conv).model_dump(mode="json"),
        "candidates": cands,
        "total": len(cands),
    }


@router.get("/conversations/{cid}", response_model=ConversationOut)
def get_conversation(cid: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    return get_owned_conv(db, trial, cid)


@router.get("/conversations/{cid}/retrieve", response_model=RetrieveOut)
def re_retrieve(cid: uuid.UUID, q: str | None = Query(default=None, max_length=500),
                trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    conv = get_owned_conv(db, trial, cid)
    cands = _candidates(db, trial, q or conv.goal, conv.conditions or {})
    return RetrieveOut(conversation_id=conv.id, candidates=cands, total=len(cands))


@router.post("/conversations/{cid}/materials/confirm", response_model=ConversationOut)
def confirm_materials(cid: uuid.UUID, payload: ConfirmMaterialsIn,
                      trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """BE-232 确认拟采用材料：只保留 use=true 的，其余回知识库不删。"""
    conv = get_owned_conv(db, trial, cid)
    chosen = [r.id for r in payload.materials if r.use]
    _load_materials(db, trial, chosen)
    conv.material_ids = chosen
    conv.status = "confirmed"
    conv.version += 1
    conv.updated_at = _now()
    db.commit()
    db.refresh(conv)
    return conv


@router.patch("/conversations/{cid}/conditions", response_model=ConversationOut)
def update_conditions(cid: uuid.UUID, payload: ConditionsUpdate,
                      trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """BE-234 同一对话内改条件：version+1，旧的生成一律作废（以最后一次为准）。"""
    conv = get_owned_conv(db, trial, cid)
    if payload.version != conv.version:
        raise errors.ApiError(409, errors.VERSION_CONFLICT, "这个对话刚被改过，请刷新后再改", "version",
                              {"current_version": conv.version})
    if payload.conditions is not None:
        conv.conditions = {**(conv.conditions or {}), **payload.conditions}
    if payload.retained is not None:
        conv.retained = {**(conv.retained or {}), **payload.retained}
    conv.version += 1
    conv.status = "confirmed"
    conv.updated_at = _now()
    db.execute(
        update(Generation)
        .where(Generation.conversation_id == conv.id, Generation.status.in_(["pending", "running"]))
        .values(status="superseded")
    )
    db.commit()
    db.refresh(conv)
    return conv


@router.post("/conversations/{cid}/generations", status_code=202)
async def create_generation(cid: uuid.UUID, body: GenerationCreate | None = None,
                            trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """BE-236 异步生成：202 + 轮询。可选多个模型做效果对比。"""
    conv = get_owned_conv(db, trial, cid)
    body = body or GenerationCreate()
    cond = {**(conv.conditions or {}), **(body.conditions or {})}
    material_ids = body.material_ids or conv.material_ids or []
    if not material_ids:
        raise errors.ApiError(422, errors.SOURCE_INSUFFICIENT, "至少要选一条材料才能生成")
    mats = _load_materials(db, trial, material_ids)
    link_only = [str(m.id) for m in mats if m.status == "link_only"]
    unusable = [str(m.id) for m in mats if m.status not in ("citable",)]
    if link_only:
        raise errors.ApiError(422, errors.SOURCE_INSUFFICIENT,
                              "有材料只有链接、没有原文，请先补上原文再生成",
                              "material_ids", {"link_only": link_only})
    if unusable:
        raise errors.ApiError(422, errors.SOURCE_INSUFFICIENT, "有材料不可引用", "material_ids", {"unusable": unusable})

    raw_ids = body.model_ids or [providers.default_model_id()]
    specs = []
    for mid in raw_ids:
        mid = (mid or "").strip()
        if not mid:
            continue
        spec = providers.resolve(mid)
        if spec is None:
            raise errors.ApiError(422, errors.VALIDATION_ERROR, "未知模型：%s" % mid, "model_ids")
        ok, why = providers.availability(spec)
        if not ok:
            raise errors.ApiError(422, errors.VALIDATION_ERROR, "模型 %s 不可用：%s" % (mid, why), "model_ids")
        if spec.id not in [x.id for x in specs]:
            specs.append(spec)
    if not specs:
        raise errors.ApiError(422, errors.VALIDATION_ERROR, "至少要选一个模型", "model_ids")
    if len(specs) > settings.llm_max_compare:
        raise errors.ApiError(422, errors.VALIDATION_ERROR,
                              "一次最多对比 %d 个模型" % settings.llm_max_compare, "model_ids")
    for _ in specs:
        _rate_limit(trial.id, settings.gen_rate_per_minute)

    available = cond.get("available_minutes")
    if available is None:
        if conv.task_type == "review":
            available = cond.get("duration_minutes")
        elif conv.task_type == "activity":
            available = cond.get("total_minutes")
    request_base = {
        "goal": conv.goal,
        "task_type": conv.task_type,
        "material_ids": [str(i) for i in material_ids],
        "conditions": cond,
        "retained": body.retained or conv.retained or {},
        "pending_facts": body.pending_facts or {},
        "available_minutes": available,
        "people": cond.get("people"),
        "budget_per_person": cond.get("budget_per_person"),
        "start_time": cond.get("start_time"),
        "legs": cond.get("legs"),
    }
    gens = []
    for spec in specs:
        gen = Generation(
            owner_id=trial.id,
            conversation_id=conv.id,
            conversation_version=conv.version,
            status="pending",
            model=spec.id,
            request={**request_base, "model_id": spec.id},
        )
        db.add(gen)
        gens.append(gen)
    conv.status = "generating"
    db.commit()
    for g in gens:
        db.refresh(g)

    import asyncio

    for g in gens:
        asyncio.create_task(run_generation(g.id))

    if len(gens) == 1:
        g = gens[0]
        return {
            "id": str(g.id),
            "conversation_id": str(conv.id),
            "status": "pending",
            "model_id": specs[0].id,
            "conversation_version": conv.version,
            "poll_interval_seconds": settings.poll_interval_seconds,
            "poll_url": "/api/v1/generations/%s" % g.id,
        }
    return {
        "conversation_id": str(conv.id),
        "status": "pending",
        "conversation_version": conv.version,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "comparisons": [
            {"generation_id": str(g.id), "model_id": sp.id, "status": "pending",
             "poll_url": "/api/v1/generations/%s" % g.id}
            for g, sp in zip(gens, specs)
        ],
    }


@router.post("/conversations/{cid}/turns", status_code=202)
async def add_turn(cid: uuid.UUID, payload: TurnIn,
                   trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """BE-238 自然语言追问：一句话改条件，按最后一次输入重排。

    - 解析结果立刻写回对话（version+1），进行中的旧生成一律作废；
    - regenerate=true 时当场重排，返回生成句柄，结构与 POST /generations 一致。
    """
    conv = get_owned_conv(db, trial, cid)
    if payload.version is not None and payload.version != conv.version:
        raise errors.ApiError(409, errors.VERSION_CONFLICT, "这个对话刚被改过，请刷新后再发",
                              "version", {"current_version": conv.version})

    parsed = turns_svc.parse(payload.text, conv.task_type, conv.conditions or {}, conv.retained or {})
    conv.conditions = parsed["conditions"]
    conv.retained = parsed["retained"]
    conv.version += 1
    conv.status = "confirmed"
    conv.updated_at = _now()
    turn = {
        "at": _now().isoformat(),
        "version": conv.version,
        "text": payload.text,
        "summary": parsed["summary"],
        "applied": parsed["applied"],
        "unresolved": parsed["unresolved"],
    }
    conv.turns = list(conv.turns or []) + [turn]
    db.execute(
        update(Generation)
        .where(Generation.conversation_id == conv.id, Generation.status.in_(["pending", "running"]))
        .values(status="superseded")
    )
    db.commit()
    db.refresh(conv)

    out = {
        "conversation": ConversationOut.model_validate(conv).model_dump(mode="json"),
        "turn": turn,
        "summary": parsed["summary"],
        "applied": parsed["applied"],
        "unresolved": parsed["unresolved"],
        "generation": None,
    }
    if not payload.regenerate:
        return out

    try:
        gen = await create_generation(conv.id, GenerationCreate(model_ids=payload.model_ids), trial, db)
    except errors.ApiError as exc:
        if exc.status not in (404, 409, 422, 429):
            raise
        out["unresolved"] = parsed["unresolved"] + [exc.message]
        return out
    out["generation"] = gen
    if isinstance(gen, dict) and gen.get("comparisons"):
        out["comparisons"] = gen["comparisons"]
    return out


@router.get("/generations/{gid}", response_model=GenerationOut)
def get_generation(gid: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    gen = get_owned_gen(db, trial, gid)
    out = GenerationOut.model_validate(gen)
    return out


@router.post("/generations/{gid}/cancel")
def cancel_generation(gid: uuid.UUID, trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    gen = get_owned_gen(db, trial, gid)
    if gen.status in ("pending", "running"):
        gen.status = "cancelled"
        gen.finished_at = _now()
        db.commit()
    return {"id": str(gen.id), "status": gen.status}


@router.get("/conversations/{cid}/generations")
def list_generations(cid: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    conv = get_owned_conv(db, trial, cid)
    rows = db.scalars(
        select(Generation).where(Generation.conversation_id == conv.id).order_by(Generation.created_at.desc()).limit(20)
    ).all()
    return {
        "items": [
            {
                "id": str(g.id), "status": g.status, "conversation_version": g.conversation_version,
                "block_reason": g.block_reason, "error_code": g.error_code,
                "created_at": g.created_at.isoformat(), "finished_at": g.finished_at.isoformat() if g.finished_at else None,
            }
            for g in rows
        ]
    }
