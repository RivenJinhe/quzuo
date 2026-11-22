import json

from app.services import validation as v

CD = "第 8 页：例题讲解，先讲例题再总结。第 12 页：课后习题 1-3。"


def _review_ctx(available=5):
    return {
        "task_type": "review",
        "goal": "复习第 8 到 12 页",
        "available_minutes": available,
        "materials": [{
            "id": "m1", "title": "高数课件", "excerpt": CD,
            "source_kind": "file", "source_file": "高数课件.pdf",
            "source_page_start": 8, "source_page_end": 12,
        }],
    }


def _review_result(minutes=5):
    return {
        "task_type": "review",
        "headline": "15 分钟复习压到 5 分钟",
        "items": [{
            "title": "例题先讲", "minutes": minutes, "must_keep": True,
            "detail": "先讲第 8 页例题",
            "citations": [{"material_id": "m1", "excerpt": "第 8 页：例题讲解，先讲例题再总结。", "page": 8}],
        }],
    }


def test_review_15_to_5():
    res = _review_result(5)
    out = v.run_all(res, _review_ctx(5))
    assert out["block"] is None
    assert out["checks"][0]["ok"] is True
    dur = [c for c in out["checks"] if c["code"] == "DURATION_TOTAL"][0]
    assert dur["detail"]["total"] == 5


def test_review_over_available_is_notice_not_block():
    res = _review_result(5)
    res["items"][0]["must_keep"] = False
    out = v.run_all(res, _review_ctx(3))
    assert out["block"] is None
    dur = [c for c in out["checks"] if c["code"] == "DURATION_TOTAL"][0]
    assert dur["level"] == "notice" and dur["detail"]["diff"] == 2


def _travel_ctx():
    return {
        "task_type": "travel",
        "goal": "15:15 离馆，15:55 返校，中间想喝咖啡",
        "start_time": "15:15", "legs": [15, 15],
        "available_minutes": 10, "people": 1, "budget_per_person": 80,
        "materials": [{
            "id": "t1", "title": "美术馆笔记", "excerpt": "闭馆 16:30，步行 15 分钟到咖啡馆",
            "source_kind": "link", "source_url": "https://example.com/art",
        }],
    }


def _travel_result():
    return {
        "task_type": "travel",
        "headline": "美术馆—咖啡馆—返校",
        "items": [{
            "title": "咖啡馆小坐", "minutes": 30,
            "detail": "喝杯咖啡",
            "citations": [{"material_id": "t1", "excerpt": "步行 15 分钟到咖啡馆"}],
        }],
        "materials_needed": [{"name": "咖啡馆人均消费", "qty": 1, "unit_cost": 95}],
    }


def test_travel_coffee_10min_and_over_budget_15():
    res = _travel_result()
    out = v.run_all(res, _travel_ctx())
    assert out["block"] is None
    dur = [c for c in out["checks"] if c["code"] == "DURATION_TOTAL"][0]
    assert dur["detail"]["total"] == 30 and dur["detail"]["available"] == 10
    bud = [c for c in out["checks"] if c["code"] == "BUDGET"][0]
    assert bud["detail"]["per_person"] == 95.0
    assert bud["detail"]["diff"] == 15.0
    tl = [c for c in out["checks"] if c["code"] == "TIMELINE"][0]
    assert tl["detail"]["arrive"] == "15:30"
    assert tl["detail"]["leave"] == "15:40"
    assert tl["detail"]["end"] == "15:55"
    assert tl["detail"]["inside_minutes"] == 10


def _activity_ctx(available=25):
    return {
        "task_type": "activity",
        "goal": "20 人班级活动",
        "people": 20, "available_minutes": available,
        "fixed_furniture": {"桌椅": 20},
        "materials": [{"id": "a1", "title": "活动流程", "excerpt": "互相参观 12 分钟，猜词 8 分钟"}],
    }


def _activity_result(items=None):
    items = items or [
        {"title": "开场", "minutes": 2, "detail": "点名", "citations": [{"material_id": "a1", "excerpt": "互相参观 12 分钟"}]},
        {"title": "互相参观", "minutes": 12, "must_keep": True, "detail": "两组互访", "citations": [{"material_id": "a1", "excerpt": "互相参观 12 分钟"}]},
        {"title": "猜词", "minutes": 8, "detail": "题卡游戏", "citations": [{"material_id": "a1", "excerpt": "猜词 8 分钟"}]},
        {"title": "合影", "minutes": 3, "detail": "集体照", "citations": [{"material_id": "a1", "excerpt": "互相参观 12 分钟"}]},
    ]
    return {
        "task_type": "activity",
        "headline": "25 分钟班级活动",
        "items": items,
        "materials_needed": [
            {"name": "桌椅", "qty": 20, "unit_cost": 0},
            {"name": "题卡", "qty": 20, "unit_cost": 0},
            {"name": "名牌", "qty": 20, "unit_cost": 0},
        ],
    }


def test_activity_25_minutes_ok():
    out = v.run_all(_activity_result(), _activity_ctx(25))
    assert out["block"] is None
    dur = [c for c in out["checks"] if c["code"] == "DURATION_TOTAL"][0]
    assert dur["detail"]["total"] == 25


def test_activity_10_minutes_blocks_with_diff_2():
    out = v.run_all(_activity_result(), _activity_ctx(10))
    assert out["block"] is not None
    assert out["block"]["code"] == "CONFLICT_REQUIREMENTS"
    assert out["block"]["detail"]["required"] == 12
    assert out["block"]["detail"]["available"] == 10
    assert out["block"]["detail"]["diff"] == 2
    assert out["passed"] is False
    assert any(o["action"] == "extend_available" and o["extra_minutes"] == 2 for o in out["block"]["options"])


def test_remove_guessword_drops_cards():
    items = [i for i in _activity_result()["items"] if "猜词" not in i["title"]]
    res = _activity_result(items)
    out = v.run_all(res, _activity_ctx(25))
    assert out["block"] is None
    names = [m["name"] for m in res["materials_needed"]]
    assert "题卡" not in names
    assert "名牌" in names
    assert any(c["code"] == "MATERIAL_REMOVED" for c in out["corrections"])


def test_citation_gate_rejects_foreign_excerpt():
    res = _review_result(5)
    res["items"][0]["citations"] = [{"material_id": "m1", "excerpt": "第 9 页：这里有句话课本没写"}]
    out = v.run_all(res, _review_ctx(5))
    assert out["block"] is not None
    assert out["block"]["code"] == "SOURCE_INSUFFICIENT"
    assert out["checks"][1]["detail"]["dropped"][0]["reason"] == "EXCERPT_NOT_IN_MATERIAL"


def test_page_out_of_range_dropped():
    res = _review_result(5)
    res["items"][0]["citations"] = [{"material_id": "m1", "excerpt": "第 8 页：例题讲解", "page": 30}]
    out = v.run_all(res, _review_ctx(5))
    assert out["block"] is not None
    assert out["block"]["detail"]["dropped"][0]["reason"] == "PAGE_OUT_OF_RANGE"


def test_number_whitelist_flags_invented_price():
    res = _travel_result()
    res["items"][0]["detail"] = "喝杯咖啡，人均 128 元"
    out = v.run_all(res, _travel_ctx())
    nums = [c for c in out["checks"] if c["code"] == "NUMBER_WHITELIST"][0]
    assert nums["detail"]["flagged"] == [{"item": "咖啡馆小坐", "number": "128"}]
    assert any("待确认" in p for p in res.get("pending_facts") or [])
