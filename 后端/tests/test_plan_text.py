from app.services.plan_text import public_body


def test_trip_keeps_stops_cost_and_open_questions_in_public_body():
    result = {
        "task_type": "travel",
        "items": [{"title": "咖啡馆", "minutes": 10, "detail": "返校前短暂停留"}],
        "timeline": {"start": "15:15", "arrive": "15:30", "leave": "15:40", "end": "15:55"},
        "pending_facts": ["是否需要预约"],
        "private_note": "留在个人页面", "rationale": "朋友的私人偏好",
    }
    validation = {"checks": [{"code": "BUDGET", "detail": {
        "total": 190, "per_person": 95, "budget_per_person": 80, "diff": 15, "cost_known": True,
    }}]}
    body = public_body(result, validation)
    for fragment in ["15:15", "15:30", "15:40", "15:55", "人均 95 元", "超出预算 15 元", "是否需要预约"]:
        assert fragment in body
    assert "留在个人页面" not in body
    assert "朋友的私人偏好" not in body


def test_activity_keeps_material_counts_and_unknown_price():
    body = public_body({"task_type": "activity", "materials_needed": [
        {"name": "名牌", "qty": 20, "unit_cost": None},
    ]}, {})
    assert "名牌（数量 20，单价待确认）" in body


def test_unknown_price_is_not_published_as_free():
    result = {"task_type": "travel"}
    budget = {"code": "BUDGET", "detail": {"total": 0, "per_person": 0, "cost_known": False}}
    assert "待确认" in public_body(result, {"checks": [budget]})
    assert "人均 0 元" not in public_body(result, {"checks": [budget]})
