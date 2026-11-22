"""自然语言追问（BE-238）：把用户一句话翻译成条件/保留项增量。

确定性优先：能算成数字的进 conditions（后续照常走程序复核）；算不出的原句进
conditions.user_notes（随条件一起送给模型，但不改变复核用的数字）。
"""
from __future__ import annotations

import json
import re

_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM = r"\d{1,4}|[一二两三四五六七八九十]{1,3}"
_MIN_UNIT = r"(?:分钟|分|min|mins)"
_DUR_HINT = ("只剩", "只留", "就剩", "缩到", "缩成", "改成", "改为", "调整到", "调整为",
             "减到", "减少到", "只有", "仅有", "控制在", "限制在", "最多", "总共", "一共")
_DEL_WORDS = ("删掉", "删去", "删除", "去掉", "不要", "取消", "砍掉", "减掉", "拿掉")
_KEEP_WORDS = ("务必保留", "优先保留", "一定要", "必须", "务必", "保留")
_TIME_START = ("出发", "离馆", "离开", "开始", "出门", "到场", "到店", "集合")
_TIME_END = ("返回", "回校", "返校", "结束", "前回", "得回")
_NOISE = ("分钟", "小时", "元", "预算", "人", "页")


def _cn2int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if s in _CN:
        return _CN[s]
    m = re.fullmatch(r"([一二两三四五六七八九])?十([一二三四五六七八九])?", s)
    if m:
        tens = _CN.get(m.group(1) or "一", 1)
        ones = _CN.get(m.group(2), 0) if m.group(2) else 0
        return tens * 10 + ones
    return None


def _show(v):
    return "未设" if v in (None, "") else str(v)


def find_minutes(text: str) -> int | None:
    """取最后一个 N 分钟——15 分钟缩到 5 分钟应得 5。"""
    best = None
    for m in re.finditer(r"(?P<n>" + _NUM + r")\s*" + _MIN_UNIT, text):
        n = _cn2int(m.group("n"))
        if n and 0 < n <= 600:
            best = n
    return best


def find_money(text: str):
    """返回 (金额, 口径)，口径为 总预算 或 人均。"""
    pat = r"(?P<k>总预算|总费用|预算|人均|每人)\s*(?:是|为|只有|就|按|控制在|提高到|降到|改成|降为|升为|定在)?\s*(?P<n>\d{1,6}(?:\.\d{1,2})?)"
    m = re.search(pat, text)
    if not m:
        return None, None
    kind = m.group("k")
    val = float(m.group("n"))
    return (val, "总预算" if kind in ("总预算", "总费用") else "人均")


def find_people(text: str) -> int | None:
    m = re.search(r"(?P<n>\d{1,4})\s*个?\s*人", text)
    if not m:
        return None
    n = int(m.group("n"))
    return n if 0 < n <= 2000 else None


def find_times(text: str) -> list[str]:
    out = []
    for h, mi in re.findall(r"(\d{1,2})[:：](\d{2})", text):
        h, mi = int(h), int(mi)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            out.append("%02d:%02d" % (h, mi))
    return out


_LEAD = ("把", "将", "请", "帮", "给我", "然后", "再", "顺便", "麻烦", "希望", "我想", "想要")


def _clean(cand: str) -> str:
    cand = cand.strip(" \u300c\u300d\u0022\u0027")
    changed = True
    while changed and cand:
        changed = False
        for p in _LEAD:
            if cand.startswith(p) and len(cand) > len(p):
                cand = cand[len(p):]
                changed = True
    return cand.strip()


def _words_after(text: str, words) -> list[str]:
    """抓 删掉 X / X 删掉 两种语序里的 X，丢掉单字与带数字的噪音。"""
    out = []
    tail = r"\s*[\u300c\u0022\u0027]?([^\s，。；、！？,.;!?]{1,14})"
    head = r"([^\s，。；、！？,.;!?]{1,14})\s*[\u300c\u0022\u0027]?"
    for w in words:
        for m in re.finditer(re.escape(w) + tail, text):
            _push(out, _clean(m.group(1)))
        for m in re.finditer(head + re.escape(w), text):
            _push(out, _clean(m.group(1)))
    return out


def _push(out: list, cand: str) -> None:
    if len(cand) < 2:
        return
    if any(x in cand for x in _NOISE):
        return
    if cand not in out:
        out.append(cand)


def _match_item(items, word):
    best = None
    for it in items or []:
        title = str(it.get("title") or "")
        if not title:
            continue
        if word in title or title in word:
            if best is None or len(title) > len(str(best.get("title") or "")):
                best = it
    return best


def parse(text: str, task_type: str, conditions: dict, retained: dict) -> dict:
    """输入一句话与当前条件，返回新的 conditions/retained 与可读的变更列表。"""
    text = (text or "").strip()
    cond = json.loads(json.dumps(conditions or {}, ensure_ascii=False))
    ret = json.loads(json.dumps(retained or {}, ensure_ascii=False))
    applied: list[dict] = []
    unresolved: list[str] = []

    dur_hint = any(h in text for h in _DUR_HINT) or (
        ("时间" in text or "时长" in text) and "分钟" in text
    )
    if not dur_hint and "分钟" in text:
        # 「还是 15 分钟吧」这种没触发词、又不长的一句话，按改时长处理
        nums = re.findall(_NUM, text)
        if len(nums) == 1 and len(text) <= 30:
            dur_hint = True
    if dur_hint:
        minutes = find_minutes(text)
        if minutes:
            field = {"review": "duration_minutes", "activity": "total_minutes"}.get(task_type)
            old = cond.get(field) if field else cond.get("available_minutes")
            if field:
                cond[field] = minutes
            cond["available_minutes"] = minutes
            applied.append({"field": field or "available_minutes", "from": old, "to": minutes,
                            "label": "可安排时长 %s → %s 分钟" % (_show(old), minutes)})
        else:
            unresolved.append("听出是在改时间，但没识别出分钟数")

    money, kind = find_money(text)
    if money is not None:
        old = cond.get("budget_per_person")
        per = money
        if kind == "总预算":
            people = find_people(text) or cond.get("people")
            if people:
                per = round(money / people, 2)
            else:
                unresolved.append("给了总预算但没说人数，先按人均 %s 元算" % money)
        cond["budget_per_person"] = per
        applied.append({"field": "budget_per_person", "from": old, "to": per,
                        "label": "人均预算 %s → %s 元" % (_show(old), per)})

    people_n = find_people(text)
    if people_n:
        old = cond.get("people")
        cond["people"] = people_n
        applied.append({"field": "people", "from": old, "to": people_n,
                        "label": "人数 %s → %s" % (_show(old), people_n)})

    times = find_times(text)
    if times:
        if any(h in text for h in _TIME_START) and not cond.get("start_time"):
            cond["start_time"] = times[0]
            applied.append({"field": "start_time", "from": None, "to": times[0],
                            "label": "开始时间设为 %s" % times[0]})
        elif any(h in text for h in _TIME_END):
            cond["deadline"] = times[-1]
            applied.append({"field": "deadline", "from": None, "to": times[-1],
                            "label": "结束时间设为 %s" % times[-1]})

    items = cond.get("items")
    if isinstance(items, list) and items:
        for w in _words_after(text, _DEL_WORDS):
            it = _match_item(items, w)
            if it is None:
                unresolved.append("没找到叫 %s 的环节，没动" % w)
                continue
            was_keep = bool(it.get("must_keep"))
            items.remove(it)
            applied.append({"field": "items", "from": it.get("title"), "to": None,
                            "label": "删掉环节 %s%s" % (it.get("title"), "（原为必须保留项）" if was_keep else "")})

    keep_texts = list(ret.get("must_keep_texts") or [])
    for w in _words_after(text, _KEEP_WORDS):
        it = _match_item(cond.get("items"), w)
        if it is not None:
            if not it.get("must_keep"):
                it["must_keep"] = True
                applied.append({"field": "items", "from": None, "to": it.get("title"),
                                "label": "%s 标为必须保留" % it.get("title")})
        elif w not in keep_texts:
            keep_texts.append(w)
            applied.append({"field": "retained", "from": None, "to": w,
                            "label": "保留要求：%s" % w})
    if keep_texts:
        ret["must_keep_texts"] = keep_texts[-20:]

    notes = list(cond.get("user_notes") or [])
    if text and text not in notes:
        notes.append(text[:300])
    cond["user_notes"] = notes[-10:]

    summary = "；".join(a["label"] for a in applied)
    if not summary:
        summary = "没识别出具体改动，已按原话记进补充说明"
    return {
        "conditions": cond,
        "retained": ret,
        "applied": applied,
        "unresolved": unresolved,
        "summary": summary,
    }
