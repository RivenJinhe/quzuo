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


def test_travel_conditions_extracted_together_and_deadline_can_change() -> None:
    first = turns.parse(
        "四人从美术馆出发，15:15 离馆，去咖啡馆和返校各需十五分钟，"
        "15:55 前回校，还想喝半小时咖啡，已知人均花费 95 元，人均预算 80 元。",
        "travel", {}, {},
    )
    conditions = first["conditions"]
    assert conditions["origin"] == "美术馆"
    assert conditions["destination"] == "学校"
    assert conditions["stop"] == "咖啡馆"
    assert (conditions["start_time"], conditions["deadline"], conditions["legs"]) == ("15:15", "15:55", [15, 15])
    assert (conditions["available_minutes"], conditions["desired_stay_minutes"]) == (10, 30)
    assert (conditions["people"], conditions["known_cost_per_person"], conditions["budget_per_person"]) == (4, 95, 80)
    revised = turns.parse("出发时间改为 15:25，最晚 16:05 回校", "travel", conditions, first["retained"])
    assert (revised["conditions"]["start_time"], revised["conditions"]["deadline"]) == ("15:25", "16:05")
    assert revised["conditions"]["available_minutes"] == 10


def test_activity_total_is_separate_from_the_retained_item_duration() -> None:
    first = turns.parse(
        "20 人迎新，桌椅固定，原有 40 分钟现缩到 25 分钟仍想保留 12 分钟的同桌互访",
        "activity", {}, {},
    )
    assert first["conditions"]["total_minutes"] == 25
    assert first["conditions"]["seating_fixed"] is True
    assert first["retained"]["fixed_item_minutes"] == {"同桌互访": 12}
    revised = turns.parse("只剩10分钟", "activity", first["conditions"], first["retained"])
    assert revised["conditions"]["available_minutes"] == 10
    assert revised["retained"]["fixed_item_minutes"] == {"同桌互访": 12}


def test_relative_example_reference_prefers_a_unique_previous_example() -> None:
    first = turns.parse(
        "只剩 5 分钟，保留这道题", "review",
        {"items": [{"title": "用排练与兼职说明机会成本"}, {"title": "复述定义"},
                   {"title": "练例题：排练与兼职"}]}, {},
    )
    assert first["retained"]["must_keep_texts"] == ["练例题：排练与兼职"]
    assert first["conditions"]["available_minutes"] == 5
    ambiguous = turns.parse(
        "只剩 5 分钟，保留这道例题", "review",
        {"items": [{"title": "排练例题"}, {"title": "选课例题"}]}, {},
    )
    assert not ambiguous["retained"]
    assert ambiguous["unresolved"]


def test_total_budget_is_recomputed_when_people_are_given_later() -> None:
    first = turns.parse("总预算 640 元", "travel", {}, {})
    assert "budget_per_person" not in first["conditions"]
    assert first["unresolved"]
    revised = turns.parse("我们有八人", "travel", first["conditions"], {})
    assert revised["conditions"]["budget_per_person"] == 80
