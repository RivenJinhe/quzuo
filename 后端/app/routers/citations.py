"""出处展开接口（BE-270 ~ BE-272）：读快照，不回查材料。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.db import get_db
from app.deps import require_readable, require_writable
from app.models import Citation, Generation, Plan, TrialUser

router = APIRouter(prefix="/api/v1", tags=["citations"])


def _public(c: Citation) -> dict:
    page = None
    if c.source_page_start:
        page = c.source_page_start if not c.source_page_end or c.source_page_end == c.source_page_start \
            else "%s-%s" % (c.source_page_start, c.source_page_end)
    jump = c.source_url if (c.source_url or "").startswith(("http://", "https://")) else None
    return {
        "id": str(c.id),
        "generation_id": str(c.generation_id),
        "material_id": str(c.material_id) if c.material_id else None,
        "material_title": c.material_title,
        "claim_text": c.claim_text,
        "excerpt_snapshot": c.excerpt_snapshot,
        "source_kind": c.source_kind,
        "source_url": c.source_url,
        "source_file": c.source_file,
        "source_page_start": c.source_page_start,
        "source_page_end": c.source_page_end,
        "source_page": page,
        "jump_url": jump,
    }


@router.get("/citations/{cid}")
def get_citation(cid: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    c = db.get(Citation, cid)
    if c is None or c.owner_id != trial.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "出处不存在")
    return _public(c)


@router.get("/generations/{gid}/citations")
def list_generation_citations(gid: uuid.UUID, trial: TrialUser = Depends(require_readable),
                              db: Session = Depends(get_db)):
    gen = db.get(Generation, gid)
    if gen is None or gen.owner_id != trial.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "生成任务不存在")
    rows = db.scalars(select(Citation).where(Citation.generation_id == gen.id).order_by(Citation.ordinal)).all()
    return {"items": [_public(c) for c in rows]}


@router.get("/plans/{pid}/citations")
def list_plan_citations(pid: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    plan = db.get(Plan, pid)
    if plan is None or plan.deleted_at is not None or plan.owner_id != trial.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "方案不存在")
    ids = [uuid.UUID(str(c)) for c in (plan.citation_ids or [])]
    if not ids:
        return {"items": []}
    rows = db.scalars(select(Citation).where(Citation.id.in_(ids)).order_by(Citation.ordinal)).all()
    return {"items": [_public(c) for c in rows]}
