"""按账号读取月历并保存手动或导入的日程。"""
from __future__ import annotations

import unicodedata
import uuid
from calendar import monthrange
from datetime import date
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.config import settings
from app.db import begin_write, get_db
from app.deps import require_readable, require_writable
from app.models import CalendarEvent, TrialUser
from app.schemas import (
    CalendarEventIn,
    CalendarEventOut,
    CalendarEventUpdate,
    CalendarImportEvent,
    CalendarImportResult,
)
from app.services import calendar_import, providers


router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


@router.get("", response_model=list[CalendarEventOut])
def list_events(
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
    trial: TrialUser = Depends(require_readable),
    db: Session = Depends(get_db),
):
    year, number = map(int, month.split("-"))
    if not year:
        raise errors.ApiError(422, errors.VALIDATION_ERROR, "月份无效", "month")
    first = date(year, number, 1)
    last = date(year, number, monthrange(year, number)[1])
    return db.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.owner_id == trial.id, CalendarEvent.start_date <= last, CalendarEvent.end_date >= first)
        .order_by(CalendarEvent.start_date, CalendarEvent.start_time.nulls_first(), CalendarEvent.title, CalendarEvent.id)
    ).all()


def _lock_calendar(db: Session, owner_id: uuid.UUID) -> None:
    begin_write(db)
    # Serialize this owner's writes, including concurrent imports of the same item.
    db.execute(select(TrialUser.id).where(TrialUser.id == owner_id).with_for_update()).scalar_one()


def _event_values(payload: CalendarEventIn) -> dict:
    values = payload.model_dump(include=set(CalendarEventIn.model_fields))
    values["end_date"] = payload.end_date or payload.start_date
    return values


def _owned_event(db: Session, owner_id: uuid.UUID, event_id: uuid.UUID, version: int) -> CalendarEvent:
    event = db.get(CalendarEvent, event_id)
    if event is None or event.owner_id != owner_id:
        raise errors.ApiError(404, errors.NOT_FOUND, "日程不存在")
    if event.version != version:
        raise errors.ApiError(409, errors.VERSION_CONFLICT, "这条日程刚刚更新，请重新打开后再编辑")
    return event


@router.post("/events", status_code=201, response_model=CalendarEventOut)
def create_event(
    payload: CalendarEventIn,
    trial: TrialUser = Depends(require_writable),
    db: Session = Depends(get_db),
):
    _lock_calendar(db, trial.id)
    event = CalendarEvent(owner_id=trial.id, **_event_values(payload))
    db.add(event)
    db.commit()
    return event


@router.put("/events/{event_id}", response_model=CalendarEventOut)
def update_event(
    event_id: uuid.UUID,
    payload: CalendarEventUpdate,
    trial: TrialUser = Depends(require_writable),
    db: Session = Depends(get_db),
):
    _lock_calendar(db, trial.id)
    event = _owned_event(db, trial.id, event_id, payload.version)
    for key, value in _event_values(payload).items():
        setattr(event, key, value)
    event.version += 1
    db.commit()
    return event


@router.delete("/events/{event_id}", status_code=204)
def delete_event(
    event_id: uuid.UUID,
    version: Annotated[int, Query(ge=1)],
    trial: TrialUser = Depends(require_writable),
    db: Session = Depends(get_db),
):
    _lock_calendar(db, trial.id)
    db.delete(_owned_event(db, trial.id, event_id, version))
    db.commit()


def _title_key(title: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def _apply_import(
    db: Session, owner_id: uuid.UUID, extracted: calendar_import.Extraction,
) -> CalendarImportResult:
    dates: set[date] = set()
    warnings = list(extracted.warnings)
    result = CalendarImportResult(created=0, updated=0, unchanged=0, dates=[], warnings=[])
    if not extracted.events:
        result.warnings = warnings or ["没有识别到带日期的日程"]
        return result

    _lock_calendar(db, owner_id)
    candidate_dates = {item.start_date for item in extracted.events}
    candidate_dates.update(item.previous_date for item in extracted.events if item.previous_date is not None)
    candidates = list(db.scalars(select(CalendarEvent).where(
        CalendarEvent.owner_id == owner_id, CalendarEvent.start_date.in_(candidate_dates),
    )))
    for item in extracted.events:
        destination = _calendar_matches(candidates, item.title, item.start_date)
        exact = [event for event in destination if event.start_time == item.start_time]
        matches = exact
        if item.previous_date is not None:
            matches = _calendar_matches(candidates, item.previous_title or item.title, item.previous_date)
            if item.previous_time is not None:
                matches = [event for event in matches if event.start_time == item.previous_time]
            if item.start_time is None:
                if len(matches) == 1:
                    exact = [event for event in destination if event.start_time == matches[0].start_time]
                elif not matches and not exact and len(destination) == 1:
                    exact = destination
            if not matches and not exact:
                warnings.append(f"「{item.title}」找不到原日程，请手动核对")
                continue
            if not matches:
                matches = exact  # This reschedule was already imported.
        if len(matches) > 1 or (matches and any(event is not matches[0] for event in exact)):
            warnings.append(f"「{item.title}」对应多条日程，请手动核对")
            continue

        if matches:
            current = matches[0]
            try:
                changes = _import_values(item, current)
            except (ValueError, OverflowError):
                warnings.append(f"「{item.title}」的新日期或时间与原日程冲突，请手动核对")
                continue
            if changes:
                for key, value in changes.items():
                    setattr(current, key, value)
                current.version += 1
                result.updated += 1
            else:
                result.unchanged += 1
        else:
            current = CalendarEvent(owner_id=owner_id, **_event_values(item), version=1)
            db.add(current)
            candidates.append(current)
            result.created += 1
        dates.add(item.start_date)

    db.commit()
    result.dates = sorted(dates)
    result.warnings = warnings
    return result


def _calendar_matches(candidates: list[CalendarEvent], title: str, day: date) -> list[CalendarEvent]:
    key = _title_key(title)
    return [event for event in candidates if event.start_date == day and _title_key(event.title) == key]


def _import_values(item: CalendarImportEvent, existing: CalendarEvent) -> dict:
    fields = set(CalendarEventIn.model_fields)
    prior = {key: getattr(existing, key) for key in fields}
    merged = prior | item.model_dump(include=fields, exclude_none=True)
    if item.previous_title is None:
        merged["title"] = existing.title
    if item.end_date is None and item.start_date != existing.start_date:
        merged["end_date"] = item.start_date + (existing.end_date - existing.start_date)
    if item.end_time is None and merged["start_time"] != existing.start_time:
        merged["end_time"] = None
    CalendarEventIn.model_validate(merged)
    return {key: value for key, value in merged.items() if value != prior[key]}


@router.post("/import", response_model=CalendarImportResult)
def import_events(
    reference_date: Annotated[date, Form()],
    text: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
    trial: TrialUser = Depends(require_writable),
    db: Session = Depends(get_db),
):
    image = None
    if file is not None:
        if PurePath(file.filename or "").suffix.lower() in {".txt", ".md"}:
            limit = calendar_import.MAX_TEXT * 4 + 3
            raw = file.file.read(limit + 1)
            if len(raw) > limit:
                raise errors.ApiError(413, errors.PAYLOAD_TOO_LARGE, "文字文件太长，请分批导入")
            try:
                text += "\n" + raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise errors.ApiError(422, errors.VALIDATION_ERROR, "文字文件需要使用 UTF-8 编码") from exc
        else:
            raw = file.file.read(calendar_import.MAX_IMAGE + 1)
            if len(raw) > calendar_import.MAX_IMAGE:
                raise errors.ApiError(413, errors.PAYLOAD_TOO_LARGE, "图片不能超过 8 MB")
            mime = calendar_import.image_type(raw)
            if mime is None:
                raise errors.ApiError(415, errors.UNSUPPORTED_FILE_TYPE, "请选择 PNG、JPG、WebP、TXT 或 Markdown 文件")
            image = (mime, raw)
    text = text.strip()
    if len(text) > calendar_import.MAX_TEXT:
        raise errors.ApiError(413, errors.PAYLOAD_TOO_LARGE, "文字超过 12000 字，请分批导入")
    if not text and image is None:
        raise errors.ApiError(422, errors.VALIDATION_ERROR, "请粘贴文字或选择文件")

    owner_id = trial.id
    db.rollback()  # Do not hold a database connection while the model is working.
    extracted = calendar_import.extract(
        text, reference_date, image,
        lambda: providers.rate_limit("calendar", str(owner_id), settings.gen_rate_per_minute),
    )
    return _apply_import(db, owner_id, extracted)
