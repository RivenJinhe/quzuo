"""按用户条件检查候选环节，并计算复习、出行和活动的可用时长。"""
from __future__ import annotations

import re

from app.services.turns import same_target

_MOVE_SEATS = (
    r"换(?:座位|座|位|桌|椅子)|换到.{0,8}(?:座位|座|桌)|交换(?:座位|桌椅|位置)|"
    r"(?:移动|挪动?|搬动?|重新布置)(?:桌椅|座位|桌子|桌|椅子)|"
    r"(?:把|将)(?:桌椅|座位|桌子|椅子)(?:移开|挪开|搬走|搬开)"
)
_NO_MOVE = re.compile(r"(?:无需|不用|不必|不需要|不要求|不能|禁止|不要|避免|不得|不)" + "(?:" + _MOVE_SEATS + ")")
_ROUTE_KINDS = {"outbound", "return", "leg", "route", "transport"}
_REHEARSE = re.compile(r"例题|例子|练题|练习题|案例")
_RECAP = re.compile(r"复述|回顾|复盘|自述|试讲|总结")


def item_minutes(item: dict) -> int:
    value = item.get("minutes")
    return value if type(value) is int and 0 <= value <= 1440 else 0


def matches_item(name: str, item: dict) -> bool:
    title = str(item.get("title") or "")
    page = re.search(r"第?\s*(\d+)\s*页", name)
    if page and _REHEARSE.search(name):
        return bool(_REHEARSE.search(title)) and any(
            str(citation.get("page")) == page[1] for citation in item.get("citations") or []
            if isinstance(citation, dict)
        )
    generic = {"例题", "例子", "案例", "练例题"}
    if title in generic and name not in generic and _REHEARSE.search(name):
        return False
    return same_target(name, title)


def retained_requirements(ctx: dict) -> dict[str, int | None]:
    conditions = ctx.get("conditions") or {}
    retained = ctx.get("retained") or {}
    required = {name: None for name in ctx.get("retained_texts") or retained.get("must_keep_texts") or []
                if isinstance(name, str) and name.strip()}

    def set_duration(name, minutes):
        if isinstance(name, str) and name.strip() and type(minutes) is int and minutes >= 0:
            key = next((entry for entry in required if same_target(entry, name)), name)
            required[key] = minutes

    for item in conditions.get("items") or []:
        if isinstance(item, dict) and item.get("must_keep"):
            name = item.get("title")
            if ctx.get("task_type") == "review":
                if isinstance(name, str) and name.strip() and not any(same_target(name, n) for n in required):
                    required[name] = None
            else:
                set_duration(name, item.get("minutes"))
    for name, minutes in (retained.get("fixed_item_minutes") or {}).items():
        set_duration(name, minutes)
    return required


def remove_excluded(result: dict, ctx: dict) -> list[dict]:
    conditions = ctx.get("conditions") or {}
    excluded = conditions.get("excluded_items") or []
    fixed_seats = ctx.get("task_type") == "activity" and (
        conditions.get("seating_fixed") or conditions.get("fixed_furniture") or ctx.get("fixed_furniture")
    )
    corrections = []
    kept = []
    for item in result["items"]:
        if any(matches_item(str(name), item) for name in excluded):
            reason = "USER_EXCLUDED"
        elif fixed_seats and (item.get("requires_seat_change") is True or re.search(
            _MOVE_SEATS, _NO_MOVE.sub("", "%s %s" % (item["title"], item["detail"]))
        )):
            reason = "FIXED_SEATING"
        else:
            kept.append(item)
            continue
        corrections.append({"code": "ITEM_REMOVED", "title": item["title"], "reason": reason})
    result["items"] = kept
    return corrections


def apply_retained(items: list[dict], required: dict[str, int | None]) -> list[dict]:
    corrections = []
    for name, duration in required.items():
        item = next((candidate for candidate in items if matches_item(name, candidate)), None)
        if item is None:
            continue
        item["must_keep"] = True
        if duration is not None and item_minutes(item) != duration:
            corrections.append(set_minutes(item, duration))
    return corrections


def set_minutes(item: dict, minutes: int) -> dict:
    previous = item_minutes(item)
    detail = item["detail"]
    # 同步原说明中直接重复的环节用时。材料原文引用保持原样。
    if previous != minutes and previous > 0:
        detail = re.sub(r"(?<![\d.])" + str(previous) + r"(\s*分钟)", lambda m: str(minutes) + m[1], detail)
    item["detail"] = detail
    item["minutes"] = minutes
    return {"code": "ITEM_DURATION_RECOMPUTED", "title": item["title"], "from": previous, "to": minutes}


def minutes_for_window(items: list[dict], minutes: int, required: set[int]) -> tuple[list[dict], list[dict]]:
    """按原建议的相对用时压缩，保留每个安排至少一分钟。"""
    if not items:
        return [], []
    if minutes <= 0:
        return [], [{"code": "ITEM_REMOVED", "title": i["title"], "reason": "TIME_LIMIT"} for i in items]
    if sum(item_minutes(i) for i in items) <= minutes:
        return items, []
    optional = [i for i in items if id(i) not in required]
    retained = [i for i in items if id(i) in required]
    selected = retained + optional[:max(0, minutes - len(retained))]
    selected_ids = {id(i) for i in selected}
    if not selected:
        return [], []
    # 各环节至少先分一分钟，再按原始用时分配剩余分钟。
    free = max(0, minutes - len(selected))
    weights = [max(item_minutes(i) - 1, 0) for i in selected]
    weight_total = sum(weights)
    if weight_total == 0:
        weights = [1] * len(selected)
        weight_total = len(selected)
    parts = [divmod(free * weight, weight_total) for weight in weights]
    allocated = [1 + whole for whole, _ in parts]
    priorities = sorted(range(len(parts)), key=lambda index: parts[index][1], reverse=True)
    for index in priorities[:minutes - sum(allocated)]:
        allocated[index] += 1
    corrections = [set_minutes(i, duration) for i, duration in zip(selected, allocated) if item_minutes(i) != duration]
    corrections.extend({"code": "ITEM_REMOVED", "title": i["title"], "reason": "TIME_LIMIT"}
                       for i in items if id(i) not in selected_ids)
    return [i for i in items if id(i) in selected_ids], corrections


def fit_review(items: list[dict], minutes: int, required: dict[str, int | None]) -> tuple[list[dict], list[dict]]:
    if minutes <= 0:
        return minutes_for_window(items, minutes, set())
    examples = [i for i in items if _REHEARSE.search(i["title"]) and not _RECAP.search(i["title"])]
    recap = [i for i in items if _RECAP.search(i["title"])]
    focus = next((i for i in examples if any(matches_item(name, i) for name in required)), None)
    focus = focus or next(iter(examples), None)
    retained = {id(i) for i in items if any(matches_item(name, i) for name in required)}
    if focus is not None:
        retained.add(id(focus))
    if (focus is None or not recap or minutes < 2 or len(retained) - 1 > minutes // 3
            or retained - {id(focus), *(id(i) for i in recap)}):
        return minutes_for_window(items, minutes, retained)

    # 复述占约三分之一，短时间至少留一分钟，余下时间优先练重点。
    wrap = max(1, minutes // 3)
    main = minutes - wrap
    remaining = [i for i in items if i is not focus]
    remaining = recap + [i for i in remaining if not any(i is entry for entry in recap)]
    corrections = [set_minutes(focus, main)] if item_minutes(focus) != main else []
    selected, resized = minutes_for_window(remaining, wrap, {id(recap[0])} | (retained - {id(focus)}))
    corrections.extend(resized)
    used = sum(item_minutes(i) for i in selected)
    if selected and used < wrap:
        corrections.append(set_minutes(selected[0], item_minutes(selected[0]) + wrap - used))
    return [focus, *selected], corrections


def fit_activity(items: list[dict], minutes: int, required: dict[str, int | None]) -> tuple[list[dict], list[dict]]:
    fixed = [i for i in items if any(duration is not None and matches_item(name, i) for name, duration in required.items())]
    fixed_ids = {id(i) for i in fixed}
    adjustable = [i for i in items if id(i) not in fixed_ids]
    retained = {id(i) for i in adjustable if any(matches_item(name, i) for name in required)}
    available = minutes - sum(item_minutes(i) for i in fixed)
    selected, corrections = minutes_for_window(adjustable, available, retained)
    kept = fixed_ids | {id(i) for i in selected}
    return [i for i in items if id(i) in kept], corrections


def stops(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("kind") not in _ROUTE_KINDS and not re.fullmatch(
        r"(?:直接|按时|立即)?(?:回校|返校|回学校|返回|出发|离馆)", i["title"].strip()
    )]


def skip_stop(conditions: dict, items: list[dict] | None = None) -> bool:
    known = conditions.get("stops") or ([conditions["stop"]] if conditions.get("stop") else [])
    exclusions = conditions.get("excluded_items") or []
    if items is not None and stops(items):
        return False
    if known:
        return all(any(same_target(str(name), stop) for name in exclusions) for stop in known)
    return items is not None and bool(exclusions)
