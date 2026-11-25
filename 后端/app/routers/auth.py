"""账号：注册、登录、登出、当前账号。数据归属仍走 trial_user，账号只是把身份绑起来。"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.config import settings
from app.db import begin_write, get_db
from app.deps import clear_session_cookie, get_account, get_trial, set_session_cookie
from app.models import Account, AuthToken, TrialUser
from app.schemas import LoginIn, RegisterIn
from app.security import hash_password, hash_token, new_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_LOGIN_WINDOW = 60
_LOGIN_MAX = 10
_attempts: dict[str, list[float]] = defaultdict(list)


def _login_rate_limit(ip: str) -> None:
    now = time.time()
    # 顺手清理：很久没登录过的 IP，把它的记录整条删掉，防止字典越积越大
    stale = [k for k, v in _attempts.items() if not v or now - v[-1] >= _LOGIN_WINDOW]
    for k in stale:
        del _attempts[k]

    arr = [t for t in _attempts[ip] if now - t < _LOGIN_WINDOW]
    if len(arr) >= _LOGIN_MAX:
        raise errors.ApiError(429, errors.RATE_LIMITED, "尝试太频繁，请稍后再试")
    arr.append(now)
    _attempts[ip] = arr



def _extend_trial(trial: TrialUser) -> None:
    """账号用户不受匿名 30 天试用期限制：注册与登录时续成长期有效。"""
    now = datetime.now(timezone.utc)
    trial.expires_at = now + timedelta(days=36500)
    trial.purge_at = now + timedelta(days=36507)


def _account_out(acc: Account) -> dict:
    return {
        "id": str(acc.id),
        "username": acc.username,
        "created_at": acc.created_at.isoformat(),
    }


def _trial_out(trial: TrialUser) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "trial_id": str(trial.id),
        "expires_at": trial.expires_at.isoformat(),
        "purge_at": trial.purge_at.isoformat(),
        "writable": now < trial.expires_at,
        "days_left": max(0, (trial.expires_at - now).days),
    }


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response,
             db: Session = Depends(get_db)):
    """注册：新建账号与数据身份。本项目要求先注册才能使用，注册前不分配任何匿名身份。"""
    password_hash = hash_password(body.password)
    begin_write(db)
    if db.scalar(select(Account).where(Account.username == body.username)) is not None:
        raise errors.ApiError(409, errors.USERNAME_TAKEN, "用户名已被占用", "username")

    now = datetime.now(timezone.utc)
    raw = new_token()
    trial = TrialUser(
        token_hash=hash_token(raw),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=36500),
        purge_at=now + timedelta(days=36507),
    )
    db.add(trial)
    db.flush()

    acc = Account(username=body.username, password_hash=password_hash, trial_user_id=trial.id)
    db.add(acc)
    db.flush()

    db.add(AuthToken(token_hash=hash_token(raw), trial_user_id=trial.id, account_id=acc.id))
    db.commit()
    db.refresh(acc)

    set_session_cookie(response, raw)
    return {"account": _account_out(acc), "trial": _trial_out(trial)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    _login_rate_limit(ip)

    acc = db.scalar(select(Account).where(Account.username == body.username))
    if acc is None or not verify_password(body.password, acc.password_hash):
        raise errors.ApiError(401, errors.INVALID_CREDENTIALS, "用户名或密码不正确")

    begin_write(db)
    trial = db.get(TrialUser, acc.trial_user_id)
    if trial is None:
        raise errors.ApiError(500, errors.INTERNAL_ERROR, "账号数据异常，请联系管理员")

    raw = new_token()
    db.add(AuthToken(token_hash=hash_token(raw), trial_user_id=trial.id, account_id=acc.id))
    _extend_trial(trial)
    db.commit()

    set_session_cookie(response, raw)
    return {"account": _account_out(acc), "trial": _trial_out(trial)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(settings.cookie_name)
    if raw:
        begin_write(db)
        tok = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(raw)))
        if tok is not None and tok.revoked_at is None:
            tok.revoked_at = datetime.now(timezone.utc)
            db.commit()
    clear_session_cookie(response)
    return {"logged_out": True}


@router.get("/me")
def auth_me(request: Request, db: Session = Depends(get_db)):
    """登录态探针：未登录返回 logged_in=false，不报 401，方便前端决定跳不跳登录页。"""
    acc = get_account(request, db)
    if acc is None:
        return {"logged_in": False, "account": None, "trial": None}
    trial = db.get(TrialUser, acc.trial_user_id)
    return {
        "logged_in": True,
        "account": _account_out(acc),
        "trial": _trial_out(trial) if trial is not None else None,
    }
