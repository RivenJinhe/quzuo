"""长材料分段选取。

一份材料超过单次生成的字数上限时，只把与本次任务最相关的段落交给模型，
而不是简单砍掉后半截。切分只在原文的空行、句号处断开，所以交出去的每一段
都是原文的连续子串 —— validation 的出处校验仍然成立。
"""
from __future__ import annotations

import re

MIN_CHUNK = 260
MAX_CHUNK = 1400
HEAD_CHUNKS = 2  # 开头几段总是保留：标题、范围、目录通常在这里

_BLANK = re.compile(r"\n\s*\n+")
_SENTENCE = re.compile(r"(?<=[。！？；!?;])|\n")


def _bigrams(text: str) -> set[str]:
    cleaned = re.sub(r"\s+", "", text)
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def split_chunks(text: str) -> list[str]:
    """按空行切块；过长的块再按句子切到 MAX_CHUNK 以内。"""
    blocks = [block.strip() for block in _BLANK.split(text) if block.strip()]
    chunks: list[str] = []
    for block in blocks:
        if len(block) <= MAX_CHUNK:
            chunks.append(block)
            continue
        buf = ""
        for piece in _SENTENCE.split(block):
            if not piece:
                continue
            if buf and len(buf) + len(piece) > MAX_CHUNK:
                chunks.append(buf)
                buf = piece
            else:
                buf += piece
        if buf:
            chunks.append(buf)

    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) < MIN_CHUNK and len(merged[-1]) + len(chunk) + 1 <= MAX_CHUNK:
            merged[-1] = merged[-1] + "\n" + chunk
        else:
            merged.append(chunk)
    return merged


def select(text: str, budget: int, query: str) -> tuple[str, int, int]:
    """挑出最相关的若干段。

    返回 (拼接文本, 原段数, 选中段数)。拼回去的顺序仍按原文顺序，
    这样模型读到的上下文顺序与材料一致。
    """
    if budget <= 0:
        return "", 0, 0
    if len(text) <= budget:
        return text, 0, 0
    chunks = split_chunks(text)
    if len(chunks) <= 1:
        return text[:budget], len(chunks), 1

    query_grams = _bigrams(query)
    scored: list[tuple[float, int]] = []
    for index, chunk in enumerate(chunks):
        overlap = len(_bigrams(chunk) & query_grams) if query_grams else 0
        score = overlap / (len(chunk) ** 0.5)
        if index < HEAD_CHUNKS:
            score += 1000.0
        scored.append((score, index))
    scored.sort(reverse=True)

    picked: list[int] = []
    used = 0
    for _, index in scored:
        chunk = chunks[index]
        if used + len(chunk) > budget and picked:
            continue
        picked.append(index)
        used += len(chunk)
        if used >= budget * 0.98:
            break
    picked.sort()
    body = "\n\n".join(chunks[index] for index in picked)
    return body, len(chunks), len(picked)
