"""将首轮目标和追问中的明确条件转成可计算的值，同时保留原话。"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata

_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(?<![\d.])(?:\d{1,4}|[零一二两三四五六七八九十百千]{1,7})"
_DURATION = re.compile(
    r"(?P<half>半(?:个)?小时)|(?P<h>" + _NUM + r")\s*(?:个)?小时"
    r"(?:(?P<extra>" + _NUM + r")\s*分钟?|(?P<extra_half>半))?|"
    r"(?P<m>" + _NUM + r")\s*(?:分钟?|mins?\b)"
)
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)")
_SPLIT = re.compile(r"[，。；！？,;!?\n]|(?<!\d)\.(?!\d)")
_DUR_HINT = re.compile(
    r"只(?:剩|留|有)|就剩|缩(?:到|成|短到)|压(?:到|成|缩到)|改(?:成|为|到)|调(?:整)?(?:到|为)|"
    r"减(?:少)?到|仅有|控制在|限制在|最多|总(?:共|用时|时长|时间)|一共|可用|可安排|时长|时间"
)
_CHANGE = r"(?:改(?:成|为|到)|调(?:整)?(?:到|为)|提(?:高)?到|降(?:到|为)|升(?:到|为)|放宽到|缩到|仍是|控制在|只有|定在|为|是|约|按|就|共|已知|预计)"
_AMOUNT = r"(?P<amount>\d{1,7}(?:\.\d{1,2})?|[零一二两三四五六七八九十百千]{1,7})"
_DEL_WORDS = r"删(?:掉|去|除)|去掉|不要|取消|砍掉|减掉|拿掉|跳过"
_KEEP_WORDS = r"(?:仍|还)?(?:然)?(?:想|要)?(?:务必|优先)?保留|一定要|必须|务必"
_START = r"离馆|离场|出发|离开|开始|出门|到场|到店|集合"
_END = r"返回|回校|返校|回到|回学校|结束|到校|得回|截止"
_OUTBOUND = r"去|前往|走到|到达|过去"
_RETURN = r"返校|回校|回学校|回程|返回|回去|返程"
_STAY = r"停留|逗留|小坐|坐(?:一会儿)?|喝(?:咖啡)?|咖啡|参观|游玩|停(?:个)?"
_FIXED_SEATING = re.compile(r"(?:桌椅|座位)(?:是|都|为)?固定|固定(?:的)?(?:桌椅|座位)|(?:不能|不得|不许)(?:换座|换位|移动(?:桌椅|座位))")
_MOVABLE_SEATING = re.compile(
    r"(?:桌椅|座位)(?:可以|能|可)(?:移动|搬动|调整)|"
    r"(?<!不|禁)(?:可以|允许|可)换座|(?:桌椅|座位)不(?:再)?固定"
)
_LEAD = re.compile(r"^(?:(?:请|把|将|然后|再|顺便|麻烦|希望|给我|帮我|我想|仍想|要|继续)\s*)+")
_END_KEEP = re.compile(r"(?:一定要|必须|务必)?(?:继续)?(?:保留|讲(?:到)?|练(?:习)?|留下|要讲|要练)(?:一下|一遍)?$")


def _number(s: str) -> int:
    if s.isdigit():
        return int(s)
    total = digit = 0
    for char in s:
        if char in _CN:
            digit = _CN[char]
        else:
            unit = {"十": 10, "百": 100, "千": 1000}[char]
            total += (digit or 1) * unit
            digit = 0
    return total + digit


def _minutes(match: re.Match) -> int:
    if match["half"]:
        return 30
    if match["h"]:
        return _number(match["h"]) * 60 + (
            _number(match["extra"]) if match["extra"] else 30 if match["extra_half"] else 0
        )
    return _number(match["m"])


def _duration_matches(text: str) -> list[re.Match]:
    return [m for m in _DURATION.finditer(text) if 0 <= _minutes(m) <= 1440]


def _time_minutes(value: str | None) -> int | None:
    match = _TIME.fullmatch(str(value or ""))
    return int(match[1]) * 60 + int(match[2]) if match else None


def travel_window(start: str | None, deadline: str | None, legs: list | None) -> int | None:
    """当天时段扣除两段路程。负数表示单算路程也无法按时到达。"""
    first, last = _time_minutes(start), _time_minutes(deadline)
    if first is None or last is None or not isinstance(legs, (list, tuple)) or len(legs) != 2 or any(
        type(n) is not int or n < 0 for n in legs
    ):
        return None
    return last - first - sum(legs)


def normalize_conditions(task_type: str, conditions: dict) -> dict:
    """依据最新的结构化条件重算派生值。"""
    cond = dict(conditions or {})
    if task_type == "travel":
        window = travel_window(cond.get("start_time"), cond.get("deadline"), cond.get("legs"))
        if window is not None:
            cond["available_minutes"] = max(0, window)
    people = cond.get("people")
    total = cond.get("budget_total")
    if isinstance(total, (int, float)) and isinstance(people, int) and people > 0:
        cond["budget_per_person"] = round(total / people, 2)
    return cond


def _time_fields(text: str) -> dict:
    fields = {}
    for clause in _SPLIT.split(text):
        matches = list(_TIME.finditer(clause))
        role = None
        for index, match in enumerate(matches):
            before = clause[matches[index - 1].end() if index else 0:match.start()]
            after = clause[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(clause)]
            # 优先识别贴在时间两侧的动词，避免把下一次时间的动词归给当前时间。
            suffix = re.match(r"\s*(?:前|之前)?\s*(?P<verb>" + _START + "|" + _END + ")", after)
            prefix = re.search(r"(?P<verb>" + _START + "|" + _END + r")(?:[^、]{0,10}?)$", before)
            verb = (suffix or prefix)["verb"] if suffix or prefix else None
            time = "%02d:%s" % (int(match[1]), match[2])
            if verb and re.fullmatch(_END, verb):
                role = "deadline"
            elif verb:
                role = "start_time"
            elif re.search(r"最晚\s*$", before) or re.match(r"\s*(?:前|之前)", after):
                role = "deadline"
            elif not re.search(r"(?:到|至|为)\s*$", before):
                role = None
            if role:
                fields[role] = time
    return fields


def _travel_durations(text: str, old_legs: list | None) -> dict:
    fields = {}
    legs = list(old_legs[:2]) if isinstance(old_legs, (list, tuple)) else []
    legs.extend([None] * (2 - len(legs)))
    for clause in _SPLIT.split(text):
        matches = _duration_matches(clause)
        for index, match in enumerate(matches):
            before = clause[matches[index - 1].end() if index else 0:match.start()]
            after = clause[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(clause)]
            before = re.split(r"然后|还想|接着|另外|打算|希望|顺便", before)[-1]
            after = re.split(r"再|然后|还想|接着|且", after, maxsplit=1)[0]
            value = _minutes(match)
            if re.search(r"各(?:需|要|花|用|走|是)?\s*$", before) and re.search(
                _RETURN + "|" + _OUTBOUND + r"|来回|往返", before
            ):
                legs = [value, value]
            elif re.search(r"(?:直接|直返).*(?:" + _RETURN + r")|直返", before):
                fields["direct_return_minutes"] = value
            elif re.search(_RETURN, before):
                legs[1] = value
            elif re.search(_OUTBOUND, before) and not re.search(r"到.{0,12}(?:停留|坐|喝|参观|游玩)", before):
                legs[0] = value
            elif re.search(_STAY, before + after):
                fields["desired_stay_minutes"] = value
            elif _DUR_HINT.search(before):
                fields["available_minutes"] = value
    if any(n is not None for n in legs):
        fields["legs"] = legs
    return fields


def _travel_places(text: str) -> dict:
    fields = {}
    route = re.search(r"(?:从|由)\s*([^，。；、,\s]{1,16}?)\s*(?:出发|去|前往|到)", text)
    if route:
        fields["origin"] = route[1]
    destination = re.search(r"(?:回到|回|返)\s*(学校|校区|宿舍|家)(?!长)", text)
    if destination:
        fields["destination"] = destination[1]
    elif re.search(r"回校|返校", text):
        fields["destination"] = "学校"
    stops = list(dict.fromkeys(re.findall(
        r"(?:去(?!掉)|到|在|前往)\s*([^，。；、,\s]{0,16}?(?:咖啡馆|咖啡店|博物馆|美术馆|书店|餐厅|公园))", text
    )))
    stops = [stop for stop in stops if stop != fields.get("origin")]
    if stops:
        fields["stop"] = stops[0]
        if len(stops) > 1:
            fields["stops"] = stops
    return fields


def _money_fields(text: str) -> dict:
    fields = {}
    for key, prefix in (
        ("known_cost_per_person", r"(?:(?:人均|每人)\s*(?:已知)?\s*(?:实际)?\s*(?:花费|费用|花销|支出|消费|花|需付)|"
         r"(?:花费|费用|花销|支出|消费)(?:是|为)?\s*(?:人均|每人))"),
        ("budget_total", r"(?:总预算|总共预算|全团预算|总共(?=\s*\d+\s*(?:元|块)))"),
        ("budget_per_person", r"(?:人均预算|每人预算|(?<!总|团)预算)"),
    ):
        pattern = prefix + r"\s*(?:" + _CHANGE + r"\s*)?" + _AMOUNT
        for match in re.finditer(pattern, text):
            val = match["amount"]
            fields[key] = float(val) if val[0].isdigit() else float(_number(val))
    if "budget_total" in fields:
        fields.pop("budget_per_person", None)
    elif not fields:
        match = re.search(r"(?:人均|每人)\s*(?:" + _CHANGE + r"\s*)?" + _AMOUNT, text)
        if match:
            value = match["amount"]
            fields["budget_per_person"] = float(value) if value[0].isdigit() else float(_number(value))
    return fields


def _people(text: str) -> int | None:
    matches = re.finditer(r"(?P<n>" + _NUM + r")\s*(?:个)?(?:人|位)(?!均|头)", text)
    people = [_number(m["n"]) for m in matches]
    return people[-1] if people and 0 < people[-1] <= 2000 else None


def _clean_target(text: str) -> str:
    text = text.strip(" \t「」『』“”\"'")
    text = _LEAD.sub("", text)
    text = _END_KEEP.sub("", text).strip(" \t「」『』“”\"'")
    text = re.sub(r"(?:这个|那个)?环节$|(?:吧|了)$|^(?:那|这)(?:道|个|一)", "", text)
    return text.strip(" \t「」『』“”\"'")


def _target(text: str) -> str:
    return text if 2 <= len(text) <= 35 and not re.search(r"预算|名额|人数|时长|时间|桌椅|座位|^\d", text) else ""


def _keep_targets(clause: str) -> list[tuple[str, int | None]]:
    match = re.search(_KEEP_WORDS, clause)
    if not match:
        return []
    before, after = clause[:match.start()].strip(), clause[match.end():].strip()
    # “保留 12 分钟的互访”与“保留互访 12 分钟”都记录环节的独立时长。
    durations = _duration_matches(after)
    if durations:
        m = durations[0]
        name = _target(_clean_target(after[:m.start()].strip() or after[m.end():].lstrip("的 ") or before))
        if name:
            return [(name, _minutes(m))]
    if after in ("", "保留", "讲", "练", "讲一下", "继续讲", "继续练"):
        after = before
    if re.fullmatch(r"(?:那|这)(?:一)?道(?:例)?题(?:吧|了)?", after.strip(" 「」")):
        return [("这道题", None)]
    name = _target(_clean_target(after))
    return [(name, None)] if name else []


def _removed_targets(clause: str) -> list[str]:
    if re.search(r"(?:不|别)(?:删掉|删去|去掉|取消)", clause):
        return []
    match = re.search(_DEL_WORDS, clause)
    if not match:
        return []
    before, after = clause[:match.start()].strip(), clause[match.end():].strip()
    raw = after if after not in ("", "吧", "了", "即可") else before
    name = _target(_clean_target(raw))
    return [name] if name else []


def _match_item(items, word):
    candidates = [i for i in items or [] if same_target(word, str(i.get("title") or ""))]
    return max(candidates, key=lambda i: len(i["title"]), default=None)


def _previous_example(items: list, kept: list[str]) -> str | None:
    examples = {
        item["title"] for item in items if isinstance(item.get("title"), str)
        and re.search(r"例题|例子|案例|(?:^|[练做讲])题", item["title"])
        and not re.search(r"复述|总结|回顾", item["title"])
    }
    if len(examples) == 1:
        return next(iter(examples))
    previous = [name for name in kept if re.search(r"例题|例子|案例", name)]
    return previous[0] if not examples and len(previous) == 1 else None


def same_target(left: str, right: str) -> bool:
    """在用户和方案的环节名称之间识别常见语序和同义写法。"""
    left, right = (re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", s)) for s in (left, right))
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    if all(re.search(r"互访|互相参观", name) for name in (left, right)):
        return True
    generic_cafe = {"咖啡", "咖啡馆", "咖啡店", "喝咖啡", "咖啡小坐", "咖啡馆小坐"}
    return (left in generic_cafe or right in generic_cafe) and all("咖啡" in name for name in (left, right))


def _duration_field(text: str, task_type: str) -> int | None:
    candidates = []
    for clause in _SPLIT.split(text):
        keep = re.search(_KEEP_WORDS, clause) if task_type == "activity" else None
        general = clause[:keep.start()] if keep else clause
        matches = _duration_matches(general)
        if not matches:
            continue
        if _DUR_HINT.search(general) or (not keep and (
            re.fullmatch(r"\s*(?:还是|就)?\s*" + _DURATION.pattern + r"\s*(?:吧|了)?", general)
            or re.search(r"原有|原来|原先|安排|讲解|迎新|活动|复习", general)
        )):
            candidates.append(_minutes(matches[-1]))
    return candidates[-1] if candidates else None


_LABELS = {
    "duration_minutes": "可安排时长", "total_minutes": "可安排时长", "available_minutes": "可安排时长",
    "budget_per_person": "人均预算", "budget_total": "总预算", "known_cost_per_person": "已知人均花费",
    "people": "人数", "start_time": "开始时间", "deadline": "最晚结束时间", "legs": "两段路程用时",
    "desired_stay_minutes": "希望停留时长", "seating_fixed": "桌椅固定",
    "origin": "起点", "destination": "终点", "stop": "途经地点", "direct_return_minutes": "直返用时",
    "stops": "途经地点",
}
_UNITS = {
    "duration_minutes": " 分钟", "total_minutes": " 分钟", "available_minutes": " 分钟",
    "desired_stay_minutes": " 分钟", "budget_per_person": " 元", "budget_total": " 元",
    "known_cost_per_person": " 元", "legs": " 分钟", "direct_return_minutes": " 分钟",
}


def _show(value):
    if value is None:
        return "未设"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return " / ".join(_show(item) for item in value)
    return str(value)


def parse(text: str, task_type: str, conditions: dict, retained: dict) -> dict:
    """输入首轮目标或追问，返回持续有效的条件与这次变更。"""
    original = (text or "").strip()
    text = unicodedata.normalize("NFKC", original)
    cond = deepcopy(conditions or {})
    ret = deepcopy(retained or {})
    applied = []
    unresolved = []

    def update(key, value):
        old = cond.get(key)
        cond[key] = value
        if old != value:
            applied.append({
                "field": key, "from": old, "to": value,
                "label": "%s %s → %s%s" % (_LABELS[key], _show(old), _show(value), _UNITS.get(key, "")),
            })

    if task_type != "travel":
        minutes = _duration_field(text, task_type)
        if minutes is not None:
            field = "duration_minutes" if task_type == "review" else "total_minutes"
            update(field, minutes)
            cond["available_minutes"] = minutes
    elif text:
        for key, value in {
            **_time_fields(text), **_travel_places(text), **_travel_durations(text, cond.get("legs")),
        }.items():
            update(key, value)

    amounts = _money_fields(text)
    if "budget_per_person" in amounts:
        cond.pop("budget_total", None)
    elif "budget_total" in amounts:
        cond.pop("budget_per_person", None)
    for key, value in amounts.items():
        update(key, value)
    people = _people(text)
    if people is not None:
        update("people", people)
    if cond.get("budget_total") is not None and not cond.get("people"):
        unresolved.append("总预算需要人数才能折算为人均预算")
    if task_type == "activity":
        if _MOVABLE_SEATING.search(text):
            update("seating_fixed", False)
            cond.pop("fixed_furniture", None)
        elif _FIXED_SEATING.search(text):
            update("seating_fixed", True)

    items = cond.get("items") or []
    kept = list(ret.get("must_keep_texts") or [])
    fixed = dict(ret.get("fixed_item_minutes") or {})
    excluded = list(cond.get("excluded_items") or [])
    for clause in _SPLIT.split(text):
        for name in _removed_targets(clause):
            item = _match_item(items, name)
            if item is None and items:
                unresolved.append("已有环节里没有找到「%s」" % name)
            if item is not None:
                items.remove(item)
            if name not in excluded:
                excluded.append(name)
            kept = [existing for existing in kept if not same_target(name, existing)]
            fixed = {existing: mins for existing, mins in fixed.items() if not same_target(name, existing)}
            was_kept = item is not None and item.get("must_keep")
            applied.append({"field": "items", "from": (item or {}).get("title", name), "to": None,
                            "label": "删掉环节 %s%s" % (name, "（原为必须保留项）" if was_kept else "")})
        for name, minutes in _keep_targets(clause):
            referenced = name == "这道题"
            if referenced:
                name = _previous_example(items, kept)
                if not name:
                    unresolved.append("请说明要保留哪道例题")
                    continue
                kept = [existing for existing in kept if existing == name or not same_target(name, existing)]
            item = _match_item(items, name)
            if item is not None:
                item["must_keep"] = True
            excluded = [existing for existing in excluded if not same_target(name, existing)]
            if not any(same_target(name, existing) for existing in kept):
                kept.append(name)
                applied.append({"field": "retained", "from": None, "to": name, "label": "保留要求「%s」" % name})
            if minutes is not None and task_type != "review":
                key = next((existing for existing in fixed if same_target(existing, name)), name)
                old = fixed.get(key)
                fixed[key] = minutes
                if old != minutes:
                    applied.append({"field": "fixed_item_minutes", "from": old, "to": minutes,
                                    "label": "%s %s → %s 分钟" % (key, _show(old), minutes)})
    if "must_keep_texts" in ret or kept:
        ret["must_keep_texts"] = kept[-20:]
    if "fixed_item_minutes" in ret or fixed:
        ret["fixed_item_minutes"] = fixed
    if "excluded_items" in cond or excluded:
        cond["excluded_items"] = excluded[-20:]
    if isinstance(cond.get("items"), list):
        cond["items"] = items

    notes = list(cond.get("user_notes") or [])
    if original and original[:300] not in notes:
        notes.append(original[:300])
    cond["user_notes"] = notes[-10:]
    normalized = normalize_conditions(task_type, cond)
    return {
        "conditions": normalized,
        "retained": ret,
        "applied": applied,
        "unresolved": unresolved,
        "summary": "；".join(a["label"] for a in applied) or "没识别出具体改动，已按原话记进补充说明",
    }
