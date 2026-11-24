"""两次生成的差异对比（PRD 4.4：改完条件后给三行「本次改动影响」）。

只对比用户能感知到的三件事：总时长、环节增删、需要的物料数量。
第一次生成没有上一版，返回空列表，界面就不显示这一块。
"""
from __future__ import annotations

_MAX_LINES = 4


def _items(res: dict | None) -> list:
    return [it for it in (res or {}).get("items") or [] if isinstance(it, dict)]


def _minutes_of(it: dict) -> int:
    try:
        return int(it.get("minutes") or 0)
    except (TypeError, ValueError):
        return 0


def _total_minutes(res: dict | None) -> int:
    return sum(_minutes_of(it) for it in _items(res))


def _titles(res: dict | None) -> dict:
    out = {}
    for it in _items(res):
        t = str(it.get("title") or "").strip()
        if t:
            out[t] = _minutes_of(it)
    return out


def _needed(res: dict | None) -> list:
    return [m for m in (res or {}).get("materials_needed") or [] if isinstance(m, dict) and m.get("name")]


def _bigrams(s: str) -> set:
    s = "".join(ch for ch in str(s or "") if ch.strip())
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def _similar(a: str, b: str) -> bool:
    """标题换了个说法（梳理→回顾）也算同一条，不要报成删了又加。"""
    sa = "".join(ch for ch in str(a or "") if ch.strip())
    sb = "".join(ch for ch in str(b or "") if ch.strip())
    # 同一位置的环节常被换个说法（「开场：明确目标」vs「开场定位与目标拆解」），
    # 前两个字一样就当同一条：宁可漏报增删，也不要把措辞变化报成改动。
    common = 0
    for x, y in zip(sa, sb):
        if x == y:
            common += 1
        else:
            break
    if common >= 2:
        return True
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return False
    small, big = (A, B) if len(A) <= len(B) else (B, A)
    # 短标题被长标题整段包含（「开场」vs「开场：明确复习目标」）也算同一条
    if len(small) <= 3 and small <= big:
        return True
    return len(A & B) / len(A | B) >= 0.5


def _qty(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def diff_result(prev: dict | None, cur: dict | None) -> list:
    """返回 [{kind, label, from, to}]，label 是给用户看的一行话。"""
    if not prev or not cur:
        return []
    lines: list[dict] = []

    a, b = _total_minutes(prev), _total_minutes(cur)
    if a and b and a != b:
        tail = "缩短 %d 分钟" % (a - b) if b < a else "多出 %d 分钟" % (b - a)
        lines.append({"kind": "duration", "label": "总时长 %d → %d 分钟（%s）" % (a, b, tail), "from": a, "to": b})

    ta, tb = _titles(prev), _titles(cur)
    removed = [x for x in ta if x not in tb]
    added = [x for x in tb if x not in ta]
    for r in list(removed):
        for a in list(added):
            if _similar(r, a):
                removed.remove(r)
                added.remove(a)
                break
    for t in removed[:2]:
        lines.append({"kind": "removed", "label": "删掉「%s」" % t, "from": t, "to": None})
    for t in added[:2]:
        lines.append({"kind": "added", "label": "加了「%s」" % t, "from": None, "to": t})

    ma = {m["name"]: _qty(m.get("qty")) for m in _needed(prev)}
    mb = {m["name"]: _qty(m.get("qty")) for m in _needed(cur)}
    for name in list(ma)[:2]:
        if name not in mb:
            lines.append({"kind": "material", "label": "%s %s → 0" % (name, ma[name]), "from": ma[name], "to": 0})
        elif ma[name] != mb[name]:
            lines.append({"kind": "material", "label": "%s %s → %s" % (name, ma[name], mb[name]), "from": ma[name], "to": mb[name]})
    for name in [n for n in mb if n not in ma][:2]:
        lines.append({"kind": "material", "label": "%s 0 → %s" % (name, mb[name]), "from": 0, "to": mb[name]})

    if not lines:
        lines.append({"kind": "same", "label": "时间表没变，这一步只调整了措辞和理由", "from": None, "to": None})
    return lines[:_MAX_LINES]
