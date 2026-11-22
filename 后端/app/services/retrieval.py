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


def score(m: dict, query: set, topic_hint: set) -> tuple:
    mt = _material_tokens(m)
    hit = query & mt
    s = len(hit) * 2.0
    if topic_hint:
        s += len(topic_hint & mt) * 3.0
    if _usable(m):
        s += 1.0
    else:
        s -= 5.0
    reason = "命中：" + "、".join(sorted(hit)[:5]) if hit else ""
    return s, reason


def retrieve(materials: Iterable[dict], goal: str, conditions=None, limit: int = 10) -> list:
    q = tokens(goal or "")
    q |= tokens(_conditions_text(conditions))
    topic_hint = set()
    for m in materials:
        if m.get("topic"):
            topic_hint.add(norm_loose(m["topic"]))
    ranked = []
    for m in materials:
        s, reason = score(m, q, topic_hint)
        if s <= 0:
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
