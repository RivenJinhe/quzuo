"""比较前后两版方案的时长、环节和物料数量。"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import time
from math import isfinite


def _name(text) -> str:
    return " ".join(str(text or "").split())


def _items(result: dict) -> list[tuple[str, int]]:
    items = []
    for item in result.get("items") or []:
        if isinstance(item, dict):
            try:
                minutes = int(item.get("minutes") or 0)
            except (TypeError, ValueError, OverflowError):
                minutes = 0
            items.append((_name(item.get("title")), minutes))
    return items


def _duration(result: dict, items: list[tuple[str, int]]) -> int:
    timeline = result.get("timeline")
    if (result.get("task_type") == "travel" and isinstance(timeline, dict)
            and timeline.get("start") and timeline.get("end")):
        try:
            start = time.fromisoformat(timeline["start"])
            end = time.fromisoformat(timeline["end"])
        except (TypeError, ValueError):
            pass
        else:
            return ((end.hour - start.hour) * 60 + end.minute - start.minute) % (24 * 60)
    return sum(minutes for _, minutes in items)


def _materials(result: dict) -> dict:
    quantities = {}
    for material in result.get("materials_needed") or []:
        if not isinstance(material, dict) or not (name := _name(material.get("name"))):
            continue
        try:
            value = float(material.get("qty"))
            qty = (int(value) if value.is_integer() else value) if isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            qty = None
        previous = quantities.get(name, 0)
        quantities[name] = previous + qty if previous is not None and qty is not None else None
    return quantities


def diff_result(prev: dict | None, cur: dict | None) -> list[dict]:
    """为每项变化返回 kind、label、from 和 to，供界面展示。"""
    if not prev or not cur:
        return []
    lines = []
    before_items = _items(prev)
    after_items = _items(cur)
    before_total = _duration(prev, before_items)
    after_total = _duration(cur, after_items)
    if before_total != after_total:
        lines.append({"kind": "duration", "label": "总时长 %d → %d 分钟" % (before_total, after_total),
                      "from": before_total, "to": after_total})

    positions = defaultdict(deque)
    for index, (title, _) in enumerate(before_items):
        positions[title.casefold()].append(index)
    matched = []
    for title, minutes in after_items:
        matches = positions[title.casefold()]
        if not matches:
            lines.append({"kind": "added", "label": "加了「%s」" % title, "from": None, "to": title})
            continue
        index = matches.popleft()
        matched.append(index)
        _, original_minutes = before_items[index]
        if minutes != original_minutes:
            lines.append({"kind": "item_duration", "label": "「%s」%d → %d 分钟" % (title, original_minutes, minutes),
                          "from": original_minutes, "to": minutes})
    matched_positions = set(matched)
    for index, (title, _) in enumerate(before_items):
        if index not in matched_positions:
            lines.append({"kind": "removed", "label": "删掉「%s」" % title, "from": title, "to": None})
    if matched != sorted(matched):
        old_order = [before_items[index][0] for index in sorted(matched)]
        new_order = [before_items[index][0] for index in matched]
        if old_order != new_order:
            lines.append({"kind": "order", "label": "环节顺序调整为 " + " → ".join(new_order),
                          "from": old_order, "to": new_order})

    before_materials, after_materials = _materials(prev), _materials(cur)
    for name in dict.fromkeys([*before_materials, *after_materials]):
        old = before_materials.get(name, 0)
        new = after_materials.get(name, 0)
        if old != new:
            before_label = "待确认" if old is None else str(old)
            after_label = "待确认" if new is None else str(new)
            lines.append({"kind": "material", "label": "%s %s → %s" % (name, before_label, after_label),
                          "from": old, "to": new})
    return lines or [{"kind": "same", "label": "环节、时长和物料数量与上一版一致", "from": None, "to": None}]
