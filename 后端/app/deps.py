"""鉴权与归属校验依赖（BE-101 / BE-102）。

本项目要求先注册才能使用：没有有效登录态时一律 401，不再分配匿名试用身份。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.config import settings
from app.db import begin_write, get_db
from app.models import Account, AuthToken, TrialUser
from app.security import hash_token

COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 账号登录态保留一年，不必频繁重登

# last_seen_at 目前只写不读，5 分钟一次足够；阈值放宽不会影响任何功能。
_TOUCH_EVERY = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_trial(
    request: Request,
    db: Session = Depends(get_db),
) -> TrialUser:
    """取当前登录身份。没有有效登录态一律 401（不自动创建匿名身份）。"""
    raw = request.cookies.get(settings.cookie_name)
    if not raw:
        raise errors.ApiError(401, errors.UNAUTHENTICATED, "请先注册或登录")

    h = hash_token(raw)
    # 只读查询不要拿写锁：SQLite 下 BEGIN IMMEDIATE 会挡住别的写请求，
    # 而这个依赖被每个请求调用一次，锁一旦跨整个请求持有，并发就掉下去了。
    tok = db.scalar(
        select(AuthToken).where(AuthToken.token_hash == h, AuthToken.revoked_at.is_(None))
    )
    if tok is None or tok.account_id is None:
        raise errors.ApiError(401, errors.UNAUTHENTICATED, "登录状态已失效，请重新登录")

    trial = db.get(TrialUser, tok.trial_user_id)
    if trial is None:
        raise errors.ApiError(401, errors.UNAUTHENTICATED, "账号数据异常，请重新登录")

    # last_seen 不必每次请求都写库：距上次超过 5 分钟才更新一次，
    # 纯读操作不再触发写事务（SQLite 写是串行的，写少了并发才上得去）；
    # 真要写的时候，才把写锁拿进来。
    last_seen = tok.last_seen_at or tok.created_at
    if _now() - last_seen > _TOUCH_EVERY:
        begin_write(db)
        tok.last_seen_at = _now()
        db.commit()
    return trial


def set_session_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=raw,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, path="/")


def get_account(request: Request, db: Session) -> Account | None:
    raw = request.cookies.get(settings.cookie_name)
    if not raw:
        return None
    tok = db.scalar(
        select(AuthToken).where(AuthToken.token_hash == hash_token(raw), AuthToken.revoked_at.is_(None))
    )
    if tok is None or tok.account_id is None:
        return None
    return db.get(Account, tok.account_id)


def require_writable(trial: TrialUser = Depends(get_trial)) -> TrialUser:
    now = _now()
    if now >= trial.purge_at:
        raise errors.ApiError(403, errors.TRIAL_EXPIRED, "账号状态异常，数据不可访问")
    if now >= trial.expires_at:
        raise errors.ApiError(403, errors.TRIAL_EXPIRED_READONLY, "账号状态异常，当前只能查看")
    return trial


def require_readable(trial: TrialUser = Depends(get_trial)) -> TrialUser:
    if _now() >= trial.purge_at:
        raise errors.ApiError(403, errors.TRIAL_EXPIRED, "账号状态异常，数据不可访问")
    return trial
