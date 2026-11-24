"""把复核后的行程、费用和物料整理成产品页和已存正文共用的公开内容。"""
from __future__ import annotations


def public_sections(result: dict, validation: dict) -> list[dict]:
    sections = []
    timeline = result.get("timeline") or {}
    stops = [f"{label} {timeline[key]}" for key, label in
             (("start", "出发"), ("arrive", "到达"), ("leave", "离开"), ("end", "结束"))
             if timeline.get(key)]
    if stops or timeline.get("deadline") or timeline.get("legs"):
        times = [" → ".join(stops)] if stops else []
        if timeline.get("legs"):
            times.append("各段路程 " + "、".join("待确认" if leg is None else f"{leg} 分钟" for leg in timeline["legs"]))
        if timeline.get("inside_minutes") is not None:
            times.append(f"停留 {timeline['inside_minutes']} 分钟")
        if timeline.get("deadline"):
            times.append(f"最晚返回 {timeline['deadline']}")
        if timeline.get("requested_end") and (timeline.get("overrun_minutes") or 0) > 0:
            times.append(f"若停留 {timeline['requested_inside_minutes']} 分钟，将于 {timeline['requested_end']} 返回，"
                         f"晚 {timeline['overrun_minutes']} 分钟")
        sections.append({"title": "时间安排", "lines": times})

    materials = result.get("materials_needed") or []
    lines = []
    for item in materials:
        if not item.get("name"):
            continue
        quantity = item.get("qty")
        price = item.get("unit_cost")
        cost = "单价待确认" if price is None else f"单价 {price} 元"
        count = "数量待确认" if quantity is None else f"数量 {quantity}"
        lines.append(f"{item['name']}（{count}，{cost}）")
    if lines:
        sections.append({"title": "所需物料与费用", "lines": lines, "sources": _sources(materials)})

    budget = next((c["detail"] for c in validation.get("checks", [])
                   if c.get("code") == "BUDGET" and isinstance(c.get("detail"), dict)), {})
    if result.get("task_type") == "travel" or materials or budget.get("budget_per_person") is not None:
        cost_lines = [f"{row['title']}：人均 {row['per_person']} 元"
                      for row in budget.get("cost_items", []) if row.get("per_person") is not None]
        cost_known = budget.get("cost_known", bool(materials) and not budget.get("unknown_cost_items"))
        if cost_known:
            if budget.get("total") is not None:
                cost_lines.append(f"已知总额 {budget['total']} 元")
            if budget.get("per_person") is not None:
                cost_lines.append(f"已知人均 {budget['per_person']} 元")
        if budget.get("budget_per_person") is not None:
            cost_lines.append(f"人均预算 {budget['budget_per_person']} 元")
        if (budget.get("diff") or 0) > 0:
            cost_lines.append(f"人均超出预算 {budget['diff']} 元")
        if result.get("task_type") == "travel" and (not cost_known or budget.get("unknown_cost_items")):
            cost_lines.append("尚有费用待确认")
        if cost_lines:
            sections.append({"title": "费用", "lines": cost_lines, "sources": _sources(result.get("costs") or [])})
    pending = [str(fact) for fact in result.get("pending_facts") or [] if fact]
    if pending:
        sections.append({"title": "待确认", "lines": pending})
    return sections


def _sources(entries: list[dict]) -> list[dict]:
    return [{"id": citation["citation_id"], "label": entry.get("title") or entry.get("name")}
            for entry in entries for citation in entry.get("citations") or [] if citation.get("citation_id")]


def public_body(result: dict, validation: dict) -> str:
    """只导出选定方案的正文。私人备注、选择理由和原始摘录留在个人页面。"""
    paragraphs = []
    for number, item in enumerate(result.get("items") or [], 1):
        heading = f"{number}. {item.get('title', '')}"
        if item.get("minutes") is not None:
            heading += f"（{item['minutes']} 分钟）"
        pages = list(dict.fromkeys(str(c["page"]) for c in item.get("citations") or [] if c.get("page")))
        if pages:
            heading += " · 第 " + "、".join(pages) + " 页"
        paragraphs.append("\n".join(filter(None, (heading, item.get("detail")))))
    for section in public_sections(result, validation):
        paragraphs.append("\n".join([section["title"], *section["lines"]]))
    return "\n\n".join(paragraphs)
