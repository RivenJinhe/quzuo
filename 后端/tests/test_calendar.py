"""日历导入、修改和账号归属走真实的接口和数据库写入。"""
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import errors
from app.config import settings
from app.db import Base, get_db
from app.routers import auth, calendar
from app.services import providers


@pytest.fixture
def application(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "calendar.db"))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)

    def get_test_db():
        with sessions() as session:
            yield session

    app = FastAPI()
    app.add_exception_handler(errors.ApiError, errors.api_error_handler)
    app.include_router(auth.router)
    app.include_router(calendar.router)
    app.dependency_overrides[get_db] = get_test_db
    monkeypatch.setattr(settings, "llm_allow_real", False)
    monkeypatch.setattr(settings, "cookie_secure", False)
    yield app
    engine.dispose()


def _register(client, username):
    response = client.post("/api/v1/auth/register", json={"username": username, "password": "calendar-pass"})
    assert response.status_code == 201, response.text


def _import(client, text="", files=None):
    return client.post("/api/v1/calendar/import",
                       data={"reference_date": "2026-11-25", "text": text}, files=files)


def _month(client, month="2026-12"):
    response = client.get("/api/v1/calendar", params={"month": month})
    assert response.status_code == 200, response.text
    return response.json()


def test_text_import_reuses_existing_event_and_moves_it_across_months(application):
    with TestClient(application) as client:
        _register(client, "calendar_owner")
        text = "2026-12-04 09:00–10:30 小组讨论"
        receipt = _import(client, text + "\n" + text).json()
        assert (receipt["created"], receipt["unchanged"]) == (1, 1)
        [before] = _month(client)
        move = "小组讨论：原定 2026-12-04 09:00 改到 2027-01-05"
        assert _import(client, move).json()["updated"] == 1
        assert _import(client, move).json()["unchanged"] == 1
        assert _month(client) == []
        [after] = _month(client, "2027-01")
        assert after["id"] == before["id"]
        assert after["version"] == 2
        assert after["start_time"] == "09:00:00"
        assert after["end_time"] == "10:30:00"


def test_duplicate_title_does_not_allow_ambiguous_reschedule(application):
    with TestClient(application) as client:
        _register(client, "calendar_owner")
        _import(client, "2026-12-04 09:00 小组讨论\n2026-12-04 11:00 小组讨论")
        receipt = _import(client, "小组讨论：2026-12-04 改到 2026-12-05").json()
        assert receipt["updated"] == 0
        assert "多条" in receipt["warnings"][0]
        assert len(_month(client)) == 2


def test_month_overlap_ownership_and_concurrent_edit_protection(application):
    with TestClient(application) as owner, TestClient(application) as other:
        assert other.get("/api/v1/calendar?month=2026-11").status_code == 401
        _register(owner, "calendar_owner")
        _register(other, "calendar_other")
        fields = {"title": "期末行程", "start_date": "2026-11-30", "end_date": "2026-12-02"}
        response = owner.post("/api/v1/calendar/events", json=fields)
        assert response.status_code == 201, response.text
        entry = response.json()
        assert _month(owner, "2026-11") == _month(owner) == [entry]
        assert _month(other) == []
        path = "/api/v1/calendar/events/" + entry["id"]
        update = {**fields, "title": "期末行程（已确认）", "version": 1}
        assert other.put(path, json=update).status_code == 404
        assert owner.put(path, json=update).json()["version"] == 2
        assert owner.delete(path, params={"version": 1}).status_code == 409
        assert owner.delete(path, params={"version": 2}).status_code == 204
        assert _month(owner) == []


def test_image_upload_uses_vision_and_imports_validated_response(application, monkeypatch):
    with TestClient(application) as client:
        _register(client, "calendar_owner")
        image = {"file": ("schedule.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")}
        assert _import(client, files=image).status_code == 503
        monkeypatch.setattr(settings, "llm_allow_real", True)
        monkeypatch.setattr(settings, "provider_qwen_key", "test-key")

        def vision(url, key, body):
            assert url == providers.QWEN_BASE_URL + "/chat/completions"
            assert key == "test-key"
            assert body["model"] == settings.calendar_vision_model
            assert body["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
            content = {"events": [{"title": "发布会", "start_date": "2026-12-20", "location": "主会场"}]}
            return httpx.Response(200, request=httpx.Request("POST", url),
                                  json={"choices": [{"message": {"content": json.dumps(content)}}]})

        monkeypatch.setattr(providers, "post_chat", vision)
        result = _import(client, files=image)
        assert result.status_code == 200, result.text
        assert result.json()["created"] == 1
        [saved] = _month(client)
        assert (saved["title"], saved["location"], saved["start_time"]) == ("发布会", "主会场", None)


def test_invalid_dates_do_not_partially_write_an_import(application):
    with TestClient(application) as client:
        _register(client, "calendar_owner")
        result = _import(client, "2026-12-05 10:00 小组讨论\n2026-12-32 截止")
        assert result.status_code == 422
        assert _month(client) == []
