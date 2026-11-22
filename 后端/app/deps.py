"""鉴权与归属校验依赖（BE-101 / BE-102）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.config import settings
from app.db import get_db
from app.models import Account, AuthToken, TrialUser
from app.security import hash_token, new_token

COOKIE_MAX_AGE = 60 * 60 * 24 * settings.trial_purge_days


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_trial(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TrialUser:
    """取当前身份：优先看登录令牌，其次是匿名试用令牌，都没有就新建一个匿名身份。"""
    raw = request.cookies.get(settings.cookie_name)
    now = _now()
    trial: TrialUser | None = None
    if raw:
        h = hash_token(raw)
        tok = db.scalar(
            select(AuthToken).where(AuthToken.token_hash == h, AuthToken.revoked_at.is_(None))
        )
        if tok is not None:
            trial = db.get(TrialUser, tok.trial_user_id)
            if trial is not None:
                tok.last_seen_at = now
                db.commit()
        else:
            trial = db.scalar(select(TrialUser).where(TrialUser.token_hash == h))

    if trial is None:
        raw = new_token()
        trial = TrialUser(
            token_hash=hash_token(raw),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=settings.trial_ttl_days),
            purge_at=now + timedelta(days=settings.trial_purge_days),
        )
        db.add(trial)
        db.commit()
        db.refresh(trial)

    set_session_cookie(response, raw)
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
        raise errors.ApiError(403, errors.TRIAL_EXPIRED, "试用期已结束，数据已不可访问")
    if now >= trial.expires_at:
        raise errors.ApiError(403, errors.TRIAL_EXPIRED_READONLY, "试用期已过，当前只能查看，不能再新建或修改")
    return trial



def require_readable(trial: TrialUser = Depends(get_trial)) -> TrialUser:
    if _now() >= trial.purge_at:
        raise errors.ApiError(403, errors.TRIAL_EXPIRED, "试用期已结束，数据已不可访问")
    return trial
