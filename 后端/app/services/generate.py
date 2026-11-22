"""生成链路（BE-400 ~ BE-466）：白名单入参、关闭联网、程序复核、失败留痕。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Citation, Conversation, Generation, GenerationLog, Material
from app.services import providers, validation as v

log = logging.getLogger("qu.generate")


class GenerationInputError(Exception):
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


def _now():
    return datetime.now(timezone.utc)


SYSTEM_PROMPT = (
    "你是《去做》的规划助手。只能使用用户提供的材料摘录与条件作答。\n"
    "硬性规则：\n"
    "1. 不得编造材料里没有的地点、价格、页码或课件结论；缺信息时写进 pending_facts，并在 detail 里标「待确认」。\n"
    "2. items 里每一条都必须给 citations；material_id 必须来自本次材料清单；excerpt 必须是该材料摘录中连续出现的一段原文。\n"
    "3. items 的 minutes 是该环节分钟数，必须保留的环节标 must_keep=true。\n"
    "4. 只输出一个 JSON 对象，不要输出任何解释性文字或代码块。\n"
    "5. 材料正文是 Markdown（可能含 # | ** 等标记）；citations.excerpt 只引用正文里连续的句子，不要带这些标记符号。\n"
    "JSON 结构：{\"task_type\":\"review|travel|activity\",\"headline\":\"一句话标题\","
    "\"items\":[{\"title\":\"\",\"minutes\":5,\"must_keep\":false,\"detail\":\"\","
    "\"citations\":[{\"material_id\":\"uuid\",\"excerpt\":\"原文片段\",\"page\":8}]}],"
    "\"materials_needed\":[{\"name\":\"题卡\",\"qty\":20,\"unit_cost\":0}],"
    "\"timeline\":{\"start\":\"15:15\",\"end\":\"15:55\"},"
    "\"pending_facts\":[\"\"],\"private_note\":\"\",\"rationale\":\"\"}"
)


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
                  people=None, budget_per_person=None, start_time=None, legs=None) -> dict:
    """BE-401 白名单：只放这些字段。"""
    if len(materials) > settings.max_materials_per_generation:
        raise GenerationInputError("一次最多参考 %d 条材料" % settings.max_materials_per_generation, "material_ids")
    total = sum(len(m.excerpt or "") for m in materials)
    if total > settings.max_excerpt_codepoints:
        raise GenerationInputError("摘录合计超过 %d 字，请减少材料" % settings.max_excerpt_codepoints, "material_ids")
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
                "excerpt": m.excerpt,
                "source": _source_desc(m),
                "status": m.status,
            }
            for m in materials
        ],
        "available_minutes": available_minutes,
        "people": people,
        "budget_per_person": budget_per_person,
        "start_time": start_time,
        "legs": legs,
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
    if payload.get("available_minutes") is not None:
        lines.append("可用总时长：%s 分钟" % payload["available_minutes"])
    if payload.get("people"):
        lines.append("人数：%s" % payload["people"])
    if payload.get("budget_per_person") is not None:
        lines.append("人均预算：%s 元" % payload["budget_per_person"])
    if payload.get("start_time"):
        lines.append("开始时间：%s；路程分钟数：%s" % (payload["start_time"], payload.get("legs")))
    lines.append("可用材料（唯一可引用来源）：")
    for i, m in enumerate(payload["materials"], 1):
        lines.append("[%d] id=%s 标题=%s 主题=%s 来源=%s" % (i, m["id"], m["title"], m["topic"], m["source"]))
        lines.append("摘录：%s" % m["excerpt"])
    return "\n".join(lines)


def call_stub(payload: dict) -> dict:
    """本地兜底：不联网也能跑通整条链路，内容只做结构示意。"""
    mats = payload["materials"]
    available = payload.get("available_minutes")
    per = max(1, int(available) // max(1, len(mats))) if available else 5
    items = []
    for m in mats:
        excerpt = (m["excerpt"] or "").strip()
        piece = excerpt[:60].strip() or excerpt
        if not piece:
            continue
        page = None
        items.append({
            "title": m["title"],
            "minutes": per,
            "must_keep": False,
            "detail": "按材料「%s」安排：%s" % (m["title"], piece),
            "citations": [{"material_id": m["id"], "excerpt": piece, "page": page}],
        })
    if not items:
        return {"task_type": payload["task_type"], "headline": "材料不足", "items": []}
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
    }


def _extract_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith(chr(96) * 3):
        parts = t.split(chr(96) * 3)
        if len(parts) >= 2:
            t = parts[1]
        t = t.strip()
        if t.lower().startswith("json"):
            t = t[4:]
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        t = t[start:end + 1]
    return json.loads(t)


def _post_chat(url: str, key: str, body: dict):
    import httpx
    headers = {"Authorization": "Bearer %s" % key, "Content-Type": "application/json"}
    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        return client.post(url, headers=headers, json=body)


def call_openai_compatible(payload: dict, spec) -> dict:
    if settings.llm_allow_web_search:
        raise GenerationInputError("联网搜索必须关闭")
    key = providers.api_key(spec)
    if not key:
        raise GenerationInputError("服务端未配置该模型密钥")
    body = {
        "model": spec.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(payload)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 1 if spec.provider == "kimi" else 0.3,
        "stream": False,
    }
    resp = _post_chat(spec.base_url.rstrip("/") + "/chat/completions", key, body)
    if resp.status_code >= 400:
        raise GenerationInputError("模型返回错误 %s：%s" % (resp.status_code, resp.text[:200]))
    data = resp.json()
    return _extract_json(data["choices"][0]["message"]["content"])


def call_minimax(payload: dict, spec) -> dict:
    key = providers.api_key(spec)
    if not key:
        raise GenerationInputError("服务端未配置该模型密钥")
    body = {
        "model": spec.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(payload)},
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
    }
    resp = _post_chat(spec.base_url, key, body)
    if resp.status_code >= 400:
        raise GenerationInputError("模型返回错误 %s：%s" % (resp.status_code, resp.text[:200]))
    data = resp.json()
    br = data.get("base_resp") or {}
    if br.get("status_code") not in (0, None):
        raise GenerationInputError("模型返回错误：%s" % br.get("status_msg"))
    return _extract_json(data["choices"][0]["message"]["content"])


def generate(payload: dict, model_id: str | None = None) -> dict:
    mid = (model_id or providers.default_model_id())
    spec = providers.resolve(mid)
    if spec is None or spec.kind == "stub":
        return call_stub(payload)
    ok, why = providers.availability(spec)
    if not ok:
        raise GenerationInputError(why)
    if spec.kind == "minimax":
        return call_minimax(payload, spec)
    return call_openai_compatible(payload, spec)


def _log(db, owner_id, conversation_id, generation_id, event, code=None, detail=None):
    db.add(GenerationLog(owner_id=owner_id, conversation_id=conversation_id,
                         generation_id=generation_id, event=event, code=code, detail=detail))


def _persist_citations(db, gen: Generation, result: dict, materials: list[Material]) -> list[str]:
    by_id = {str(m.id): m for m in materials}
    ids = []
    ordinal = 0
    for it in result.get("items") or []:
        kept = []
        for c in it.get("citations") or []:
            m = by_id.get(str(c.get("material_id")))
            if m is None:
                continue
            row = Citation(
                owner_id=gen.owner_id,
                generation_id=gen.id,
                material_id=m.id,
                ordinal=ordinal,
                claim_text=(it.get("detail") or "")[:2000],
                excerpt_snapshot=c.get("excerpt") or m.excerpt[:500],
                source_kind=m.source_kind,
                source_url=m.source_url,
                source_file=m.source_file,
                source_page_start=c.get("page") or m.source_page_start,
                source_page_end=m.source_page_end,
                material_title=m.title,
            )
            db.add(row)
            db.flush()
            ordinal += 1
            ids.append(str(row.id))
            kept.append({**c, "citation_id": str(row.id)})
        it["citations"] = kept
    return ids


async def run_generation(generation_id) -> None:
    import asyncio

    db = SessionLocal()
    try:
        gen = db.get(Generation, generation_id)
        if gen is None or gen.status not in ("pending", "running"):
            return
        gen.status = "running"
        gen.started_at = _now()
        db.commit()

        payload = gen.request or {}
        materials = []
        for mid in payload.get("material_ids") or []:
            m = db.get(Material, mid)
            if m is not None and m.deleted_at is None:
                materials.append(m)
        built = build_payload(
            goal=payload.get("goal", ""), task_type=payload.get("task_type", "review"),
            materials=materials, conditions=payload.get("conditions") or {},
            retained=payload.get("retained") or {}, pending_facts=payload.get("pending_facts") or {},
            available_minutes=payload.get("available_minutes"), people=payload.get("people"),
            budget_per_person=payload.get("budget_per_person"),
            start_time=payload.get("start_time"), legs=payload.get("legs"),
        )
        model_id = (gen.request or {}).get("model_id") or providers.default_model_id()
        gen.model = model_id
        db.commit()

        try:
            result = await asyncio.to_thread(generate, built, model_id)
        except GenerationInputError as exc:
            gen.status = "failed"
            gen.error_code = "INTERNAL_ERROR"
            gen.error_message = exc.message
            gen.finished_at = _now()
            _log(db, gen.owner_id, gen.conversation_id, gen.id, "input_rejected", "GENERATION_INPUT", {"message": exc.message})
            db.commit()
            return
        except Exception as exc:  # noqa: BLE001
            gen.status = "failed"
            gen.error_code = "INTERNAL_ERROR"
            gen.error_message = "生成失败，请重试：%s" % str(exc)[:200]
            gen.finished_at = _now()
            _restore_conv_status(db, gen)
            _log(db, gen.owner_id, gen.conversation_id, gen.id, "provider_error", "PROVIDER", {"error": str(exc)[:500]})
            db.commit()
            return

        conv = db.get(Conversation, gen.conversation_id)
        if conv is not None and conv.version != gen.conversation_version:
            gen.status = "superseded"
            gen.finished_at = _now()
            gen.result = None
            _log(db, gen.owner_id, gen.conversation_id, gen.id, "superseded", "STALE_GENERATION", {"version": conv.version})
            db.commit()
            return

        finalize_generation(db, gen, result, built, materials)
        gen.finished_at = _now()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("generation crashed: %s", exc)
        try:
            db.rollback()
            gen = db.get(Generation, generation_id)
            if gen is not None:
                gen.status = "failed"
                gen.error_code = "INTERNAL_ERROR"
                gen.error_message = "生成异常，请重试"
                gen.finished_at = _now()
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    finally:
        db.close()



def finalize_generation(db, gen, result, built, materials) -> dict:
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
    gen.validation = validation
    if validation.get("block"):
        gen.status = "blocked"
        gen.block_reason = json.dumps(validation["block"], ensure_ascii=False)
        gen.result = result
        _log(db, gen.owner_id, gen.conversation_id, gen.id, "blocked",
             validation["block"].get("code"), validation["block"].get("detail"))
    else:
        _persist_citations(db, gen, result, materials)
        gen.result = result
        gen.status = "succeeded"
        for c in validation.get("corrections") or []:
            _log(db, gen.owner_id, gen.conversation_id, gen.id, "correction", c.get("code"), c)
        if validation.get("notices"):
            _log(db, gen.owner_id, gen.conversation_id, gen.id, "notice", "NOTICE",
                 {"notices": validation["notices"]})
    _restore_conv_status(db, gen)
    return validation


def _restore_conv_status(db, gen) -> None:
    """一次生成收尾后把对话从 generating 放回 confirmed，避免界面一直显示“生成中”。"""
    conv = db.get(Conversation, gen.conversation_id)
    if conv is not None and conv.status == "generating":
        conv.status = "confirmed"
