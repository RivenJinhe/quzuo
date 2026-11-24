import json

from app.services import turns, validation as v

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
            "id": "t1", "title": "美术馆笔记", "excerpt": "闭馆 16:30，步行 15 分钟到咖啡馆。咖啡馆人均消费 95 元。",
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
        "materials_needed": [{
            "name": "咖啡馆人均消费", "qty": 1, "unit_cost": 95,
            "citations": [{"material_id": "t1", "excerpt": "咖啡馆人均消费 95 元。"}],
        }],
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


def _check(out, code):
    return next(check["detail"] for check in out["checks"] if check["code"] == code)


def _natural_context(goal, task_type, materials, previous=None):
    previous = previous or {}
    state = turns.parse(goal, task_type, previous.get("conditions") or {}, previous.get("retained") or {})
    return {"goal": goal, "task_type": task_type, "materials": materials,
            "conditions": state["conditions"], "retained": state["retained"]}


def test_review_allocates_time_to_the_example_and_recap_on_both_turns():
    context = _natural_context(
        "明天要口头讲解，今晚只有十五分钟，例题一定要讲。", "review", [{
            "id": "course", "title": "机会成本讲义", "source_kind": "courseware",
            "source_page_start": 8, "source_page_end": 12,
            "excerpt": "<!-- page 8 -->\n机会成本是被放弃的最佳替代选择。\n\n"
                       "<!-- page 12 -->\n可参加社团排练或参加有薪兼职。",
        }],
    )

    def candidate():
        return {
            "task_type": "review", "headline": "机会成本口头讲解", "items": [
                {"title": "复述定义", "minutes": 8, "detail": "复述机会成本的定义",
                 "citations": [{"material_id": "course", "excerpt": "机会成本是被放弃的最佳替代选择。"}]},
                {"title": "练例题：排练与兼职", "minutes": 4, "detail": "选排练时放弃可获得的兼职收入",
                 "citations": [{"material_id": "course", "excerpt": "可参加社团排练或参加有薪兼职。"}]},
            ],
        }

    first = candidate()
    assert v.run_all(first, context)["passed"]
    assert [item["minutes"] for item in first["items"]] == [10, 5]
    assert [item["citations"][0]["page"] for item in first["items"]] == [12, 8]
    revised = _natural_context(
        "只剩五分钟，保留这道题", "review", context["materials"],
        {"conditions": {**context["conditions"], "items": first["items"]}, "retained": context["retained"]},
    )
    result = candidate()
    assert v.run_all(result, revised)["passed"]
    assert [item["minutes"] for item in result["items"]] == [4, 1]
    assert result["items"][0]["title"] == first["items"][0]["title"]


def test_travel_natural_conditions_restrict_stop_and_produce_actionable_options():
    context = _natural_context(
        "三人，15:15 离馆，去咖啡馆和返校各十五分钟，"
        "15:55 前回校，还想喝半小时咖啡，已知人均花费 95 元、预算 80 元。",
        "travel", _travel_ctx()["materials"],
    )
    result = _travel_result()
    result["items"][0]["minutes"] = 22
    result["timeline"] = {"end": "16:15"}
    verified = v.run_all(result, context)
    assert verified["passed"]
    assert result["items"][0]["minutes"] == 10
    assert result["timeline"]["end"] == "15:55"
    assert result["timeline"]["requested_end"] == "16:15"
    assert result["timeline"]["overrun_minutes"] == 20
    assert _check(verified, "BUDGET")["diff"] == 15
    assert _check(verified, "BUDGET")["total"] == 285
    duration_options = next(n["options"] for n in verified["notices"] if n["code"] == "DURATION_TOTAL")
    assert {option["action"] for option in duration_options} == {"shorten_stay", "remove_item", "extend_available"}
    assert all(option["effect"] and option["send"] for option in duration_options)


def test_travel_removing_one_stop_preserves_another_and_flags_new_route():
    materials = _travel_ctx()["materials"]
    materials[0]["excerpt"] += "书店可以停留看书。"
    context = _natural_context(
        "15:15 出发，先去咖啡馆再去书店，出发和返校各需十五分钟，最晚 15:55 回校。",
        "travel", materials,
    )
    revised = _natural_context("删掉咖啡馆", "travel", materials, context)
    result = _travel_result()
    result["items"].append({
        "title": "书店", "detail": "看看书架", "minutes": 12,
        "citations": [{"material_id": "t1", "excerpt": "书店可以停留看书。"}],
    })
    assert v.run_all(result, revised)["passed"]
    assert [item["title"] for item in result["items"]] == ["书店"]
    assert result["timeline"]["stop"] == "书店"
    assert result["timeline"]["end"] is None
    assert "去往停留点的路程待确认" in result["pending_facts"]


def test_travel_unknown_cost_stays_distinct_from_free():
    context = _travel_ctx()
    result = _travel_result()
    result["materials_needed"] = []
    result["costs"] = [{
        "title": result["items"][0]["title"], "per_person": 95,
        "citations": [{"material_id": "t1", "excerpt": "步行 15 分钟到咖啡馆"}],
    }]
    check = _check(v.run_all(result, context), "BUDGET")
    assert not check["cost_known"] and check["per_person"] is None
    assert result["costs"][0]["per_person"] is None
    context["materials"][0]["excerpt"] += "咖啡馆的饮品并非免费。"
    result["costs"][0]["per_person"] = 0
    result["costs"][0]["citations"] = [{"material_id": "t1", "excerpt": "咖啡馆的饮品并非免费。"}]
    assert _check(v.run_all(result, context), "BUDGET")["per_person"] is None
    context["materials"][0]["excerpt"] += "咖啡馆的小院免费开放。"
    result["costs"] = [{
        "title": result["items"][0]["title"], "per_person": 0,
        "citations": [{"material_id": "t1", "excerpt": "咖啡馆的小院免费开放。"}],
    }]
    check = _check(v.run_all(result, context), "BUDGET")
    assert check["cost_known"] and check["per_person"] == 0


def test_activity_constraints_recalculate_compatible_flow_and_cards():
    materials = [{
        "id": "a1", "title": "活动流程",
        "excerpt": "主持人开场。与同桌互相参观。请同学分享。主持人收尾。猜词要题卡。音乐游戏需要换座。",
    }]
    context = _natural_context(
        "20 人迎新，桌椅固定，原有四十分钟，缩到二十五分钟，仍想保留十二分钟的同桌互访，删掉猜词",
        "activity", materials,
    )
    candidates = [
        ("开场", 3, "主持人开场。"), ("互相参观", 9, "与同桌互相参观。"),
        ("分享", 11, "请同学分享。"), ("收尾", 4, "主持人收尾。"), ("猜词", 6, "猜词要题卡。"),
        ("音乐游戏", 7, "音乐游戏需要换座。"),
    ]
    result = _activity_result([
        {"title": title, "minutes": minutes, "detail": excerpt,
         "citations": [{"material_id": "a1", "excerpt": excerpt}]}
        for title, minutes, excerpt in candidates
    ])
    out = v.run_all(result, context)
    assert out["passed"]
    assert [(item["title"], item["minutes"]) for item in result["items"]] == [
        ("开场", 2), ("互相参观", 12), ("分享", 8), ("收尾", 3),
    ]
    assert all(item["name"] != "题卡" for item in result["materials_needed"])
    assert any(c["reason"] == "FIXED_SEATING" for c in out["corrections"] if c["code"] == "ITEM_REMOVED")
    revised = _natural_context("总时长只剩十分钟", "activity", materials, context)
    block = v.request_conflict(revised)
    assert block["detail"]["diff"] == 2
    assert {option["action"] for option in block["options"]} == {"extend_available", "shorten_item", "remove_item"}
