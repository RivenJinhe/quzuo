"""材料检索（BE-230/231）：只召回本人材料，按目标/条件做确定性匹配，不调用模型。"""
from __future__ import annotations

import re
from typing import Iterable

from app.services.validation import norm_loose

_CJK = re.compile(r"[\u4e00-\u9fff]")
_EN = re.compile(r"[a-zA-Z0-9_]+")

STOP = {"的", "了", "我", "想", "要", "就", "把", "是", "在", "和", "与", "然后", "一下", "东西", "这个", "那个"}


def tokens(text) -> set:
    s = norm_loose(text)
    out = set()
    for w in _EN.findall(s):
        if len(w) > 1:
            out.add(w)
    chars = [c for c in s if _CJK.match(c)]
    for i in range(len(chars) - 1):
        bg = chars[i] + chars[i + 1]
        if bg not in STOP and bg[0] not in STOP and bg[1] not in STOP:
            out.add(bg)
    return out


def _material_tokens(m: dict) -> set:
    return tokens(" ".join(str(m.get(k) or "") for k in ("title", "topic", "excerpt", "note")))


def _usable(m: dict) -> bool:
    return (m.get("status") or "citable") == "citable"


_SENT = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]?")


def _quote(text, hits: set, limit: int = 40) -> str:
    """从一段文字里挑出含命中词的一句，原样引用；没有就返回空串。"""
    if not text:
        return ""
    best = ""
    for sent in _SENT.findall(str(text)):
        s = sent.strip()
        if not s:
            continue
        norm = norm_loose(s)
        if any(h in norm for h in hits):
            if not best or len(s) < len(best):
                best = s
    if not best:
        return ""
    if len(best) > limit:
        return best[:limit].rstrip() + "…"
    return best


def _reason(m: dict, hit: set, topic_hit: set) -> str:
    """给出人能看懂的一行命中理由：优先引用户自己写的备注原文。"""
    if not hit and not topic_hit:
        return ""
    note = _quote(m.get("note"), hit)
    if note:
        return "你写过：「%s」" % note
    title = _quote(m.get("title"), hit)
    if title:
        return "标题里有「%s」" % title
    if topic_hit:
        return "主题「%s」对得上" % (m.get("topic") or "")
    return "正文提到「%s」" % "、".join(sorted(hit)[:4])


def score(m: dict, query: set, topic_hint: set) -> tuple:
    mt = _material_tokens(m)
    hit = query & mt
    s = len(hit) * 2.0
    th = topic_hint & mt if topic_hint else set()
    if topic_hint:
        s += len(th) * 3.0
    if _usable(m):
        s += 1.0
    else:
        s -= 5.0
    return s, _reason(m, hit, th)


def retrieve(materials: Iterable[dict], goal: str, conditions=None, limit: int = 10) -> list:
    q = tokens(goal or "")
    q |= tokens(_conditions_text(conditions))
    # 只有目标/条件里点到的主题才加分；否则每份材料都会命中自己的主题，候选必然全选。
    goal_text = norm_loose(" ".join([goal or "", _conditions_text(conditions)]))
    topic_hint = set()
    for m in materials:
        t = norm_loose(m.get("topic") or "")
        if t and t in goal_text:
            topic_hint.add(t)
    ranked = []
    for m in materials:
        s, reason = score(m, q, topic_hint)
        if s <= 0 or not reason:
            continue
        ranked.append({
            "material_id": str(m.get("id")),
            "title": m.get("title"),
            "topic": m.get("topic"),
            "status": m.get("status") or "usable",
            "usable": _usable(m),
            "score": round(s, 2),
            "reason": reason,
        })
    ranked.sort(key=lambda x: -x["score"])
    return ranked[:limit]


def _conditions_text(conditions) -> str:
    if not conditions:
        return ""
    if isinstance(conditions, dict):
        parts = []
        for k, v in conditions.items():
            if isinstance(v, (list, dict)):
                parts.append(str(v))
            else:
                parts.append("%s%s" % (k, v))
        return " ".join(parts)
    return str(conditions)
