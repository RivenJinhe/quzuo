"""多模型目录（BE-4xx 扩展）：密钥只在此处从环境变量读，绝不返回前端、不入库、不写日志。"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    provider_label: str
    model: str
    base_url: str
    kind: str
    key_attr: str = ""
    hidden: bool = False


CATALOG: list[ModelSpec] = [
    # 本地占位：仅内部自测（AC 脚本）使用，hidden=True 表示不出现在 /api/v1/models，用户不可选。
    ModelSpec("stub/local", "stub", "本地占位（不调用外部模型）", "stub", "", "stub", hidden=True),
    ModelSpec("deepseek/deepseek-chat", "deepseek", "DeepSeek", "deepseek-chat",
              "https://api.deepseek.com/v1", "openai", "provider_deepseek_key"),
    ModelSpec("kimi/kimi-k2.6", "kimi", "Kimi", "kimi-k2.6",
              "https://api.moonshot.cn/v1", "openai", "provider_kimi_key"),
    ModelSpec("kimi/kimi-k3", "kimi", "Kimi", "kimi-k3",
              "https://api.moonshot.cn/v1", "openai", "provider_kimi_key"),
    ModelSpec("qwen/qwen-plus", "qwen", "通义千问", "qwen-plus",
              "https://dashscope.aliyuncs.com/compatible-mode/v1", "openai", "provider_qwen_key"),
    ModelSpec("qwen/qwen4-max", "qwen", "通义千问", "qwen4-max",
              "https://dashscope.aliyuncs.com/compatible-mode/v1", "openai", "provider_qwen_key"),
    ModelSpec("minimax/MiniMax-M1", "minimax", "MiniMax", "MiniMax-M1",
              "https://api.minimax.chat/v1/text/chatcompletion_v2", "minimax", "provider_minimax_key"),
]

BY_ID = {m.id: m for m in CATALOG}


def api_key(spec: ModelSpec) -> str:
    return getattr(settings, spec.key_attr, "") if spec.key_attr else ""


def default_model_id() -> str:
    configured = (settings.llm_default_model or "").strip()
    if configured in BY_ID:
        ok, _ = availability(BY_ID[configured])
        if ok:
            return configured
    # 配置的默认模型不可用时，退到第一个可用的真实模型；真实模型全不可用才用占位。
    for spec in CATALOG:
        if spec.hidden:
            continue
        ok, _ = availability(spec)
        if ok:
            return spec.id
    return "stub/local"


def availability(spec: ModelSpec) -> tuple[bool, str]:
    if spec.kind == "stub":
        return True, ""
    if not settings.llm_allow_real:
        return False, "服务端未开启真实模型（QU_LLM_ALLOW_REAL=false）"
    if not api_key(spec):
        return False, "服务端未配置该模型密钥"
    return True, ""


def public_catalog() -> list[dict]:
    out = []
    for m in CATALOG:
        if m.hidden:
            continue
        ok, why = availability(m)
        out.append({
            "id": m.id,
            "provider": m.provider,
            "provider_label": m.provider_label,
            "model": m.model,
            "available": ok,
            "reason": why,
            "default": m.id == default_model_id(),
        })
    return out


def resolve(model_id: str) -> ModelSpec | None:
    return BY_ID.get((model_id or "").strip())
