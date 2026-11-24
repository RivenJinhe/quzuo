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
from app.models import Conversation, Generation, Material, Plan, TrialUser
from app.schemas import (ConditionsUpdate, ConfirmMaterialsIn, ConversationCreate, ConversationOut,
                         GenerationCreate, GenerationOut, RetrieveOut, TurnIn)
from app.services import retrieval
from app.services import turns as turns_svc
from app.services.generate import restore_conversation_status, run_generation
from app.services.plan_text import public_body

router = APIRouter(prefix="/api/v1", tags=["conversations"])

_rate: dict[str, list[float]] = {}
_BASE_GENERATION_KEYS = ("base_generation_id", "base_generation_version")


def _now():
    return datetime.now(timezone.utc)


def _material_limit() -> int:
    return min(20, settings.max_materials_per_generation)


def _rate_limit(owner_id, per_minute: int, count: int):
    import time

    key = str(owner_id)
    now = time.time()
    bucket = [t for t in _rate.get(key, []) if now - t < 60]
    if len(bucket) + count > per_minute:
        raise errors.ApiError(429, errors.RATE_LIMITED, "生成太频繁，请稍后再试")
    bucket.extend([now] * count)
    _rate[key] = bucket


def get_owned_conv(db: Session, trial: TrialUser, cid: uuid.UUID, *, lock: bool = False) -> Conversation:
    conv = db.get(Conversation, cid, with_for_update=lock, populate_existing=lock)
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
    by_id = {m.id: m for m in rows}
    missing = [str(i) for i in ids if i not in by_id]
    if missing:
        raise errors.ApiError(404, errors.NOT_FOUND, "有材料不存在或不属于你", "material_ids", {"missing": missing})
    return [by_id[i] for i in ids]


def _unusable_reason(material: Material) -> str | None:
    if material.status == "link_only":
        return "只有链接，请先补上可引用的原文"
    if material.status != "citable" or not (material.excerpt or "").strip():
        return "没有可引用的原文"
    return None


def _candidates(db: Session, trial: TrialUser, goal: str, conditions: dict,
                selected_ids: list[uuid.UUID]) -> list[dict]:
    rows = db.scalars(
        select(Material).where(Material.owner_id == trial.id, Material.deleted_at.is_(None))
    ).all()
    by_id = {m.id: m for m in rows}
    selected = []
    for mid in selected_ids:
        m = by_id.pop(mid, None)
        if m is None:
            continue
        reason = _unusable_reason(m)
        note = (m.note or "").strip()
        if note:
            first_line = note.splitlines()[0]
            note = first_line[:60] + ("…" if len(first_line) > 60 else "")
        selection = "你写过：「%s」" % note if note else "你手选了这条材料"
        selected.append({
            "material_id": str(m.id), "title": m.title, "topic": m.topic,
            "status": m.status, "usable": reason is None, "preselected": True,
            "reason": reason or selection,
            "unusable_reason": reason,
        })
    remaining = max(0, _material_limit() - len(selected))
    if not remaining:
        return selected
    mats = [
        {"id": m.id, "title": m.title, "topic": m.topic, "excerpt": m.excerpt,
         "status": "unusable" if _unusable_reason(m) and m.status == "citable" else m.status,
         "note": m.note}
        for m in by_id.values()
    ]
    ranked = retrieval.retrieve(mats, goal, conditions, limit=remaining)
    for candidate in ranked:
        candidate["unusable_reason"] = _unusable_reason(by_id[uuid.UUID(candidate["material_id"])])
    return selected + ranked


def _user_retained(retained: dict | None) -> dict:
    return {key: value for key, value in (retained or {}).items() if key not in _BASE_GENERATION_KEYS}


def _merged_conditions(task_type: str, stored: dict | None, explicit: dict | None) -> dict:
    explicit = explicit or {}
    conditions = {**(stored or {}), **explicit}
    if "budget_per_person" in explicit and "budget_total" not in explicit:
        conditions.pop("budget_total", None)
    duration_field = {"review": "duration_minutes", "activity": "total_minutes"}.get(task_type)
    if duration_field:
        if duration_field in explicit:
            conditions["available_minutes"] = explicit[duration_field]
        elif "available_minutes" in explicit:
            conditions[duration_field] = explicit["available_minutes"]
    return turns_svc.normalize_conditions(task_type, conditions)


def _supersede_pending(db: Session, conv: Conversation) -> None:
    db.execute(
        update(Generation)
        .where(Generation.conversation_id == conv.id, Generation.status.in_(["pending", "running"]))
        .values(status="superseded", finished_at=_now())
    )


@router.post("/conversations", status_code=201)
def create_conversation(payload: ConversationCreate, trial: TrialUser = Depends(require_writable),
                        db: Session = Depends(get_db)):
    """BE-230 创建对话即检索：立刻给出拟采用材料供确认。"""
    material_ids = list(dict.fromkeys(payload.material_ids))
    _load_materials(db, trial, material_ids)
    parsed = turns_svc.parse(payload.goal, payload.task_type, {}, {})
    parsed["conditions"].pop("user_notes", None)
    conv = Conversation(
        owner_id=trial.id,
        goal=payload.goal,
        task_type=payload.task_type,
        material_ids=material_ids,
        conditions=_merged_conditions(payload.task_type, parsed["conditions"], payload.conditions),
        retained={**parsed["retained"], **_user_retained(payload.retained)},
        status="retrieved",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    cands = _candidates(db, trial, conv.goal, conv.conditions or {}, material_ids)
    return {
        "conversation": ConversationOut.model_validate(conv).model_dump(mode="json"),
        "candidates": cands,
        "total": len(cands),
        "material_limit": _material_limit(),
    }


@router.get("/conversations/{cid}", response_model=ConversationOut)
def get_conversation(cid: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    return get_owned_conv(db, trial, cid)


@router.get("/conversations/{cid}/retrieve", response_model=RetrieveOut)
def re_retrieve(cid: uuid.UUID, q: str | None = Query(default=None, max_length=500),
                trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    conv = get_owned_conv(db, trial, cid)
    cands = _candidates(db, trial, q or conv.goal, conv.conditions or {}, conv.material_ids or [])
    return RetrieveOut(conversation_id=conv.id, candidates=cands, total=len(cands), material_limit=_material_limit())


@router.post("/conversations/{cid}/materials/confirm", response_model=ConversationOut)
def confirm_materials(cid: uuid.UUID, payload: ConfirmMaterialsIn,
                      trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """BE-232 确认拟采用材料：只保留 use=true 的，其余回知识库不删。"""
    conv = get_owned_conv(db, trial, cid, lock=True)
    chosen = list(dict.fromkeys(r.id for r in payload.materials if r.use))
    _load_materials(db, trial, chosen)
    conv.material_ids = chosen
    conv.status = "confirmed"
    conv.version += 1
    conv.updated_at = _now()
    _supersede_pending(db, conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.patch("/conversations/{cid}/conditions", response_model=ConversationOut)
def update_conditions(cid: uuid.UUID, payload: ConditionsUpdate,
                      trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """BE-234 同一对话内改条件：version+1，旧的生成一律作废（以最后一次为准）。"""
    conv = get_owned_conv(db, trial, cid, lock=True)
    if payload.version != conv.version:
        raise errors.ApiError(409, errors.VERSION_CONFLICT, "这个对话刚被改过，请刷新后再改", "version",
                              {"current_version": conv.version})
    if payload.conditions is not None:
        conv.conditions = _merged_conditions(conv.task_type, conv.conditions, payload.conditions)
    if payload.retained is not None:
        conv.retained = {**(conv.retained or {}), **_user_retained(payload.retained)}
    conv.version += 1
    conv.status = "confirmed"
    conv.updated_at = _now()
    _supersede_pending(db, conv)
    db.commit()
    db.refresh(conv)
    return conv


def _prior_generation(db: Session, conv: Conversation,
                      selected_id: uuid.UUID | None = None) -> Generation | None:
    retained = conv.retained or {}
    stored_id = retained.get("base_generation_id")
    try:
        saved_id = uuid.UUID(stored_id) if isinstance(stored_id, str) else None
    except ValueError:
        saved_id = None
    resumed_version = retained.get("base_generation_version")
    stmt = select(Generation).where(
        Generation.conversation_id == conv.id,
        Generation.owner_id == conv.owner_id,
        Generation.status == "succeeded",
        Generation.conversation_version <= conv.version,
    )
    if saved_id and type(resumed_version) is int and 0 < resumed_version <= conv.version:
        stmt = stmt.where(Generation.conversation_version >= resumed_version)
    if selected_id is not None and selected_id != saved_id:
        selected = db.scalar(stmt.where(Generation.id == selected_id))
        if selected is None:
            raise errors.ApiError(409, errors.STALE_GENERATION, "所选草稿已失效，请刷新后再修改",
                                  "base_generation_id")
        return selected
    if selected_id is None:
        latest = db.scalar(stmt.order_by(Generation.created_at.desc(), Generation.id.desc()).limit(1))
        if latest is not None:
            return latest
    if saved_id is None:
        return None
    saved = db.get(Generation, saved_id)
    if (saved is not None and saved.owner_id == conv.owner_id
            and saved.conversation_id == conv.id and saved.status == "succeeded"
            and saved.conversation_version <= conv.version):
        return saved
    return None


def _previous_context(db: Session, conv: Conversation,
                      previous: Generation | None) -> dict | None:
    if previous is None or not previous.result:
        return None
    body = db.scalar(
        select(Plan.body).where(
            Plan.owner_id == conv.owner_id, Plan.conversation_id == conv.id,
            Plan.generation_id == previous.id, Plan.deleted_at.is_(None),
        )
    )
    result = previous.result
    return {
        "generation_id": str(previous.id),
        "body": body if body is not None else public_body(result, previous.validation or {}),
        "task_type": result.get("task_type"),
        "timeline": result.get("timeline"),
        "items": [
            {key: item.get(key) for key in ("title", "detail", "minutes", "must_keep")}
            for item in (result.get("items") or []) if isinstance(item, dict)
        ],
        "materials_needed": [
            {key: material.get(key) for key in ("name", "qty")}
            for material in (result.get("materials_needed") or []) if isinstance(material, dict)
        ],
    }


def _enqueue_generation(conv: Conversation, body: GenerationCreate, trial: TrialUser, db: Session,
                        previous: Generation | None):
    cond = _merged_conditions(conv.task_type, conv.conditions, body.conditions)
    material_ids = list(dict.fromkeys(body.material_ids if body.material_ids is not None
                                     else conv.material_ids or []))
    if not material_ids:
        raise errors.ApiError(422, errors.SOURCE_INSUFFICIENT, "至少要选一条材料才能生成")
    if len(material_ids) > _material_limit():
        raise errors.ApiError(422, errors.VALIDATION_ERROR, "一次最多参考 %d 条材料" % _material_limit(),
                              "material_ids")
    mats = _load_materials(db, trial, material_ids)
    link_only = [str(m.id) for m in mats if m.status == "link_only"]
    unusable = [str(m.id) for m in mats if _unusable_reason(m)]
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
    _rate_limit(trial.id, settings.gen_rate_per_minute, len(specs))

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
        "material_versions": {str(m.id): m.version for m in mats},
        "conditions": cond,
        "retained": {**_user_retained(conv.retained), **_user_retained(body.retained)},
        "pending_facts": body.pending_facts or {},
        "available_minutes": available,
        "people": cond.get("people"),
        "budget_per_person": cond.get("budget_per_person"),
        "start_time": cond.get("start_time"),
        "legs": cond.get("legs"),
        "previous_result": _previous_context(db, conv, previous),
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


@router.post("/conversations/{cid}/generations", status_code=202)
async def create_generation(cid: uuid.UUID, body: GenerationCreate | None = None,
                            trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """异步生成并返回轮询地址，支持同一批请求比较模型。"""
    conv = get_owned_conv(db, trial, cid, lock=True)
    previous = _prior_generation(db, conv)
    _supersede_pending(db, conv)
    return _enqueue_generation(conv, body or GenerationCreate(), trial, db, previous)


@router.post("/conversations/{cid}/turns", status_code=202)
async def add_turn(cid: uuid.UUID, payload: TurnIn,
                   trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    """BE-238 自然语言追问：一句话改条件，按最后一次输入重排。

    - 解析结果立刻写回对话（version+1），进行中的旧生成一律作废；
    - regenerate=true 时当场重排，返回生成句柄，结构与 POST /generations 一致。
    """
    conv = get_owned_conv(db, trial, cid, lock=True)
    if payload.version is not None and payload.version != conv.version:
        raise errors.ApiError(409, errors.VERSION_CONFLICT, "这个对话刚被改过，请刷新后再发",
                              "version", {"current_version": conv.version})

    previous = _prior_generation(db, conv, payload.base_generation_id)
    parser_conditions = dict(conv.conditions or {})
    use_prior_items = "items" not in parser_conditions and previous is not None
    if use_prior_items:
        parser_conditions["items"] = (previous.result or {}).get("items") or []
    parsed = turns_svc.parse(payload.text, conv.task_type, parser_conditions, conv.retained or {})
    if use_prior_items:
        parsed["conditions"].pop("items", None)
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
    _supersede_pending(db, conv)

    out = {
        "conversation": ConversationOut.model_validate(conv).model_dump(mode="json"),
        "turn": turn,
        "summary": parsed["summary"],
        "applied": parsed["applied"],
        "unresolved": parsed["unresolved"],
        "generation": None,
    }
    if not payload.regenerate:
        db.commit()
        return out

    try:
        gen = _enqueue_generation(conv, GenerationCreate(model_ids=payload.model_ids), trial, db, previous)
    except errors.ApiError as exc:
        if exc.status not in (404, 409, 422, 429):
            raise
        db.commit()
        out["unresolved"] = parsed["unresolved"] + [exc.message]
        return out
    out["generation"] = gen
    out["conversation"] = ConversationOut.model_validate(conv).model_dump(mode="json")
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
    conv = get_owned_conv(db, trial, gen.conversation_id, lock=True)
    db.refresh(gen, with_for_update=True)
    if gen.status in ("pending", "running"):
        gen.status = "cancelled"
        gen.finished_at = _now()
        restore_conversation_status(db, conv)
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
