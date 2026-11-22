"""文件转 Markdown：pdf / docx / pptx / txt / md → 纯文本 Markdown，供大模型读取。

边界：只做文字与结构的转换（标题、列表、表格、页码标记）；不做 OCR、不还原版式、不解析图片。
"""
from __future__ import annotations

from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 300
MAX_CHARS = 20000

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv"}


class ExtractError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _pages_pdf(data: bytes) -> list[str]:
    """逐页转 Markdown（pymupdf4llm 识别标题/表格；失败回退纯文本）。"""
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        total = min(len(doc), MAX_PAGES)
        try:
            import pymupdf4llm

            chunks = pymupdf4llm.to_markdown(doc, pages=list(range(total)), page_chunks=True)
            pages = [(c.get("text") or "").strip() for c in chunks]
            if any(pages):
                return pages
        except Exception:  # noqa: BLE001  转换失败就退回纯文本
            pass
        return [(doc[i].get_text("text", sort=True) or "").strip() for i in range(total)]
    finally:
        doc.close()


def _md_table(rows: list[list[str]]) -> str:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [(r + [""] * (width - len(r)))[:width] for r in rows]
    def cell(c: str) -> str:
        return (c or "").replace("|", "\\|").replace("\n", " ").strip()
    head = "| " + " | ".join(cell(c) for c in norm[0]) + " |"
    sep = "| " + " | ".join(["---"] * width) + " |"
    body = ["| " + " | ".join(cell(c) for c in r) + " |" for r in norm[1:]]
    return "\n".join([head, sep] + body)


def _pages_docx(data: bytes) -> list[str]:
    import io

    from docx import Document

    doc = Document(io.BytesIO(data))
    lines: list[str] = []
    blocks = doc.iter_inner_content() if hasattr(doc, "iter_inner_content") else doc.paragraphs
    for block in blocks:
        if hasattr(block, "rows"):
            table = _md_table([[c.text for c in row.cells] for row in block.rows])
            if table:
                lines.append(table)
            continue
        text = (block.text or "").strip()
        if not text:
            continue
        style = ((block.style.name if block.style else "") or "").lower()
        if style.startswith("heading"):
            digits = "".join(ch for ch in style if ch.isdigit())
            level = min(6, max(1, int(digits) if digits else 1))
            lines.append("#" * level + " " + text)
        elif style.startswith("list"):
            lines.append("- " + text)
        else:
            lines.append(text)
    return ["\n\n".join(lines)]


def _pages_pptx(data: bytes) -> list[str]:
    import io

    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    pages: list[str] = []
    for idx, slide in enumerate(prs.slides):
        if idx >= MAX_PAGES:
            break
        lines: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = (shape.text_frame.text or "").strip()
                if text:
                    lines.extend([ln.rstrip() for ln in text.splitlines() if ln.strip()])
            if getattr(shape, "has_table", False):
                rows = [[c.text for c in row.cells] for row in shape.table.rows]
                table = _md_table(rows)
                if table:
                    lines.append(table)
        pages.append("\n\n".join(lines))
    return pages


def _pages_text(data: bytes) -> list[str]:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return [data.decode(enc)]
        except UnicodeDecodeError:
            continue
    raise ExtractError("UNSUPPORTED_FILE_TYPE", "文本文件编码无法识别（试过 UTF-8 / GBK）")


def extract(filename: str, data: bytes, page_start: int | None = None, page_end: int | None = None) -> dict:
    """返回 {source_file, kind, page_count, page_start, page_end, chars, excerpt, truncated, suggested_title, pages}。

    excerpt / markdown 是同一份 Markdown 文本（超过 20000 字截断），前端下载 .md 与存材料都用它。
    """
    if not data:
        raise ExtractError("VALIDATION_ERROR", "文件是空的")
    if len(data) > MAX_BYTES:
        raise ExtractError("PAYLOAD_TOO_LARGE", "文件超过 20MB，请拆分后再传")
    suffix = Path(filename or "").suffix.lower()
    if suffix in TEXT_SUFFIXES:
        all_pages = _pages_text(data)
        kind = "text"
    elif suffix == ".pdf":
        all_pages = _pages_pdf(data)
        kind = "pdf"
    elif suffix == ".docx":
        all_pages = _pages_docx(data)
        kind = "docx"
    elif suffix == ".pptx":
        all_pages = _pages_pptx(data)
        kind = "pptx"
    else:
        raise ExtractError("UNSUPPORTED_FILE_TYPE", "只支持 txt/md/pdf/docx/pptx，其它格式请手动粘贴正文")
    page_count = len(all_pages)
    if not page_count or not any((p or "").strip() for p in all_pages):
        raise ExtractError("UNSUPPORTED_FILE_TYPE", "文件里没有可提取的文字（可能是图片型文件）")
    start = 1 if page_start is None else max(1, int(page_start))
    end = page_count if page_end is None else min(page_count, int(page_end))
    if start > end:
        raise ExtractError("VALIDATION_ERROR", "起止页不合法：起始页大于结束页")
    parts: list[str] = []
    for offset, text in enumerate(all_pages[start - 1:end]):
        body = (text or "").strip()
        if not body:
            continue
        if page_count > 1:
            parts.append("<!-- page %d -->\n\n%s" % (start + offset, body))
        else:
            parts.append(body)
    md = "\n\n".join(parts).strip()
    if not md:
        raise ExtractError("UNSUPPORTED_FILE_TYPE", "选中的页码里没有文字（可能是图片页）")
    truncated = len(md) > MAX_CHARS
    excerpt = md[:MAX_CHARS]
    return {
        "source_file": filename,
        "kind": kind,
        "format": "markdown",
        "page_count": page_count,
        "page_start": start,
        "page_end": end,
        "chars": len(md),
        "excerpt": excerpt,
        "markdown": excerpt,
        "truncated": truncated,
        "suggested_title": Path(filename or "材料").stem[:120],
        "pages": [{"page": i + 1, "chars": len((all_pages[i] or "").strip())} for i in range(page_count)],
    }
