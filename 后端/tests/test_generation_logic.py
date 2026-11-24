from app.services.diffgen import diff_result
from app.services.validation import check_citations, verified_citations


def test_exact_courseware_page_requires_the_quote_to_be_on_that_page():
    ctx = {"materials": [{
        "id": "slides",
        "source_kind": "courseware",
        "source_page_start": 8,
        "source_page_end": 9,
        "excerpt": "<!-- page 8 -->\n\n**例题**：先求导。\n\n<!-- page 9 -->\n\n再检查驻点。",
    }]}
    kept, dropped = verified_citations([
        {"material_id": "slides", "excerpt": "例题：先求导。", "page": 9},
        {"material_id": "slides", "excerpt": "例题：先求导。"},
    ], ctx)
    assert [citation["page"] for citation in kept] == [8]
    assert [citation["reason"] for citation in dropped] == ["PAGE_UNVERIFIED"]


def test_courseware_page_range_does_not_prove_an_exact_page():
    ctx = {"materials": [{
        "id": "slides",
        "source_kind": "courseware",
        "source_page_start": 8,
        "source_page_end": 9,
        "excerpt": "求导检查驻点。",
    }]}
    kept, dropped = verified_citations([
        {"material_id": "slides", "excerpt": "求导检查驻点。", "page": 8},
        {"material_id": "slides", "excerpt": "求导检查驻点。"},
    ], ctx)
    assert [citation["page"] for citation in kept] == [None]
    assert len(dropped) == 1


def test_quote_cannot_join_paragraphs_and_ungrounded_steps_move_to_pending():
    ctx = {"materials": [{"id": "note", "excerpt": "前门集合。\n\n现场发题卡。"}]}
    result = {"items": [
        {"title": "集合", "citations": [{"material_id": "note", "excerpt": "前门集合。"}]},
        {"title": "发题卡", "citations": [{"material_id": "note", "excerpt": "前门集合。 现场发题卡。"}]},
    ]}
    gate = check_citations(result, ctx)
    assert gate["level"] == "notice"
    assert [item["title"] for item in result["items"]] == ["集合"]
    assert gate["detail"]["ungrounded_items"] == ["发题卡"]
    assert "发题卡" in result["pending_facts"][0]


def test_diff_keeps_all_material_changes_after_duration_and_agenda_changes():
    before = {
        "items": [{"title": "破冰", "minutes": 3}, {"title": "互访", "minutes": 5}],
        "materials_needed": [{"name": "名牌", "qty": 20}, {"name": "题卡", "qty": 20},
                             {"name": "胶带", "qty": 2}],
    }
    after = {
        "items": [{"title": "互访", "minutes": 7}, {"title": "分享", "minutes": 4}],
        "materials_needed": [{"name": "名牌", "qty": 24}, {"name": "胶带", "qty": 4},
                             {"name": "纸张", "qty": 24}],
    }
    changes = diff_result(before, after)
    assert [line["kind"] for line in changes].count("material") == 4
    assert {(line["kind"], line["from"], line["to"]) for line in changes} >= {
        ("duration", 8, 11), ("item_duration", 5, 7), ("removed", "破冰", None), ("added", None, "分享"),
        ("material", 20, 24), ("material", 20, 0), ("material", 2, 4), ("material", 0, 24),
    }
