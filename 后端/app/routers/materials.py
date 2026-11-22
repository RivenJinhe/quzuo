"""材料接口（BE-210 ~ BE-215）。"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import errors
from app.db import get_db
from app.deps import require_readable, require_writable
from app.models import Material, TrialUser
from app.schemas import MaterialCreate, MaterialListOut, MaterialOut, MaterialUpdate
from app.services import extract as extractor

router = APIRouter(prefix="/api/v1/materials", tags=["materials"])


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(("o:%d" % offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        return max(0, int(raw.split(":")[1]))
    except Exception:  # noqa: BLE001
        raise errors.ApiError(422, errors.VALIDATION_ERROR, "游标不合法", "cursor")


def get_owned(db: Session, trial: TrialUser, material_id: uuid.UUID) -> Material:
    m = db.get(Material, material_id)
    if m is None or m.deleted_at is not None or m.owner_id != trial.id:
        raise errors.ApiError(404, errors.NOT_FOUND, "材料不存在")
    return m


@router.post("", status_code=201, response_model=MaterialOut)
def create_material(payload: MaterialCreate, trial: TrialUser = Depends(require_writable),
                    db: Session = Depends(get_db)):
    m = Material(owner_id=trial.id, **payload.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


@router.get("", response_model=MaterialListOut)
def list_materials(
    topic: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=100),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    trial: TrialUser = Depends(require_readable),
    db: Session = Depends(get_db),
):
    stmt = select(Material).where(Material.owner_id == trial.id, Material.deleted_at.is_(None))
    if topic:
        stmt = stmt.where(Material.topic == topic)
    if q:
        like = "%" + q + "%"
        stmt = stmt.where(or_(Material.title.ilike(like), Material.excerpt.ilike(like), Material.note.ilike(like)))
    offset = _decode_cursor(cursor)
    rows = db.scalars(stmt.order_by(Material.updated_at.desc(), Material.id.desc()).offset(offset).limit(limit + 1)).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    return MaterialListOut(items=items, next_cursor=_encode_cursor(offset + limit) if has_more else None)


@router.get("/topics")
def list_topics(trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Material.topic, func.count()).where(Material.owner_id == trial.id, Material.deleted_at.is_(None)).group_by(Material.topic)
    ).all()
    return {"items": [{"topic": t, "count": c} for t, c in rows]}


@router.post("/batch", response_model=MaterialListOut)
def batch_get(payload: dict, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or len(ids) > 50:
        raise errors.ApiError(422, errors.VALIDATION_ERROR, "ids 不合法", "ids")
    try:
        uids = [uuid.UUID(str(i)) for i in ids]
    except ValueError:
        raise errors.ApiError(422, errors.VALIDATION_ERROR, "ids 含非法 UUID", "ids")
    rows = db.scalars(
        select(Material).where(Material.owner_id == trial.id, Material.deleted_at.is_(None), Material.id.in_(uids))
    ).all()
    order = {u: i for i, u in enumerate(uids)}
    items = sorted(rows, key=lambda m: order.get(m.id, 0))
    return MaterialListOut(items=items, next_cursor=None)


@router.post("/extract")
async def extract_file(
    file: UploadFile = File(...),
    page_start: int | None = Query(default=None),
    page_end: int | None = Query(default=None),
    trial: TrialUser = Depends(require_writable),
):
    """上传 txt/md/pdf/docx/pptx，抽出纯文本供用户确认后存成材料（不落库、不解析版式、不做 OCR）。"""
    if file.size and file.size > extractor.MAX_BYTES:
        raise errors.ApiError(413, errors.PAYLOAD_TOO_LARGE, "文件超过 20MB，请拆分后再传")
    data = await file.read()
    try:
        return extractor.extract(file.filename or "材料", data, page_start, page_end)
    except extractor.ExtractError as exc:
        status_map = {"UNSUPPORTED_FILE_TYPE": 415, "PAYLOAD_TOO_LARGE": 413, "VALIDATION_ERROR": 422}
        raise errors.ApiError(status_map.get(exc.code, 422), exc.code, exc.message)


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: uuid.UUID, trial: TrialUser = Depends(require_readable), db: Session = Depends(get_db)):
    return get_owned(db, trial, material_id)


@router.patch("/{material_id}", response_model=MaterialOut)
def update_material(material_id: uuid.UUID, payload: MaterialUpdate,
                    trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    m = get_owned(db, trial, material_id)
    if payload.version != m.version:
        raise errors.ApiError(409, errors.VERSION_CONFLICT, "这条材料已被修改过，请刷新后再改", "version",
                              {"current_version": m.version})
    data = payload.model_dump(exclude_unset=True, exclude={"version"})
    for k, val in data.items():
        setattr(m, k, val)
    m.version = m.version + 1
    m.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(m)
    return m


@router.delete("/{material_id}", status_code=204)
def delete_material(material_id: uuid.UUID, trial: TrialUser = Depends(require_writable), db: Session = Depends(get_db)):
    m = get_owned(db, trial, material_id)
    m.deleted_at = datetime.now(timezone.utc)
    m.version = m.version + 1
    db.commit()
    return Response(status_code=204)
