"""自然语言追问解析（BE-238）单测。"""
from app.services import turns


def test_duration_review() -> None:
    r = turns.parse("时间只剩 5 分钟了", "review", {"duration_minutes": 15}, {})
    assert r["conditions"]["duration_minutes"] == 5
    assert r["conditions"]["available_minutes"] == 5
    assert r["applied"][0]["from"] == 15


def test_duration_takes_last() -> None:
    r = turns.parse("从 15 分钟缩到 10 分钟", "activity", {"total_minutes": 25}, {})
    assert r["conditions"]["total_minutes"] == 10


def test_budget_per_person() -> None:
    r = turns.parse("预算 60 元", "travel", {"budget_per_person": 80}, {})
    assert r["conditions"]["budget_per_person"] == 60.0


def test_budget_total_divided_by_people() -> None:
    r = turns.parse("总预算 400，20 人", "activity", {}, {})
    assert r["conditions"]["budget_per_person"] == 20.0
    assert r["conditions"]["people"] == 20


def test_remove_item() -> None:
    cond = {"items": [{"title": "开场", "minutes": 2}, {"title": "猜词", "minutes": 8}]}
    r = turns.parse("把猜词去掉", "activity", cond, {})
    titles = [x["title"] for x in r["conditions"]["items"]]
    assert titles == ["开场"]
    assert "猜词" in r["summary"]


def test_remove_must_keep_item_is_reported() -> None:
    cond = {"items": [{"title": "互相参观", "minutes": 12, "must_keep": True}]}
    r = turns.parse("删掉互相参观", "activity", cond, {})
    assert r["conditions"]["items"] == []
    assert "原为必须保留项" in r["summary"]


def test_keep_requirement_goes_to_retained() -> None:
    r = turns.parse("例题一定要讲", "review", {}, {})
    assert "例题" in r["retained"]["must_keep_texts"]


def test_keep_matches_existing_item() -> None:
    cond = {"items": [{"title": "互访", "minutes": 12}]}
    r = turns.parse("互访必须保留", "activity", cond, {})
    assert r["conditions"]["items"][0]["must_keep"] is True


def test_start_time() -> None:
    r = turns.parse("15:15 出发", "travel", {}, {})
    assert r["conditions"]["start_time"] == "15:15"


def test_unknown_item_is_unresolved() -> None:
    cond = {"items": [{"title": "开场", "minutes": 2}]}
    r = turns.parse("把咖啡去掉", "activity", cond, {})
    assert r["unresolved"]
    assert len(r["conditions"]["items"]) == 1


def test_fallback_keeps_original_text() -> None:
    r = turns.parse("顺便提醒我别熬夜", "review", {}, {})
    assert r["conditions"]["user_notes"] == ["顺便提醒我别熬夜"]
    assert "补充说明" in r["summary"]


def test_does_not_mutate_input() -> None:
    cond = {"items": [{"title": "开场", "minutes": 2}]}
    turns.parse("删掉开场", "activity", cond, {})
    assert len(cond["items"]) == 1
