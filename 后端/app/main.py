"""应用入口：挂路由、统一错误体、试用身份。"""
from __future__ import annotations

import logging
import uuid as uuidlib
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import errors
from app.config import settings
from app.db import get_db
from app.deps import get_trial
from app.models import TrialUser
from app.services import providers
from app.routers import auth, citations, conversations, materials, models, plans

logging.basicConfig(level=settings.log_level)

from app.db import Base, engine

Base.metadata.create_all(bind=engine)


def _ensure_columns() -> None:
    """create_all 不会给已有表补字段，这里做一次幂等补列。"""
    from sqlalchemy import text as _text

    with engine.begin() as conn:
        conn.execute(_text("ALTER TABLE generation ADD COLUMN IF NOT EXISTS usage JSONB"))
        conn.execute(_text("ALTER TABLE generation ADD COLUMN IF NOT EXISTS changes JSONB"))
        conn.execute(_text("ALTER TABLE generation ADD COLUMN IF NOT EXISTS options JSONB"))


try:
    _ensure_columns()
except Exception as exc:  # noqa: BLE001  数据库暂时不在时不要挡住启动
    logging.getLogger("qu.main").warning("补列失败（可忽略）：%s", exc)

app = FastAPI(title="去做 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(errors.ApiError, errors.api_error_handler)
app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)

for r in (materials.router, conversations.router, plans.router, citations.router,
          models.router, auth.router):
    app.include_router(r)


@app.get("/health")
def health():
    return {"ok": True, "provider": providers.default_model_id(), "web_search": settings.llm_allow_web_search}


@app.get("/api/v1/me")
def me(trial: TrialUser = Depends(get_trial)):
    now = datetime.now(timezone.utc)
    return {
        "trial_id": str(trial.id),
        "expires_at": trial.expires_at.isoformat(),
        "purge_at": trial.purge_at.isoformat(),
        "writable": now < trial.expires_at,
        "readable": now < trial.purge_at,
        "days_left": max(0, (trial.expires_at - now).days),
    }


def _dev_local_only(request: Request) -> None:
    """
    /internal/dev/* 只给本机验收脚本用。

    经内网穿透或反向代理进来的请求一定带 CF-Connecting-IP / X-Forwarded-For / CF-Ray，
    这类请求直接 403，避免把「改试用期」「注入生成结果」暴露到公网。
    """
    if (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for")
        or request.headers.get("cf-ray")
    ):
        raise errors.ApiError(403, errors.NOT_FOUND, "dev 端点只允许本机直连")


@app.post("/internal/dev/trials/{tid}/expire")
def dev_expire(request: Request, tid: uuidlib.UUID, mode: str = "readonly", db: Session = Depends(get_db)):
    """仅本地验收用：把某个试用身份推进到只读或已过期（BE-102）。"""
    _dev_local_only(request)
    t = db.get(TrialUser, tid)
    if t is None:
        raise errors.ApiError(404, errors.NOT_FOUND, "试用身份不存在")
    now = datetime.now(timezone.utc)
    if mode == "readonly":
        t.expires_at = now - timedelta(days=1)
        t.purge_at = now + timedelta(days=6)
    else:
        t.expires_at = now - timedelta(days=8)
        t.purge_at = now - timedelta(days=2)
    db.commit()
    return {"trial_id": str(t.id), "mode": mode, "expires_at": t.expires_at.isoformat(),
            "purge_at": t.purge_at.isoformat()}



@app.post("/internal/dev/generations/inject")
def dev_inject(request: Request, payload: dict, db: Session = Depends(get_db)):
    """仅本地验收用：把一份固定结果走真实复核链路（不调模型）。"""
    _dev_local_only(request)
    from app.models import Conversation, Generation, Material
    from app.services.generate import build_payload, finalize_generation

    conv = db.get(Conversation, payload["conversation_id"])
    if conv is None:
        raise errors.ApiError(404, errors.NOT_FOUND, "对话不存在")
    cond = conv.conditions or {}
    ids = conv.material_ids or []
    mats = db.query(Material).filter(Material.id.in_(ids), Material.deleted_at.is_(None)).all() if ids else []
    built = build_payload(
        goal=conv.goal, task_type=conv.task_type, materials=list(mats), conditions=cond,
        retained=conv.retained or {}, pending_facts={},
        available_minutes=cond.get("available_minutes"), people=cond.get("people"),
        budget_per_person=cond.get("budget_per_person"),
        start_time=cond.get("start_time"), legs=cond.get("legs"),
    )
    gen = Generation(owner_id=conv.owner_id, conversation_id=conv.id,
                     conversation_version=conv.version, status="running", request={}, model="dev-inject")
    db.add(gen)
    db.commit()
    db.refresh(gen)
    result = payload["result"]
    validation = finalize_generation(db, gen, result, built)
    gen.finished_at = datetime.now(timezone.utc)
    db.commit()
    return {"generation_id": str(gen.id), "status": gen.status, "validation": validation,
            "result": result, "block_reason": gen.block_reason}



@app.post("/internal/dev/generations/pending")
def dev_pending(request: Request, payload: dict, db: Session = Depends(get_db)):
    """仅本地验收用：造一条 pending 生成，验证改条件后旧生成作废。"""
    _dev_local_only(request)
    from app.models import Conversation, Generation

    conv = db.get(Conversation, payload["conversation_id"])
    if conv is None:
        raise errors.ApiError(404, errors.NOT_FOUND, "对话不存在")
    gen = Generation(owner_id=conv.owner_id, conversation_id=conv.id,
                     conversation_version=conv.version, status="pending", request={}, model="dev-pending")
    db.add(gen)
    db.commit()
    db.refresh(gen)
    return {"generation_id": str(gen.id), "conversation_version": conv.version}

# 产品页面由 /app/ 提供，旧链接沿用同一份页面。
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

WEB_DIR = _Path(__file__).resolve().parent.parent / "web" / "dist"

@app.get("/app/design.html", include_in_schema=False)
def _design_redirect():
    return RedirectResponse(url="/app/")


if WEB_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

@app.get("/", include_in_schema=False)
def _index_redirect():
    return RedirectResponse(url="/app/")
