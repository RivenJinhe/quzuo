"""程序侧复核（BE-420~427）。

纯函数：输入模型输出的结果 dict 与本次请求上下文 ctx，返回 checks / corrections / block。
level: "ok" | "notice" | "block"。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

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
        return datetime(2000, 1, 1, int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return None


def _fmt(dt):
    return dt.strftime("%H:%M") if dt else None


def _minutes(item) -> int:
    v = item.get("minutes")
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


# ---------- V6 结构完整性 ----------

_REQUIRED = ("task_type", "headline", "items")


def check_structure(result, ctx):
    missing = [k for k in _REQUIRED if not result.get(k)]
    bad_items = []
    if result.get("items"):
        for idx, it in enumerate(result["items"]):
            if not it.get("title"):
                bad_items.append({"index": idx, "reason": "MISSING_TITLE"})
            if not it.get("detail"):
                bad_items.append({"index": idx, "reason": "MISSING_DETAIL"})
    ok = not missing and not bad_items
    return {
        "code": "STRUCTURE",
        "ok": ok,
        "level": OK if ok else BLOCK,
        "detail": {"missing": missing, "bad_items": bad_items},
    }


# ---------- V5 引用闸门 ----------

def check_citations(result, ctx):
    materials = {str(m.get("id")): m for m in (ctx.get("materials") or [])}
    dropped = []
    grounded = 0
    for it in result.get("items") or []:
        kept = []
        for c in it.get("citations") or []:
            mid = str(c.get("material_id") or "")
            m = materials.get(mid)
            if m is None:
                dropped.append({"material_id": mid, "reason": "MATERIAL_NOT_IN_REQUEST"})
                continue
            excerpt = norm_match(c.get("excerpt"))
            if not excerpt or excerpt not in norm_match(m.get("excerpt")):
                dropped.append({"material_id": mid, "reason": "EXCERPT_NOT_IN_MATERIAL"})
                continue
            ps = c.get("page")
            if ps is not None:
                lo = m.get("source_page_start")
                hi = m.get("source_page_end") or lo
                try:
                    if lo is not None and not (int(lo) <= int(ps) <= int(hi)):
                        dropped.append({"material_id": mid, "reason": "PAGE_OUT_OF_RANGE"})
                        continue
                except (TypeError, ValueError):
                    pass
            kept.append(c)
        it["citations"] = kept
        it["_ungrounded"] = not kept
        grounded += len(kept)

    if grounded == 0:
        return {
            "code": "CITATION_GATE",
            "ok": False,
            "level": BLOCK,
            "detail": {"grounded": 0, "dropped": dropped, "reason": "SOURCE_INSUFFICIENT"},
        }
    ungrounded = [it.get("title") for it in (result.get("items") or []) if it.get("_ungrounded")]
    return {
        "code": "CITATION_GATE",
        "ok": True,
        "level": NOTICE if ungrounded else OK,
        "detail": {"grounded": grounded, "dropped": dropped, "ungrounded_items": ungrounded},
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

def check_duration(result, ctx):
    items = result.get("items") or []
    total = sum(_minutes(i) for i in items)
    available = ctx.get("available_minutes")
    required = sum(_minutes(i) for i in items if i.get("must_keep"))
    if available is None:
        return {"code": "DURATION_TOTAL", "ok": True, "level": OK, "detail": {"total": total}}
    if required > available:
        return {
            "code": "DURATION_TOTAL",
            "ok": False,
            "level": BLOCK,
            "detail": {
                "required": required,
                "available": int(available),
                "diff": required - int(available),
                "items": [
                    {"title": i.get("title"), "minutes": _minutes(i), "must_keep": bool(i.get("must_keep"))}
                    for i in items
                ],
            },
            "options": _relax_options(items, required, int(available)),
        }
    if total > available:
        return {
            "code": "DURATION_TOTAL",
            "ok": False,
            "level": NOTICE,
            "detail": {"total": total, "available": int(available), "diff": total - int(available)},
            "options": _relax_options(items, total, int(available)),
        }
    return {"code": "DURATION_TOTAL", "ok": True, "level": OK, "detail": {"total": total, "available": int(available)}}


def _relax_options(items, need, available):
    opts = []
    for i in items:
        if i.get("must_keep"):
            continue
        opts.append({
            "action": "remove_item",
            "title": i.get("title"),
            "save_minutes": _minutes(i),
            "label": "删掉「%s」，省 %d 分钟" % (i.get("title"), _minutes(i)),
        })
    opts.append({
        "action": "extend_available",
        "extra_minutes": need - available,
        "label": "把总时长放宽 %d 分钟" % (need - available),
    })
    return opts


# ---------- V3 物料随环节派生 ----------

CARD_KEYS = ("题卡", "卡片", "词卡")


def derive_materials(result, ctx):
    people = int(ctx.get("people") or 0)
    items = result.get("items") or []
    titles_norm = " ".join(norm_loose(i.get("title")) for i in items)
    is_activity = str(ctx.get("task_type") or "") == "activity"
    need = {}
    if people and is_activity and "猜词" in titles_norm:
        need["题卡"] = people
    if people and is_activity:
        need["名牌"] = people
    for name, qty in (ctx.get("fixed_furniture") or {}).items():
        need[name] = int(qty)

    mats = result.get("materials_needed")
    if not isinstance(mats, list):
        mats = []
    corrections = []
    by_norm = {norm_loose(m.get("name")): m for m in mats}

    for name, qty in need.items():
        key = norm_loose(name)
        hit = None
        for k, m in by_norm.items():
            if key in k or k in key:
                hit = m
                break
        if hit is None:
            mats.append({"name": name, "qty": qty, "unit_cost": hit.get("unit_cost") if hit else None})
            corrections.append({"code": "MATERIAL_ADDED", "material": name, "to": qty})
        else:
            cur = hit.get("qty")
            if cur != qty:
                corrections.append({"code": "MATERIAL_SYNCED", "material": name, "from": cur, "to": qty})
                hit["qty"] = qty

    if "猜词" not in titles_norm:
        kept = []
        for m in mats:
            nm = norm_loose(m.get("name"))
            if any(k in nm for k in CARD_KEYS):
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

def check_budget(result, ctx):
    people = int(ctx.get("people") or 1) or 1
    total = 0.0
    unknown = []
    for m in result.get("materials_needed") or []:
        uc = m.get("unit_cost")
        qty = m.get("qty")
        if uc is None or qty is None:
            if uc is None:
                unknown.append(m.get("name"))
            continue
        total += float(uc) * float(qty)
    per_person = round(total / people, 2)
    budget = ctx.get("budget_per_person")
    detail = {"total": round(total, 2), "per_person": per_person, "unknown_cost_items": unknown}
    if budget is None:
        return {"code": "BUDGET", "ok": True, "level": OK, "detail": detail}
    budget = float(budget)
    diff = round(per_person - budget, 2)
    detail.update({"budget_per_person": budget, "diff": diff})
    if diff > 0:
        return {
            "code": "BUDGET",
            "ok": False,
            "level": NOTICE,
            "detail": detail,
            "options": [
                {"action": "raise_budget", "extra": diff, "label": "预算放宽 %s 元" % diff},
                {"action": "remove_cost_item", "label": "去掉收费环节（人均 %s 元）" % per_person},
            ],
        }
    return {"code": "BUDGET", "ok": True, "level": OK, "detail": detail}


# ---------- V4 时间线到离推算 ----------

def compute_timeline(ctx, items):
    start = _parse_hhmm(ctx.get("start_time"))
    legs = [int(x) for x in (ctx.get("legs") or [])]
    total = sum(_minutes(i) for i in items)
    available = ctx.get("available_minutes")
    inside = total
    trimmed = False
    if available is not None and total > int(available):
        inside = int(available)
        trimmed = True
    if start is None:
        return {"computed": False, "inside_minutes": inside, "trimmed": trimmed}
    t = start
    if legs:
        t = t + timedelta(minutes=legs[0])
    arrive = t
    leave = arrive + timedelta(minutes=inside)
    back = leave
    if len(legs) > 1:
        back = leave + timedelta(minutes=legs[1])
    return {
        "computed": True,
        "start": _fmt(start),
        "arrive": _fmt(arrive),
        "leave": _fmt(leave),
        "end": _fmt(back),
        "inside_minutes": inside,
        "trimmed": trimmed,
    }


def check_timeline(result, ctx):
    computed = compute_timeline(ctx, result.get("items") or [])
    if not computed.get("computed"):
        return {"code": "TIMELINE", "ok": True, "level": OK, "detail": {"computed": False}}, []
    declared = result.get("timeline") or {}
    corrections = []
    if declared.get("end") and declared.get("end") != computed["end"]:
        corrections.append({"code": "TIMELINE_RECOMPUTED", "field": "end", "from": declared.get("end"), "to": computed["end"]})
    result["timeline"] = {**declared, **{k: v for k, v in computed.items() if k != "computed"}}
    return {
        "code": "TIMELINE",
        "ok": True,
        "level": NOTICE if corrections else OK,
        "detail": {k: computed.get(k) for k in ("start", "arrive", "leave", "end", "inside_minutes", "trimmed")},
    }, corrections


# ---------- V8 保留项覆盖 ----------

def check_retained(result, ctx):
    """BE-429 用户明确要求保留的内容，必须真的出现在结果里。"""
    need = [str(x).strip() for x in (ctx.get("retained_texts") or []) if str(x).strip()]
    if not need:
        return {"code": "RETAINED_COVERED", "ok": True, "level": OK,
                "detail": {"required": [], "covered": [], "missing": []}}
    parts = []
    for it in (result.get("items") or []):
        parts.append(str(it.get("title") or "") + " " + str(it.get("detail") or ""))
    blob = norm_match(" ".join(parts))
    covered, missing = [], []
    for t in need:
        key = norm_match(t)
        if key and key in blob:
            covered.append(t)
        else:
            missing.append(t)
    if missing:
        return {"code": "RETAINED_COVERED", "ok": True, "level": NOTICE,
                "detail": {"required": need, "covered": covered, "missing": missing},
                "options": [{"action": "add_item", "title": t} for t in missing]}
    return {"code": "RETAINED_COVERED", "ok": True, "level": OK,
            "detail": {"required": need, "covered": covered, "missing": []}}


# ---------- 汇总 ----------

def run_all(result, ctx):
    checks = []
    corrections = []
    notices = []
    block = None

    st = check_structure(result, ctx)
    checks.append(st)
    if not st["ok"]:
        return _final(checks, corrections, notices, block={"code": "STRUCTURE_INVALID", "detail": st["detail"]})

    cit = check_citations(result, ctx)
    checks.append(cit)
    if cit["level"] == BLOCK:
        return _final(checks, corrections, notices, block={"code": "SOURCE_INSUFFICIENT", "detail": cit["detail"]})

    nums = check_numbers(result, ctx)
    checks.append(nums)

    dur = check_duration(result, ctx)
    checks.append(dur)
    if dur["level"] == BLOCK:
        return _final(checks, corrections, notices, block={
            "code": "CONFLICT_REQUIREMENTS",
            "message": "必保留环节需要 %d 分钟，可安排 %d 分钟，超出 %d 分钟" % (
                dur["detail"]["required"], dur["detail"]["available"], dur["detail"]["diff"]),
            "detail": dur["detail"],
            "options": dur.get("options") or [],
        })

    checks.append(check_retained(result, ctx))

    mat_check, mat_fix = derive_materials(result, ctx)
    checks.append(mat_check)
    corrections.extend(mat_fix)

    bud = check_budget(result, ctx)
    checks.append(bud)

    tl_check, tl_fix = check_timeline(result, ctx)
    checks.append(tl_check)
    corrections.extend(tl_fix)

    for c in checks:
        if c["level"] == NOTICE and c["code"] != "NUMBER_WHITELIST":
            notices.append({"code": c["code"], "detail": c["detail"], "options": c.get("options") or []})
    if "flagged" in nums["detail"] and nums["detail"]["flagged"]:
        notices.append({"code": "NUMBER_UNVERIFIED", "detail": nums["detail"]})
        result.setdefault("pending_facts", [])
        for f in nums["detail"]["flagged"]:
            result["pending_facts"].append("%s：%s 无材料出处，标待确认" % (f["item"], f["number"]))
    if "unknown_cost_items" in bud["detail"] and bud["detail"]["unknown_cost_items"]:
        result.setdefault("pending_facts", [])
        for nm in bud["detail"]["unknown_cost_items"]:
            result["pending_facts"].append("%s 价格未知，标待确认" % nm)

    return _final(checks, corrections, notices, block=block)


def _final(checks, corrections, notices, block):
    passed = block is None and all(c["ok"] or c["level"] == NOTICE for c in checks)
    return {
        "passed": passed and block is None,
        "checks": checks,
        "corrections": corrections,
        "notices": notices,
        "block": block,
    }
