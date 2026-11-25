"""回归：get_trial 必须真的返回 TrialUser，且 last_seen_at 不是每个请求都写。

PR #4 把 `return trial` 一起删掉了：get_trial 返回 None，require_writable 里
`trial.purge_at` 直接 AttributeError，所有需登录接口变成 500。
当时 60 个用例全绿也发现不了，因为它们都用 dependency_overrides 绕过了这个依赖。
这里直接调用依赖函数本身，把这条路径钉住。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from app.config import settings
from app.db import Base
from app.deps import get_trial
from app.models import Account, AuthToken, TrialUser
from app.security import hash_token


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db(tmp_path) -> Session:
    engine = create_engine("sqlite:///" + str(tmp_path / "auth.db"))
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db: Session, raw: str, last_seen: datetime) -> TrialUser:
    now = _now()
    trial = TrialUser(
        id=uuid.uuid4(),
        token_hash="t" * 32,
        expires_at=now + timedelta(days=36500),
        purge_at=now + timedelta(days=36507),
        last_seen_at=last_seen,
    )
    db.add(trial)
    db.flush()
    account = Account(
        id=uuid.uuid4(), username="tester", password_hash="x", trial_user_id=trial.id
    )
    db.add(account)
    db.flush()
    db.add(
        AuthToken(
            id=uuid.uuid4(),
            token_hash=hash_token(raw),
            trial_user_id=trial.id,
            account_id=account.id,
            last_seen_at=last_seen,
        )
    )
    db.commit()
    return trial


def _request(raw: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", f"{settings.cookie_name}={raw}".encode())],
        }
    )


def _token_row(db: Session, raw: str) -> AuthToken:
    db.expire_all()
    return db.query(AuthToken).filter(AuthToken.token_hash == hash_token(raw)).one()


def test_get_trial_returns_the_trial_user(tmp_path):
    db = _db(tmp_path)
    trial = _seed(db, "raw-token", _now())

    got = get_trial(request=_request("raw-token"), db=db)

    assert got is not None, "get_trial 返回 None，require_writable 会 AttributeError"
    assert got.id == trial.id


def test_missing_cookie_still_401(tmp_path):
    from app import errors

    db = _db(tmp_path)
    with_exc = None
    try:
        get_trial(request=Request({"type": "http", "method": "GET", "path": "/", "headers": []}), db=db)
    except errors.ApiError as exc:
        with_exc = exc
    assert with_exc is not None and with_exc.status == 401


def test_last_seen_is_not_rewritten_on_every_request(tmp_path):
    db = _db(tmp_path)
    raw = "raw-token"
    stamp = _now() - timedelta(minutes=1)
    _seed(db, raw, stamp)

    for _ in range(3):
        assert get_trial(request=_request(raw), db=db) is not None

    row = _token_row(db, raw)
    assert row.last_seen_at.replace(microsecond=0) == stamp.replace(microsecond=0)


def test_last_seen_is_refreshed_after_five_minutes(tmp_path):
    db = _db(tmp_path)
    raw = "raw-token"
    old = _now() - timedelta(minutes=6)
    _seed(db, raw, old)

    assert get_trial(request=_request(raw), db=db) is not None

    row = _token_row(db, raw)
    assert row.last_seen_at > old + timedelta(minutes=5)
