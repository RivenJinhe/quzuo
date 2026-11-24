"""模型单价与费用估算（元 / 百万 token）。

单价留 0 表示还没核过官网价：此时只记 token 用量，不算钱，绝不编数字。
上线前按各厂商控制台填这里，或用环境变量 QU_LLM_PRICES 覆盖，
格式 "model_id:输入价,输出价"，多条用分号分隔，例如
QU_LLM_PRICES="deepseek/deepseek-chat:1,2;kimi/kimi-k2.6:4,16"
"""
from __future__ import annotations

from app.config import settings

# 口径：输入价按「缓存未命中」标准价，不含缓存命中折扣；DeepSeek 有高峰/空闲
# 两档，这里取高峰（标准）价，宁可算高不算低。数值来自 2026-11-24 各厂商定价页截图。
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "deepseek/deepseek-chat": (2.00, 8.00),   # DeepSeek 高峰：输入 2 / 输出 8
    "kimi/kimi-k2.6": (0.0, 0.0),             # 官网未列这一版，费用留未知
    "kimi/kimi-k3": (20.00, 100.00),          # Kimi K3
    "qwen/qwen-plus": (2.00, 8.00),           # 通义 qwen3.7-plus 标准价
    "qwen/qwen4-max": (10.00, 30.00),         # 通义 qwen4-max
    "minimax/MiniMax-M1": (0.0, 0.0),         # 官网列的是 M3 系列，M1 未列
}


def _parse(raw: str) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        mid, _, nums = chunk.partition(":")
        parts = [p.strip() for p in nums.split(",")]
        try:
            inn = float(parts[0]) if parts and parts[0] else 0.0
            outv = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
        except ValueError:
            continue
        if mid.strip():
            out[mid.strip()] = (inn, outv)
    return out


def prices() -> dict[str, tuple[float, float]]:
    table = dict(DEFAULT_PRICES)
    table.update(_parse(getattr(settings, "llm_prices", "") or ""))
    return table


def estimate(model_id: str, usage: dict | None):
    """按 token 用量和单价算钱；单价没填返回 None，表示费用未知。"""
    if not usage:
        return None
    inn, out = prices().get(model_id, (0.0, 0.0))
    if not inn and not out:
        return None
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    return round(tokens_in / 1_000_000 * inn + tokens_out / 1_000_000 * out, 6)
