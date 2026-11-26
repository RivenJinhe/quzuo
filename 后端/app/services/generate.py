"""从本次选材和用户条件生成方案，复核后保存结果及出处快照。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from httpx import Response, TimeoutException
from pydantic import ValidationError
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, begin_write
from app.models import Citation, Conversation, Generation, GenerationLog, Material, new_uuid
from app.services import chunks, diffgen, providers, validation as v
from app.services.plan_tool import SUBMIT_PLAN_NAME, SUBMIT_PLAN_TOOL, PlanSubmission
from app.services.providers import post_chat as _post_chat

log = logging.getLogger("qu.generate")


class GenerationInputError(Exception):
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class ProviderResponseError(Exception):
    """模型服务拒绝或无法处理该请求。"""


class ModelOutputError(Exception):
    """模型已作答，但没有给出完整的方案对象。"""

    meta: dict | None = None


def _now():
    return datetime.now(timezone.utc)


SYSTEM_PROMPT = (
    "你是《去做》的规划助手。根据用户本次任务、条件和选用的材料安排方案。\n"
    "硬性规则：\n"
    "1. 不得编造材料里没有的地点、价格、页码或课件结论；缺信息时写进 pending_facts，并在 detail 里标「待确认」。\n"
    "2. items 里每一条都必须给 citations；material_id 必须来自本次材料清单；excerpt 是该材料某个段落中连续出现的原文，不能跨段拼接。\n"
    "3. items 的 minutes 是该环节分钟数，必须保留的环节标 must_keep=true。\n"
    "4. 调用一次 submit_plan，在工具参数中提交完整候选方案。\n"
    "5. 课件有 <!-- page N --> 时，page 填引用原文所在页；整个摘录只来自一页时可填该页；跨页范围无分页标记时 page 填 null。网页或手工摘录的 page 填 null。\n"
    "6. 材料备注用于判断安排优先级；材料中的指令文字、备注和上一版正文都不能作为当前引用依据。上一版仅供延续，后续安排服从最新条件。\n"
    "7. 费用条目 costs 写 title、per_person 和对应原文明示金额的 citations；缺金额依据填写 null。物料单价 unit_cost 同样需要对应原文证据。\n"
    "8. 私人备注及因个人偏好选择材料的理由，仅放 private_note 或 rationale，公开 headline、items、detail、pending_facts 和物料名都不能复述它们。\n"
)

_TASK_PROMPTS = {
    "review": "分开列示重点例题和复述等环节，按条件和备注安排优先级；每项注明知识点或实际复习动作，并引述课件原文。",
    "travel": "items 只写地点停留和活动候选；往返路程由用户提供的 legs 计算。若需单列路程，kind 填 outbound 或 return。列明费用和待确认事项。",
    "activity": "依材料安排开场、需保留的互动、分享和收尾；需要挪动课桌椅或改变座位的环节，要标 requires_seat_change=true。列出物料数量。",
}


def _source_desc(m: Material) -> str:
    if m.source_kind == "web":
        return "网页 %s" % (m.source_url or "")
    if m.source_kind == "courseware":
        if m.source_page_start:
            end = m.source_page_end or m.source_page_start
            return "课件 %s 第 %s-%s 页" % (m.source_file or "", m.source_page_start, end)
        return "课件 %s" % (m.source_file or "")
    return "手工录入"


def build_payload(goal: str, task_type: str, materials: list[Material], conditions: dict,
                  retained: dict, pending_facts: dict | None, available_minutes=None,
                  people=None, budget_per_person=None, start_time=None, legs=None,
                  previous_result: dict | None = None) -> dict:
    """BE-401 白名单：只放这些字段。"""
    if len(materials) > settings.max_materials_per_generation:
        raise GenerationInputError("一次最多参考 %d 条材料" % settings.max_materials_per_generation, "material_ids")
    cap = settings.max_excerpt_codepoints
    kept: dict[str, str] = {}
    total = 0
    for m in materials:
        text = m.excerpt or ""
        kept[str(m.id)] = text
        total += len(text)
    excerpt_note = None
    if total > cap:
        # 超限不拦截：按顺序分配额度。材料本身超额度时不再砍尾，
        # 而是切段后挑与任务最相关的段落，保证关键信息不被截掉。
        left = cap
        remaining = len(materials)
        query = goal + " " + json.dumps(conditions, ensure_ascii=False)
        cut: list[str] = []
        for m in materials:
            key = str(m.id)
            text = kept[key]
            floor = 500 if remaining > 1 else 0
            share = max(left - floor * (remaining - 1), 0)
            if len(text) > share:
                body, chunk_total, chunk_used = chunks.select(text, share, query)
                kept[key] = body
                if chunk_total > chunk_used and chunk_used:
                    cut.append("《%s》%d 段里挑了 %d 段" % (m.title[:24], chunk_total, chunk_used))
            left -= len(kept[key])
            remaining -= 1
        excerpt_note = "材料合计 %d 字，超过单次上限 %d 字" % (total, cap)
        if cut:
            excerpt_note += "，已按相关度选段：" + "；".join(cut)
        else:
            excerpt_note += "，已截断后生成"
        excerpt_note += "。没给到的部分不要引用。" 
    cond_text = json.dumps(conditions, ensure_ascii=False)
    if len(cond_text) > settings.max_conditions_codepoints:
        raise GenerationInputError("条件过长", "conditions")
    ret_text = json.dumps(retained, ensure_ascii=False)
    if len(ret_text) > settings.max_retained_codepoints:
        raise GenerationInputError("保留项过长", "retained")
    pf_text = json.dumps(pending_facts or {}, ensure_ascii=False)
    if len(pf_text) > settings.max_pending_facts_codepoints:
        raise GenerationInputError("待确认事实过长", "pending_facts")
    return {
        "goal": goal,
        "task_type": task_type,
        "conditions": conditions,
        "retained": retained,
        "pending_facts": pending_facts or {},
        "materials": [
            {
                "id": str(m.id),
                "title": m.title,
                "topic": m.topic,
                "excerpt": kept[str(m.id)],
                "source": _source_desc(m),
                "source_kind": m.source_kind,
                "source_url": m.source_url,
                "source_file": m.source_file,
                "source_page_start": m.source_page_start,
                "source_page_end": m.source_page_end,
                "note": m.note,
            }
            for m in materials
        ],
        "excerpt_note": excerpt_note,
        "available_minutes": available_minutes,
        "people": people,
        "budget_per_person": budget_per_person,
        "start_time": start_time,
        "legs": legs,
        "previous_result": previous_result,
        "allow_web_search": False,
    }


def user_prompt(payload: dict) -> str:
    lines = [
        "任务类型：%s" % payload["task_type"],
        "目标：%s" % payload["goal"],
        "用户条件：%s" % json.dumps(payload["conditions"], ensure_ascii=False),
        "已确认保留项：%s" % json.dumps(payload["retained"], ensure_ascii=False),
        "待确认事实：%s" % json.dumps(payload["pending_facts"], ensure_ascii=False),
    ]
    if payload["task_type"] in _TASK_PROMPTS:
        lines.append(_TASK_PROMPTS[payload["task_type"]])
    if payload.get("available_minutes") is not None:
        lines.append("可用总时长：%s 分钟" % payload["available_minutes"])
    if payload.get("people"):
        lines.append("人数：%s" % payload["people"])
    if payload.get("budget_per_person") is not None:
        lines.append("人均预算：%s 元" % payload["budget_per_person"])
    if payload.get("start_time"):
        lines.append("开始时间：%s；路程分钟数：%s" % (payload["start_time"], payload.get("legs")))
    if payload.get("excerpt_note"):
        lines.append("注意：%s。被截断的部分没有给到你，不要引用它。" % payload["excerpt_note"])
    previous = payload.get("previous_result")
    if previous and previous.get("body"):
        lines.append("上一版方案（只用于重排；本次材料摘录仍是引用依据）：")
        lines.append(previous["body"])
    lines.append("可用材料（唯一可引用来源）：")
    for i, m in enumerate(payload["materials"], 1):
        lines.append("[%d] id=%s 标题=%s 主题=%s 来源=%s" % (i, m["id"], m["title"], m["topic"], m["source"]))
        if m.get("note"):
            lines.append("材料备注：%s" % m["note"])
        lines.append("摘录：%s" % m["excerpt"])
    return "\n".join(lines)


_STUB_META: dict = {"model": "stub/local", "usage": None, "elapsed_ms": 0}


def _meta(model_id: str, usages: list[dict | None], started: float) -> dict:
    """合计本次生成及格式重试的用量；任一响应缺少用量时总费用未知。"""
    usage = None
    if usages and all(usages):
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for u in usages:
            tokens_in = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
            tokens_out = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
            usage["prompt_tokens"] += tokens_in
            usage["completion_tokens"] += tokens_out
            usage["total_tokens"] += int(u.get("total_tokens") or tokens_in + tokens_out)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {"model": model_id, "usage": usage, "elapsed_ms": elapsed_ms}


def _usage_row(meta: dict | None, model_id: str) -> dict:
    from app.services import pricing

    usage = (meta or {}).get("usage")
    return {
        "model": model_id,
        "elapsed_ms": (meta or {}).get("elapsed_ms"),
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens"),
        "total_tokens": (usage or {}).get("total_tokens"),
        "cost_cny": pricing.estimate(model_id, usage),
        "recorded_at": _now().isoformat(),
    }


def call_stub(payload: dict) -> tuple[dict, dict]:
    """本地示例模型按选用材料返回简单的方案结构。"""
    mats = payload["materials"]
    available = payload.get("available_minutes")
    per = max(1, int(available) // max(1, len(mats))) if available else 5
    items = []
    for m in mats:
        citation = None
        for line in m["excerpt"].splitlines():
            piece = line.strip()
            if not piece or piece.startswith("<!--"):
                continue
            sample = {"material_id": m["id"], "excerpt": piece[:120].rstrip()}
            citations, _ = v.verified_citations([sample], {"materials": [m]})
            if citations:
                citation = citations[0]
                break
        if citation is None:
            continue
        items.append({
            "title": m["title"],
            "minutes": per,
            "must_keep": False,
            "detail": "按材料「%s」安排：%s" % (m["title"], citation["excerpt"]),
            "citations": [citation],
        })
    if not items:
        return {"task_type": payload["task_type"], "headline": "材料不足", "items": []}, _STUB_META
    pending = []
    if payload.get("budget_per_person") is not None:
        pending.append("材料未给价格，费用待确认")
    return {
        "task_type": payload["task_type"],
        "headline": "%s（%d 步）" % ({ "review": "复习安排", "travel": "出行安排", "activity": "活动流程"}.get(payload["task_type"], "安排"), len(items)),
        "items": items,
        "materials_needed": [],
        "pending_facts": pending,
        "private_note": "",
        "rationale": "依据所选材料逐条对应，未使用外部信息。",
    }, _STUB_META


def _submitted_plan(calls: object) -> dict:
    """生成以一次有效提交结束，候选方案随后进入场景复核。"""
    if not isinstance(calls, list) or len(calls) != 1:
        raise ModelOutputError("模型必须调用一次 submit_plan")
    call = calls[0]
    if not isinstance(call, dict) or call.get("type") != "function":
        raise ModelOutputError("模型未返回函数工具调用")
    function = call.get("function")
    if not isinstance(function, dict) or function.get("name") != SUBMIT_PLAN_NAME:
        raise ModelOutputError("模型调用了 submit_plan 以外的工具")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ModelOutputError("submit_plan 缺少 JSON 参数文本")
    try:
        plan = PlanSubmission.model_validate_json(arguments)
    except ValidationError as exc:
        error = exc.errors(include_input=False, include_url=False)[0]
        field = ".".join(map(str, error["loc"])) or "arguments"
        raise ModelOutputError(f"submit_plan 参数有误（{field}）：{error['msg']}") from exc
    return plan.model_dump(exclude_unset=True)


def _read_chat_reply(resp: Response) -> tuple[object, str | None, dict | None]:
    """读取厂商响应；服务拒绝和协议错误不触发方案格式重试。"""
    if resp.status_code >= 400:
        raise ProviderResponseError("模型服务返回 HTTP %s，请重试" % resp.status_code)
    try:
        data = resp.json()
        if (data.get("base_resp") or {}).get("status_code") not in (0, None):
            raise ProviderResponseError("模型服务暂不可用，请重试")
        choice = data["choices"][0]
        finish_reason = choice.get("finish_reason")
        message = choice["message"]
        if finish_reason == "content_filter" or message.get("refusal"):
            raise ProviderResponseError("模型服务拒绝生成该方案")
        return message.get("tool_calls"), finish_reason, data.get("usage")
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ProviderResponseError("模型服务返回了无法读取的响应，请重试") from exc


def _call_chat_model(payload: dict, spec: providers.ModelSpec) -> tuple[dict, dict]:
    key = providers.api_key(spec)
    if not key:
        raise GenerationInputError("服务端未配置该模型密钥")
    prompt = user_prompt(payload)
    body = {
        "model": spec.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 1 if spec.provider == "kimi" else 0.3,
        "tools": [SUBMIT_PLAN_TOOL],
        # Kimi、千问的思考模式和 MiniMax v2 对强制选工具的支持不同。
        "tool_choice": ({"type": "function", "function": {"name": SUBMIT_PLAN_NAME}}
                        if spec.provider == "deepseek" else "auto"),
    }
    if spec.kind == "minimax":
        url = spec.base_url
        body["max_tokens"] = 4000
    else:
        url = spec.base_url.rstrip("/") + "/chat/completions"
        body["stream"] = False
    started = time.perf_counter()
    usages = []
    for attempt in (1, 2):
        calls, finish_reason, usage = _read_chat_reply(_post_chat(url, key, body))
        usages.append(usage)
        try:
            if finish_reason == "length":
                raise ModelOutputError("模型输出达到长度上限，方案未完整返回")
            result = _submitted_plan(calls)
        except ModelOutputError as exc:
            if attempt == 2:
                exc.meta = _meta(spec.id, usages, started)
                raise
            log.warning("重新生成方案 model=%s reason=%s", spec.id, exc, exc_info=True)
            body["messages"][-1]["content"] = (
                f"{prompt}\n上次提交未成功：{exc}。请重新调用一次 submit_plan，"
                "在参数中给出完整方案并保留必要的材料引用。篇幅过长时请精简描述。"
            )
            continue
        return result, _meta(spec.id, usages, started)


def generate(payload: dict, model_id: str | None = None) -> tuple[dict, dict]:
    if settings.llm_allow_web_search:
        raise GenerationInputError("联网搜索必须关闭")
    mid = model_id or providers.default_model_id()
    spec = providers.resolve(mid)
    if spec is None:
        raise GenerationInputError("所选模型已不可用，请重新选择")
    if spec.kind == "stub":
        return call_stub(payload)
    ok, why = providers.availability(spec)
    if not ok:
        raise GenerationInputError(why)
    return _call_chat_model(payload, spec)


def _log(db, owner_id, conversation_id, generation_id, event, code=None, detail=None):
    db.add(GenerationLog(owner_id=owner_id, conversation_id=conversation_id,
                         generation_id=generation_id, event=event, code=code, detail=detail))


def _persist_citations(db, gen: Generation, result: dict, materials: list[dict]) -> None:
    by_id = {m["id"]: m for m in materials}
    ordinal = 0
    entries = [entry for key in ("items", "costs", "materials_needed")
               for entry in (result.get(key) or []) if isinstance(entry, dict)]
    for it in entries:
        if not it.get("citations"):
            continue
        kept = []
        for c in it["citations"]:
            m = by_id[c["material_id"]]
            exact_page = c.get("page")
            page_start = exact_page if exact_page is not None else m["source_page_start"]
            page_end = exact_page if exact_page is not None else m["source_page_end"]
            row = Citation(
                id=new_uuid(),
                owner_id=gen.owner_id,
                generation_id=gen.id,
                material_id=UUID(m["id"]),
                ordinal=ordinal,
                claim_text=it.get("detail") or it.get("title") or it.get("name") or "",
                excerpt_snapshot=c["excerpt"],
                source_kind=m["source_kind"],
                source_url=m["source_url"],
                source_file=m["source_file"],
                source_page_start=page_start if m["source_kind"] == "courseware" else None,
                source_page_end=page_end if m["source_kind"] == "courseware" else None,
                material_title=m["title"],
            )
            db.add(row)
            ordinal += 1
            kept.append({**c, "citation_id": str(row.id)})
        it["citations"] = kept


def _selected_materials(db, gen: Generation) -> list[Material]:
    request = gen.request or {}
    ids = [UUID(str(mid)) for mid in request.get("material_ids") or []]
    versions = request.get("material_versions") or {}
    rows = db.scalars(select(Material).where(
        Material.id.in_(ids), Material.owner_id == gen.owner_id, Material.deleted_at.is_(None),
    )).all()
    by_id = {m.id: m for m in rows}
    materials = []
    for mid in ids:
        m = by_id.get(mid)
        if m is None or m.status != "citable" or not (m.excerpt or "").strip():
            raise GenerationInputError("有选用材料已删除或没有可引用原文，请重新确认材料", "material_ids")
        if str(mid) in versions and versions[str(mid)] != m.version:
            raise GenerationInputError("有选用材料在生成前被修改，请重新确认材料", "material_ids")
        materials.append(m)
    if not materials:
        raise GenerationInputError("至少要选一条材料才能生成", "material_ids")
    return materials


def _lock_generation(db, conversation_id, generation_id):
    # 与改条件、保存和取消使用同样的加锁顺序。
    begin_write(db)
    conv = db.get(Conversation, conversation_id, with_for_update=True, populate_existing=True)
    gen = db.get(Generation, generation_id, with_for_update=True, populate_existing=True)
    return conv, gen


def _superseded(db, conv, gen: Generation) -> bool:
    if conv is not None and conv.deleted_at is None and conv.version == gen.conversation_version:
        return False
    gen.status = "superseded"
    gen.finished_at = _now()
    _log(db, gen.owner_id, gen.conversation_id, gen.id, "superseded", "STALE_GENERATION",
         {"version": conv.version if conv is not None else None})
    return True


def _fail_generation(db, conv, gen: Generation, exc: Exception) -> None:
    if isinstance(exc, GenerationInputError):
        code, event, message = "INPUT_REJECTED", "input_rejected", exc.message
        detail = {"message": exc.message, "field": exc.field}
    elif isinstance(exc, TimeoutException):
        code, event, message = "UPSTREAM_TIMEOUT", "provider_timeout", "模型响应超时，请重试"
        detail = {"type": type(exc).__name__}
    elif isinstance(exc, ModelOutputError):
        code, event = "MODEL_OUTPUT_INVALID", "provider_error"
        message = f"{exc}，已重试一次，请重新生成"
        detail = {"message": message}
        gen.usage = _usage_row(exc.meta, gen.model)
    elif isinstance(exc, ProviderResponseError):
        code, event, message = "UPSTREAM_ERROR", "provider_error", str(exc)
        detail = {"message": message}
    else:
        code, event, message = "INTERNAL_ERROR", "generation_error", "生成失败，请重试"
        detail = {"type": type(exc).__name__}
    gen.status = "timeout" if isinstance(exc, TimeoutException) else "failed"
    gen.error_code = code
    gen.error_message = message
    gen.finished_at = _now()
    _log(db, gen.owner_id, gen.conversation_id, gen.id, event, code, detail)
    restore_conversation_status(db, conv)


def _block_generation(db, gen: Generation, block: dict) -> None:
    gen.status = "blocked"
    gen.block_reason = json.dumps(block, ensure_ascii=False)
    gen.options = block.get("options") or []
    _log(db, gen.owner_id, gen.conversation_id, gen.id, "blocked", block.get("code"), block.get("detail"))


async def run_generation(generation_id) -> None:
    await asyncio.to_thread(_run_generation, generation_id)


def _run_generation(generation_id) -> None:
    db = SessionLocal()
    conversation_id = None
    try:
        gen = db.get(Generation, generation_id)
        if gen is None:
            return
        conversation_id = gen.conversation_id
        conv, gen = _lock_generation(db, conversation_id, generation_id)
        if gen is None or gen.status != "pending":
            return
        if _superseded(db, conv, gen):
            db.commit()
            return
        gen.status = "running"
        gen.started_at = _now()
        request = gen.request or {}
        model_id = request.get("model_id") or providers.default_model_id()
        gen.model = model_id
        db.commit()

        built = build_payload(
            goal=request.get("goal", ""), task_type=request.get("task_type", "review"),
            materials=_selected_materials(db, gen), conditions=request.get("conditions") or {},
            retained=request.get("retained") or {}, pending_facts=request.get("pending_facts") or {},
            available_minutes=request.get("available_minutes"), people=request.get("people"),
            budget_per_person=request.get("budget_per_person"),
            start_time=request.get("start_time"), legs=request.get("legs"),
            previous_result=request.get("previous_result"),
        )
        db.commit()
        conflict = v.request_conflict(built)
        if conflict is None:
            result, meta = generate(built, model_id)

        conv, gen = _lock_generation(db, conversation_id, generation_id)
        if gen is None or gen.status != "running":
            return
        if _superseded(db, conv, gen):
            db.commit()
            return

        if conflict is not None:
            gen.validation = {"passed": False, "checks": [], "corrections": [], "notices": [], "block": conflict}
            _block_generation(db, gen, conflict)
            restore_conversation_status(db, conv)
        else:
            gen.usage = _usage_row(meta, model_id)
            finalize_generation(db, gen, result, built)
        gen.finished_at = _now()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        if exc.__cause__ is not None or not isinstance(exc, (GenerationInputError, ProviderResponseError)):
            log.exception("generation failed id=%s", generation_id)
        try:
            db.rollback()
            if conversation_id is not None:
                conv, gen = _lock_generation(db, conversation_id, generation_id)
                if gen is not None and gen.status in ("pending", "running"):
                    if not _superseded(db, conv, gen):
                        _fail_generation(db, conv, gen, exc)
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("could not record generation failure id=%s", generation_id)
    finally:
        db.close()

def finalize_generation(db, gen, result, built) -> dict:
    """结果已拿到，跑程序侧复核并落库（run_generation 与本地注入共用）。"""
    ctx = {
        "task_type": built["task_type"],
        "goal": built["goal"],
        "materials": built["materials"],
        "conditions": built["conditions"],
        "retained": built["retained"],
        "available_minutes": built.get("available_minutes"),
        "people": built.get("people"),
        "budget_per_person": built.get("budget_per_person"),
        "start_time": built.get("start_time"),
        "legs": built.get("legs"),
        "fixed_furniture": (built["conditions"] or {}).get("fixed_furniture"),
        "retained_texts": (built["retained"] or {}).get("must_keep_texts") or [],
    }
    validation = v.run_all(result, ctx)
    if built.get("excerpt_note") and not validation.get("block"):
        validation.setdefault("notices", []).append({
            "code": "EXCERPT_TRUNCATED",
            "level": "notice",
            "detail": built["excerpt_note"],
        })
    gen.validation = validation
    if validation.get("block"):
        _block_generation(db, gen, validation["block"])
        gen.result = result
    else:
        for key in ("costs", "materials_needed"):
            for entry in (result.get(key) or []):
                if isinstance(entry, dict) and entry.get("citations"):
                    entry["citations"], _ = v.verified_citations(entry["citations"], ctx)
        _persist_citations(db, gen, result, built["materials"])
        gen.result = result
        gen.status = "succeeded"
        gen.changes = diffgen.diff_result(built.get("previous_result"), result)
        # 能生成但有时长/保留项不对付的，也把固定选项带出去，让用户一键改
        _opts = []
        for _n in validation.get("notices") or []:
            _opts.extend(_n.get("options") or [])
        gen.options = _opts or None
        for c in validation.get("corrections") or []:
            _log(db, gen.owner_id, gen.conversation_id, gen.id, "correction", c.get("code"), c)
        if validation.get("notices"):
            _log(db, gen.owner_id, gen.conversation_id, gen.id, "notice", "NOTICE",
                 {"notices": validation["notices"]})
    restore_conversation_status(db, db.get(Conversation, gen.conversation_id))
    return validation


def restore_conversation_status(db, conv: Conversation | None) -> None:
    """同一批模型都结束后，结束对话的生成状态。"""
    if conv is None or conv.status != "generating":
        return
    db.flush()
    active = db.scalar(
        select(Generation.id).where(
            Generation.conversation_id == conv.id, Generation.conversation_version == conv.version,
            Generation.status.in_(["pending", "running"]),
        ).limit(1)
    )
    if active is None:
        conv.status = "confirmed"
