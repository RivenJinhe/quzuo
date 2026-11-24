"""程序侧复核（BE-420~427）。

纯函数：输入模型输出的结果 dict 与本次请求上下文 ctx，返回 checks / corrections / block。
level: "ok" | "notice" | "block"。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from math import isfinite

from app.services import planning_rules as rules
from app.services.turns import same_target, travel_window

BLOCK = "block"
NOTICE = "notice"
OK = "ok"

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


# ---------- 文本归一化 ----------

def norm_loose(text) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).lower()
    return re.sub(r"\s+", "", t)


def norm_match(text) -> str:
    """子串匹配用：剥 Markdown/HTML 标记、去空白标点、NFKC。"""
    t = norm_loose(text)
    t = re.sub(r"<!--.*?-->", "", t)
    t = re.sub(r"<[^>]{1,40}>", "", t)
    t = re.sub(r"[|*`#>_~\[\]-]", "", t)
    return re.sub(r"[，。；：、！？“”‘’（）《》【】,.!:;\"'()\[\]<>]", "", t)


def numbers_in(text) -> set:
    return set(_NUM_RE.findall(norm_loose(text)))


def _parse_hhmm(value):
    if not value:
        return None
    parts = str(value).strip().split(":")
    try:
        return datetime(2000, 1, 1, int(parts[0]), int(parts[1])) if len(parts) == 2 else None
    except (ValueError, IndexError):
        return None


def _fmt(dt):
    return dt.strftime("%H:%M") if dt else None


_minutes = rules.item_minutes


# ---------- V6 结构完整性 ----------

_REQUIRED = ("task_type", "headline", "items")


def check_structure(result, ctx):
    if not isinstance(result, dict):
        return {"code": "STRUCTURE", "ok": False, "level": BLOCK, "detail": {"reason": "EXPECTED_OBJECT"}}
    missing = [k for k in _REQUIRED if not result.get(k)]
    bad_items = []
    if result.get("task_type") != ctx.get("task_type"):
        bad_items.append({"reason": "TASK_TYPE_MISMATCH"})
    if not isinstance(result.get("headline"), str):
        bad_items.append({"reason": "INVALID_HEADLINE"})
    if result.get("items") and not isinstance(result["items"], list):
        bad_items.append({"reason": "EXPECTED_ITEMS_LIST"})
    elif result.get("items"):
        for idx, it in enumerate(result["items"]):
            if not isinstance(it, dict):
                bad_items.append({"index": idx, "reason": "EXPECTED_ITEM_OBJECT"})
                continue
            if not isinstance(it.get("title"), str) or not it["title"].strip():
                bad_items.append({"index": idx, "reason": "MISSING_TITLE"})
            if not isinstance(it.get("detail"), str) or not it["detail"].strip():
                bad_items.append({"index": idx, "reason": "MISSING_DETAIL"})
            if type(it.get("minutes")) is not int or not 0 <= it["minutes"] <= 1440:
                bad_items.append({"index": idx, "reason": "INVALID_DURATION"})
    ok = not missing and not bad_items
    return {
        "code": "STRUCTURE",
        "ok": ok,
        "level": OK if ok else BLOCK,
        "detail": {"missing": missing, "bad_items": bad_items},
    }


# ---------- V5 引用闸门 ----------

_PAGE_MARKER = re.compile(
    r"(?m)^[ \t]*<!--[ \t]*page[ \t]+([1-9]\d{0,3})[ \t]*-->[ \t]*$"
    r"|(?:^|(?<=[。！？]))[ \t]*(?=第[ \t]*([1-9]\d{0,3})[ \t]*页[ \t]*[：:])"
)
_PARAGRAPH_BREAK = re.compile(r"\n(?:[ \t]*\n)+|\n(?=[ \t]*(?:#{1,6} |[-+*] |\d+[.)] |[>|]))")
_MARKDOWN_PREFIX = re.compile(r"(?m)^[ \t]*(?:#{1,6} +|[-+*] +|\d+[.)] +|> *)")
_MARKDOWN_INLINE = re.compile(r"(\*\*|~~|\*|\x60)(?=\S)(.+?)(?<=\S)\1")
_MARKDOWN_UNDERSCORE = re.compile(r"(?<!\w)(__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")


def _quote_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.DOTALL)
    t = _MARKDOWN_PREFIX.sub("", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = _MARKDOWN_INLINE.sub(r"\2", t)
    t = _MARKDOWN_UNDERSCORE.sub(r"\2", t)
    return " ".join(t.split())


def _source_sections(material):
    text = str(material.get("excerpt") or "").replace("\r\n", "\n")
    kind = material.get("source_kind")
    lo = material.get("source_page_start")
    hi = material.get("source_page_end") or lo
    if kind not in ("courseware", "file"):
        yield None, text
        return
    markers = list(_PAGE_MARKER.finditer(text))
    if not markers:
        yield lo if lo == hi else None, text
        return
    if markers[0].start():
        yield None, text[:markers[0].start()]
    for index, marker in enumerate(markers):
        page = int(marker[1] or marker[2])
        if (lo is not None and page < lo) or (hi is not None and page > hi):
            page = None
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        yield page, text[marker.end():end]


def verified_citations(citations, ctx) -> tuple[list[dict], list[dict]]:
    """只接纳本次材料中有连续原文、页码可验证的出处。"""
    materials = {str(m.get("id")): m for m in (ctx.get("materials") or [])}
    kept = []
    dropped = []
    for citation in citations if isinstance(citations, list) else []:
        if not isinstance(citation, dict):
            dropped.append({"reason": "INVALID_CITATION"})
            continue
        mid = str(citation.get("material_id") or "")
        material = materials.get(mid)
        if material is None:
            dropped.append({"material_id": mid, "reason": "MATERIAL_NOT_IN_REQUEST"})
            continue
        raw_excerpt = citation.get("excerpt")
        excerpt = _quote_text(raw_excerpt) if isinstance(raw_excerpt, str) else ""
        pages = {
            page for page, section in _source_sections(material)
            if excerpt and any(excerpt in _quote_text(part) for part in _PARAGRAPH_BREAK.split(section))
        }
        if not pages:
            dropped.append({"material_id": mid, "reason": "EXCERPT_NOT_IN_MATERIAL"})
            continue
        requested = citation.get("page")
        if requested is None:
            page = next(iter(pages)) if len(pages) == 1 else None
        else:
            page = int(requested) if re.fullmatch(r"[1-9]\d{0,3}", str(requested)) else None
            if page is None or page not in pages:
                lo = material.get("source_page_start")
                hi = material.get("source_page_end") or lo
                reason = "PAGE_UNVERIFIED"
                if page is not None and lo is not None and not lo <= page <= hi:
                    reason = "PAGE_OUT_OF_RANGE"
                dropped.append({"material_id": mid, "reason": reason})
                continue
        kept.append({"material_id": mid, "excerpt": raw_excerpt.strip(), "page": page})
    return kept, dropped


def check_citations(result, ctx):
    grounded = []
    ungrounded = []
    dropped = []
    citation_count = 0
    for item in result.get("items") or []:
        kept, rejected = verified_citations(item.get("citations"), ctx)
        item["citations"] = kept
        dropped.extend(rejected)
        if kept:
            grounded.append(item)
            citation_count += len(kept)
        else:
            ungrounded.append(item["title"])
    result["items"] = grounded
    if ungrounded:
        pending = result.get("pending_facts")
        if not isinstance(pending, list):
            pending = []
        result["pending_facts"] = pending + ["「%s」缺少可核对的原文出处，待确认" % title for title in ungrounded]
    detail = {"grounded": citation_count, "dropped": dropped, "ungrounded_items": ungrounded}
    if not grounded:
        detail["reason"] = "SOURCE_INSUFFICIENT"
    return {
        "code": "CITATION_GATE",
        "ok": bool(grounded),
        "level": (NOTICE if ungrounded or dropped else OK) if grounded else BLOCK,
        "detail": detail,
    }


# ---------- V7 数字白名单 ----------

def check_numbers(result, ctx):
    allowed = " ".join(
        [str(m.get("excerpt") or "") for m in (ctx.get("materials") or [])]
        + [str(m.get("title") or "") for m in (ctx.get("materials") or [])]
        + [str(ctx.get("goal") or ""), str(ctx.get("conditions") or ""), str(ctx.get("retained") or "")]
    )
    allowed_nums = numbers_in(allowed)
    flagged = []
    for it in result.get("items") or []:
        for n in numbers_in(it.get("detail")):
            if n not in allowed_nums:
                flagged.append({"item": it.get("title"), "number": n})
    return {
        "code": "NUMBER_WHITELIST",
        "ok": not flagged,
        "level": NOTICE if flagged else OK,
        "detail": {"flagged": flagged},
    }


# ---------- V1 时长加总 ----------

def _condition(ctx, key):
    return (ctx.get("conditions") or {}).get(key, ctx.get(key))


def _changed_trip_stop(ctx):
    exclusions = _condition(ctx, "excluded_items") or []
    stop = _condition(ctx, "stop")
    names = _condition(ctx, "stops") or ([stop] if stop else [])
    return any(same_target(str(excluded), str(name)) for excluded in exclusions for name in names)


def _available(ctx):
    task = ctx.get("task_type")
    if task == "travel":
        if _changed_trip_stop(ctx):
            return None
        window = travel_window(_condition(ctx, "start_time"), _condition(ctx, "deadline"), _condition(ctx, "legs"))
        if window is not None:
            return max(0, window)
    alias = {"activity": "total_minutes", "review": "duration_minutes"}.get(task)
    value = _condition(ctx, "available_minutes")
    if value is None and alias:
        value = _condition(ctx, alias)
    return value if type(value) is int and value >= 0 else None


def check_duration(result, ctx, proposed_total=None):
    items = result.get("items") or []
    task = ctx.get("task_type")
    is_travel = task == "travel"
    if is_travel:
        items = rules.stops(items)
    actual = sum(_minutes(i) for i in items)
    total = proposed_total if proposed_total is not None else actual
    if is_travel and not _changed_trip_stop(ctx) and not rules.skip_stop(ctx.get("conditions") or {}, items):
        desired = _condition(ctx, "desired_stay_minutes")
        if type(desired) is int:
            total = desired
    available = _available(ctx)
    requirements = rules.retained_requirements(ctx)
    if "conditions" not in ctx and "retained" not in ctx:
        # 兼容以前直接使用候选 must_keep 的调用。
        requirements.update({i["title"]: _minutes(i) for i in items if i.get("must_keep")})
    required = sum(duration if duration is not None else 1 for duration in requirements.values())
    detail = {"total": total, "scheduled_total": actual}
    if available is None:
        return {"code": "DURATION_TOTAL", "ok": True, "level": OK, "detail": detail}
    detail["available"] = available
    if required > available:
        fixed = [{"title": name, "minutes": duration if duration is not None else 1, "must_keep": True}
                 for name, duration in requirements.items()]
        return {
            "code": "DURATION_TOTAL",
            "ok": False,
            "level": BLOCK,
            "detail": {
                **detail, "required": required, "diff": required - available, "items": fixed,
            },
            "options": _required_options(fixed, required, available),
        }
    if total > available:
        return {
            "code": "DURATION_TOTAL",
            "ok": False,
            "level": NOTICE,
            "detail": {**detail, "diff": total - available},
            "options": _travel_options(items, total, available, ctx) if is_travel else [],
        }
    return {"code": "DURATION_TOTAL", "ok": True, "level": OK, "detail": detail}


_MAX_OPTIONS = 3


def _required_options(items, need, available):
    extra = need - available
    options = [{
        "action": "extend_available",
        "extra_minutes": extra,
        "label": "时间放宽 %d 分钟" % extra,
        "effect": "可用时间 %d → %d 分钟" % (available, need),
        "send": "时间放宽一些，按 %d 分钟安排" % need,
    }]
    for item in sorted(items, key=_minutes, reverse=True):
        title = item["title"]
        current = _minutes(item)
        shorter = max(0, current - extra)
        if shorter > 0:
            options.append({
                "action": "shorten_item", "title": title, "label": "缩短「%s」" % title,
                "effect": "%s %d → %d 分钟" % (title, current, shorter),
                "send": "保留「%s」%d 分钟" % (title, shorter),
            })
        options.append({
            "action": "remove_item", "title": title, "label": "删掉「%s」" % title,
            "effect": "必保留环节共需 %d → %d 分钟" % (need, need - current),
            "send": "把「%s」这个环节删掉" % title,
        })
    return options[:_MAX_OPTIONS]


def _travel_options(items, total, available, ctx):
    extra = total - available
    title = _condition(ctx, "stop") or (items[0]["title"] if len(items) == 1 else None)
    deadline = _parse_hhmm(_condition(ctx, "deadline"))
    options = []
    if available > 0:
        options.append({
            "action": "shorten_stay", "label": "停留 %d 分钟" % available,
            "effect": "停留 %d → %d 分钟，保持原截止时间" % (total, available),
            "send": "停留 %d 分钟" % available,
        })
    if title:
        options.append({
            "action": "remove_item", "title": title, "label": "删掉「%s」" % title,
            "effect": "省去 %d 分钟停留，直返时间以直达路程为准" % total,
            "send": "把「%s」这个环节删掉" % title,
        })
    if deadline:
        later = _fmt(deadline + timedelta(minutes=extra))
        options.append({
            "action": "extend_available", "extra_minutes": extra, "label": "返程截止改到 %s" % later,
            "effect": "截止时间 %s → %s，停留 %d 分钟" % (_fmt(deadline), later, total),
            "send": "最晚 %s 回校" % later,
        })
    return options


# ---------- V3 物料随环节派生 ----------

CARD_KEYS = ("题卡", "猜词卡", "词卡")


def derive_materials(result, ctx):
    people = _condition(ctx, "people") or 0
    items = result.get("items") or []
    titles_norm = " ".join(norm_loose(i.get("title")) for i in items)
    is_activity = str(ctx.get("task_type") or "") == "activity"
    need = dict(_condition(ctx, "fixed_furniture") or {}) if is_activity else {}
    raw = result.get("materials_needed")
    raw = raw if isinstance(raw, list) else []
    mats = [m for m in raw if isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"].strip()]
    if type(people) is int and people > 0 and is_activity and "猜词" in titles_norm:
        need["题卡"] = people
    if type(people) is int and people > 0 and is_activity:
        for material in mats:
            if "名牌" in str(material["name"]):
                need[material["name"]] = people
    corrections = []
    by_norm = {norm_loose(m.get("name")): m for m in mats}

    for name, qty in need.items():
        key = norm_loose(name)
        hit = None
        for k, m in by_norm.items():
            if k and (key in k or k in key or name == "题卡" and any(c in k for c in CARD_KEYS)):
                hit = m
                break
        if hit is None:
            mats.append({"name": name, "qty": qty, "unit_cost": None})
            corrections.append({"code": "MATERIAL_ADDED", "material": name, "to": qty})
        else:
            cur = hit.get("qty")
            if cur != qty:
                corrections.append({"code": "MATERIAL_SYNCED", "material": name, "from": cur, "to": qty})
                hit["qty"] = qty

    if is_activity:
        kept = []
        for m in mats:
            nm = norm_loose(m.get("name"))
            uses_cards = "猜词" in titles_norm or any(
                any(k in i["detail"] for k in CARD_KEYS) for i in items
            )
            item_title = m.get("item_title")
            if (item_title and not any(rules.matches_item(str(item_title), item) for item in items)) or (
                not uses_cards and any(k in nm for k in CARD_KEYS)
            ):
                corrections.append({"code": "MATERIAL_REMOVED", "material": m.get("name")})
                continue
            kept.append(m)
        mats = kept

    result["materials_needed"] = mats
    return {
        "code": "MATERIAL_DERIVE",
        "ok": not corrections,
        "level": NOTICE if corrections else OK,
        "detail": {"people": people, "items": [{"name": m.get("name"), "qty": m.get("qty")} for m in mats]},
    }, corrections


# ---------- V2 费用加总与预算差额 ----------

def _number_value(value):
    if type(value) in (int, float) and isfinite(value) and value >= 0:
        return float(value)
    return None


def _confirmed_price(raw, candidate, ctx, item_citations):
    value = _number_value(raw)
    if value is None:
        return None
    citations, _ = verified_citations(candidate.get("citations") or item_citations, ctx)
    candidate["citations"] = citations
    for citation in citations:
        excerpt = norm_loose(citation["excerpt"])
        free = re.sub(r"(?:不(?:再|是)?|并非|并不|非|取消)免费", "", excerpt)
        if value == 0 and re.search(r"免费|无(?:需)?费", free):
            return value
        quotes = re.findall(
            r"(?:¥|￥)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)(?:元|块)|"
            r"(?:人均|每人|单价|价格|票价|花费|消费)(?:约|为|是)?(\d+(?:\.\d+)?)", excerpt
        )
        if any(float(next(part for part in quote if part)) == value for quote in quotes):
            return value
    return None


def check_budget(result, ctx):
    people = _condition(ctx, "people")
    people = people if type(people) is int and people > 0 else None
    travel = ctx.get("task_type") == "travel"
    items = rules.stops(result["items"]) if travel else result["items"]
    names = []
    cost_items = []
    known_subtotal = 0.0
    known_per_person = 0.0
    material_prices = False

    declared = []
    raw_costs = result.get("costs")
    raw_costs = raw_costs if isinstance(raw_costs, list) else []
    for entry in raw_costs:
        if not isinstance(entry, dict) or not isinstance(entry.get("title"), str) or not entry["title"].strip():
            continue
        title = entry["title"]
        match = next((i for i in items if rules.matches_item(title, i)), None)
        excluded = any(same_target(str(name), title) for name in _condition(ctx, "excluded_items") or [])
        if excluded:
            continue
        price = _confirmed_price(entry.get("per_person"), entry, ctx, (match or {}).get("citations") or [])
        entry["per_person"] = price
        declared.append(entry)
        if price is None:
            names.append(title)
        else:
            known_per_person += price
            cost_items.append({"title": title, "per_person": price})
    if travel or declared:
        result["costs"] = declared

    if not travel or not declared:
        for material in result.get("materials_needed") or []:
            title = material["name"]
            match = next((i for i in items if rules.matches_item(str(title), i)), None)
            price = _confirmed_price(material.get("unit_cost"), material, ctx, (match or {}).get("citations") or [])
            material["unit_cost"] = price
            qty = _number_value(material.get("qty"))
            if price is None or qty is None:
                names.append(title)
                continue
            material_prices = True
            subtotal = price * qty
            known_subtotal += subtotal
            if people:
                cost_items.append({"title": title, "per_person": round(subtotal / people, 2)})
    else:
        # 旧模型也可能写出物料费用字段，统一核实而不重复计入出行开支。
        for material in result.get("materials_needed") or []:
            material["unit_cost"] = _confirmed_price(material.get("unit_cost"), material, ctx, [])

    supplied = _number_value(_condition(ctx, "known_cost_per_person"))
    if travel and (_changed_trip_stop(ctx) or rules.skip_stop(ctx.get("conditions") or {}, result["items"])):
        supplied = None
    if travel and supplied is None:
        for item in items:
            if not any(same_target(item["title"], str(x)) for x in [*names, *(c["title"] for c in cost_items)]):
                names.append(item["title"])
    priced = supplied is not None or bool(cost_items) or material_prices
    if supplied is not None:
        per_person = supplied
        total = round(supplied * people, 2) if people else None
    elif priced:
        per_person = round(known_per_person + known_subtotal / people, 2) if people else (
            None if known_subtotal else round(known_per_person, 2)
        )
        total = round(known_subtotal + known_per_person * people, 2) if people else (
            None if declared else round(known_subtotal, 2)
        )
    else:
        total = per_person = None
    budget = _number_value(_condition(ctx, "budget_per_person"))
    detail = {
        "total": total, "per_person": per_person, "unknown_cost_items": list(dict.fromkeys(names)),
        "cost_known": priced, "cost_items": cost_items,
    }
    if budget is None:
        return {"code": "BUDGET", "ok": True, "level": OK, "detail": detail}
    diff = round(per_person - budget, 2) if per_person is not None else None
    detail.update({"budget_per_person": budget, "diff": diff})
    if diff is not None and diff > 0:
        options = [{
            "action": "raise_budget", "extra": diff, "label": "人均预算放宽 %g 元" % diff,
            "effect": "已知人均花费 %g 元，预算 %g → %g 元" % (per_person, budget, per_person),
            "send": "人均预算改为 %g 元" % per_person,
        }]
        for cost in sorted(cost_items, key=lambda item: item["per_person"], reverse=True):
            title, value = cost["title"], cost["per_person"]
            if not value or value > per_person:
                continue
            options.append({
                "action": "remove_cost_item", "title": title, "label": "删掉「%s」" % title,
                "effect": "已知人均费用 %g → %g 元" % (per_person, per_person - value),
                "send": "把「%s」这个环节删掉" % title,
            })
        if travel and len(options) == 1 and items:
            title = next((name for name in names if any(rules.matches_item(str(name), i) for i in items)),
                         items[0]["title"])
            options.append({
                "action": "remove_cost_item", "title": title, "label": "删掉「%s」" % title,
                "effect": "去掉该站后仍需核实剩余花费，原方案每人至少要省 %g 元" % diff,
                "send": "把「%s」这个环节删掉" % title,
            })
        return {
            "code": "BUDGET",
            "ok": False,
            "level": NOTICE,
            "detail": detail,
            "options": options[:_MAX_OPTIONS],
        }
    return {"code": "BUDGET", "ok": True, "level": OK, "detail": detail}


# ---------- V4 时间线到离推算 ----------

def compute_timeline(ctx, items, requested=None):
    start = _parse_hhmm(_condition(ctx, "start_time"))
    deadline = _parse_hhmm(_condition(ctx, "deadline"))
    conditions = ctx.get("conditions") or {}
    direct = rules.skip_stop(conditions, items)
    active_stops = rules.stops(items)
    inside = sum(_minutes(i) for i in active_stops) if not direct else 0
    raw_legs = _condition(ctx, "legs")
    legs = list(raw_legs[:2]) if isinstance(raw_legs, (list, tuple)) and not _changed_trip_stop(ctx) else []
    legs.extend([None] * (2 - len(legs)))
    arrive = leave = end = None
    unknown = []
    latest_leave = requested_end = overrun = None
    if start is None:
        unknown.append("出发时间")
    if direct:
        leg = _condition(ctx, "direct_return_minutes")
        if type(leg) is int and leg >= 0:
            end = start + timedelta(minutes=leg) if start is not None else None
        else:
            unknown.append("直返路程")
        legs = [leg]
    else:
        if type(legs[0]) is int and legs[0] >= 0:
            arrive = start + timedelta(minutes=legs[0]) if start is not None else None
            leave = arrive + timedelta(minutes=inside) if arrive is not None else None
        else:
            unknown.append("去往停留点的路程")
        if type(legs[1]) is int and legs[1] >= 0:
            end = leave + timedelta(minutes=legs[1]) if leave is not None else None
            latest_leave = deadline - timedelta(minutes=legs[1]) if deadline is not None else None
            if arrive is not None and deadline is not None and requested is not None:
                requested_end = arrive + timedelta(minutes=requested + legs[1])
                overrun = max(0, int((requested_end - deadline).total_seconds() // 60))
        else:
            unknown.append("返回路程")
    stop = None if direct else _condition(ctx, "stop")
    if _changed_trip_stop(ctx) and len(active_stops) == 1:
        stop = active_stops[0]["title"]
    return {
        "computed": end is not None, "start": _fmt(start), "arrive": _fmt(arrive),
        "leave": _fmt(leave), "end": _fmt(end), "deadline": _fmt(deadline), "latest_leave": _fmt(latest_leave),
        "origin": _condition(ctx, "origin"), "stop": stop,
        "destination": _condition(ctx, "destination"), "legs": legs, "inside_minutes": inside,
        "requested_inside_minutes": requested if not direct else None,
        "requested_end": _fmt(requested_end), "overrun_minutes": overrun,
        "trimmed": requested is not None and inside < requested and not direct,
        "unknown": unknown,
    }


def check_timeline(result, ctx, requested=None):
    if ctx.get("task_type") != "travel":
        result.pop("timeline", None)
        return {"code": "TIMELINE", "ok": True, "level": OK, "detail": {"computed": False}}, []
    computed = compute_timeline(ctx, result.get("items") or [], requested)
    declared = result.get("timeline") or {}
    corrections = [
        {"code": "TIMELINE_RECOMPUTED", "field": field, "from": declared[field], "to": computed[field]}
        for field in ("start", "arrive", "leave", "end")
        if isinstance(declared, dict) and field in declared and declared[field] != computed[field]
    ]
    result["timeline"] = computed
    return {
        "code": "TIMELINE",
        "ok": True,
        "level": NOTICE if corrections or computed["unknown"] else OK,
        "detail": computed,
    }, corrections


# ---------- V8 保留项覆盖 ----------

def check_retained(result, ctx):
    """BE-429 用户明确要求保留的内容，必须真的出现在结果里。"""
    requirements = rules.retained_requirements(ctx)
    items = result.get("items") or []
    covered = [name for name, duration in requirements.items() if any(
        (rules.matches_item(name, item) or duration is None and norm_match(name) in norm_match(item["detail"]))
        and (duration is None or _minutes(item) == duration) for item in items
    )]
    missing = [name for name in requirements if name not in covered]
    return {"code": "RETAINED_COVERED", "ok": not missing, "level": BLOCK if missing else OK,
            "detail": {"required": list(requirements), "covered": covered, "missing": missing}}


# ---------- 汇总 ----------

def _format_duration_block(duration):
    return {
        "code": "CONFLICT_REQUIREMENTS",
        "message": "必保留环节需要 %d 分钟，可安排 %d 分钟，超出 %d 分钟" % (
            duration["detail"]["required"], duration["detail"]["available"], duration["detail"]["diff"]),
        "detail": duration["detail"],
        "options": duration.get("options") or [],
    }


def request_conflict(ctx):
    """仅凭用户已经提供的条件可确定冲突时，生成前就能给出取舍。"""
    duration = check_duration({"items": []}, ctx)
    if duration["level"] == BLOCK:
        return _format_duration_block(duration)
    if ctx.get("task_type") != "travel":
        return None
    conditions = ctx.get("conditions") or {}
    legs = _condition(ctx, "legs")
    start, deadline = _condition(ctx, "start_time"), _condition(ctx, "deadline")
    if rules.skip_stop(conditions):
        direct = conditions.get("direct_return_minutes")
        legs = [direct, 0] if type(direct) is int else []
    elif _changed_trip_stop(ctx):
        return None
    window = travel_window(start, deadline, legs)
    if window is None or window >= 0:
        return None
    available = sum(legs) + window
    revised_deadline = _fmt(_parse_hhmm(deadline) + timedelta(minutes=-window))
    revised_start = _fmt(_parse_hhmm(start) + timedelta(minutes=window))
    return {
        "code": "CONFLICT_REQUIREMENTS",
        "message": "仅路程就需要 %d 分钟，可用 %d 分钟，超出 %d 分钟" % (sum(legs), available, -window),
        "detail": {"required": sum(legs), "available": available, "diff": -window},
        "options": [
            {"action": "extend_available", "extra_minutes": -window, "label": "截止改为 %s" % revised_deadline,
             "effect": "留出 %d 分钟路程" % sum(legs), "send": "最晚 %s 回校" % revised_deadline},
            {"action": "start_earlier", "label": "%s 出发" % revised_start,
             "effect": "提前 %d 分钟出发，留足路程时间" % -window, "send": "%s 出发" % revised_start},
        ],
    }


def _fit_items(result, ctx, required):
    task_type = ctx["task_type"]
    items = result["items"]
    available = _available(ctx)
    if task_type == "travel":
        if rules.skip_stop(ctx.get("conditions") or {}, items):
            return []
        stops = rules.stops(items)
        desired = None if _changed_trip_stop(ctx) else _condition(ctx, "desired_stay_minutes")
        target = desired if type(desired) is int else sum(_minutes(i) for i in stops)
        if available is not None:
            target = min(target, available)
        if len(stops) == 1:
            item = stops[0]
            return [rules.set_minutes(item, target)] if target != _minutes(item) else []
        if not stops or target == 0:
            return [rules.set_minutes(i, 0) for i in stops if _minutes(i)]
        keep = {id(i) for i in stops if any(rules.matches_item(name, i) for name in required)}
        retained, corrections = rules.minutes_for_window(stops, target, keep)
        removed_ids = {id(i) for i in stops} - {id(i) for i in retained}
        result["items"] = [i for i in items if id(i) not in removed_ids]
        return corrections
    if available is None:
        return []
    fitter = rules.fit_review if task_type == "review" else rules.fit_activity
    result["items"], corrections = fitter(items, available, required)
    return corrections


def run_all(result, ctx):
    checks = []
    corrections = []
    notices = []

    st = check_structure(result, ctx)
    checks.append(st)
    if not st["ok"]:
        return _final(checks, corrections, notices, block={"code": "STRUCTURE_INVALID", "detail": st["detail"]})

    corrections.extend(rules.remove_excluded(result, ctx))
    conflict = request_conflict(ctx)
    if conflict:
        return _final(checks, corrections, notices, block=conflict)
    if not result["items"]:
        return _final(checks, corrections, notices, block={
            "code": "NO_ELIGIBLE_ITEMS",
            "message": "所选材料中的候选环节受到场地或排除条件限制，请补充可用材料或调整条件",
            "detail": {"removed": corrections},
        })

    cit = check_citations(result, ctx)
    checks.append(cit)
    if cit["level"] == BLOCK:
        return _final(checks, corrections, notices, block={
            "code": "SOURCE_INSUFFICIENT",
            "message": "候选环节缺少可核对的原文，请补充材料或重试",
            "detail": cit["detail"],
        })

    nums = check_numbers(result, ctx)
    checks.append(nums)

    required = rules.retained_requirements(ctx)
    corrections.extend(rules.apply_retained(result["items"], required))
    dur = check_duration(result, ctx)
    if dur["level"] == BLOCK:
        checks.append(dur)
        return _final(checks, corrections, notices, block=_format_duration_block(dur))
    proposed = dur["detail"]["total"]
    corrections.extend(_fit_items(result, ctx, required))
    dur = check_duration(result, ctx, proposed)
    checks.append(dur)
    available = dur["detail"].get("available")
    if not result["items"] or (available is not None and dur["detail"]["scheduled_total"] > available):
        return _final(checks, corrections, notices, block={
            "code": "PLAN_DURATION_INVALID",
            "message": "候选方案无法安排在可用时间内，请调整时长或保留项",
            "detail": dur["detail"],
            "options": dur.get("options") or [],
        })
    retained_check = check_retained(result, ctx)
    checks.append(retained_check)
    if not retained_check["ok"]:
        return _final(checks, corrections, notices, block={
            "code": "RETAINED_ITEM_MISSING",
            "message": "方案缺少要求保留的环节：%s" % "、".join(retained_check["detail"]["missing"]),
            "detail": retained_check["detail"],
        })

    mat_check, mat_fix = derive_materials(result, ctx)
    checks.append(mat_check)
    corrections.extend(mat_fix)

    bud = check_budget(result, ctx)
    checks.append(bud)

    tl_check, tl_fix = check_timeline(result, ctx, proposed)
    checks.append(tl_check)
    corrections.extend(tl_fix)

    for c in checks:
        if c["level"] == NOTICE and c["code"] != "NUMBER_WHITELIST":
            notices.append({"code": c["code"], "detail": c["detail"], "options": c.get("options") or []})
    pending = result.get("pending_facts")
    pending = [x for x in pending if isinstance(x, str) and x.strip()] if isinstance(pending, list) else []
    if nums["detail"]["flagged"]:
        notices.append({"code": "NUMBER_UNVERIFIED", "detail": nums["detail"]})
        for f in nums["detail"]["flagged"]:
            pending.append("%s：%s 无材料出处，待确认" % (f["item"], f["number"]))
            for item in result["items"]:
                if item["title"] == f["item"]:
                    item["detail"] = re.sub(
                        r"(?<![\d.])" + re.escape(f["number"]) + r"(?![\d.])", "（待确认）", item["detail"],
                    )
    for name in bud["detail"]["unknown_cost_items"]:
        pending.append("%s价格待确认" % name)
    for name in tl_check["detail"].get("unknown") or []:
        pending.append("%s待确认" % name)
    result["pending_facts"] = list(dict.fromkeys(pending))

    return _final(checks, corrections, notices, block=None)


def _final(checks, corrections, notices, block):
    passed = block is None and all(c["ok"] or c["level"] == NOTICE for c in checks)
    return {
        "passed": passed and block is None,
        "checks": checks,
        "corrections": corrections,
        "notices": notices,
        "block": block,
    }
