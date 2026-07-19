from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import uuid4

from app.services.pdf_parse import LayoutSpan, PageBundle, ParseResult

HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[\.\)]\s+|[IVXLC]+\.\s+|第[一二三四五六七八九十百]+[章节部]\s*|"
    r"Abstract|Introduction|Methods?|Results?|Discussion|Conclusion|References|Related Work|"
    r"摘要|引言|方法|结果|讨论|结论|参考文献)\b",
    re.I,
)
CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table|图|表)\s*[\dA-Za-z]", re.I)
FOOTNOTE_RE = re.compile(r"^(\*+|†+|‡+|\d+)\s+")


@dataclass
class StructuredBlock:
    block_id: str
    page_start: int
    page_end: int
    role: str  # title | section_heading | paragraph | caption | table | footnote | other
    text: str
    section_path: str = ""
    bbox: list[float] | None = None
    extra: dict = field(default_factory=dict)  # clause_id / metric_key reserved


def _role_for_span(text: str, font_size: float | None, median_size: float) -> str:
    t = text.strip()
    if not t:
        return "other"
    if CAPTION_RE.match(t):
        return "caption"
    if FOOTNOTE_RE.match(t) and len(t) < 240:
        return "footnote"
    if HEADING_RE.match(t) or (font_size and median_size and font_size >= median_size * 1.25 and len(t) < 160):
        return "section_heading"
    if font_size and median_size and font_size >= median_size * 1.55 and len(t) < 120:
        return "title"
    if "|" in t and t.count("|") >= 2:
        return "table"
    return "paragraph"


def _median(vals: list[float]) -> float:
    if not vals:
        return 11.0
    s = sorted(vals)
    return s[len(s) // 2]


def restore_structure(parse: ParseResult, *, file_name: str = "") -> list[StructuredBlock]:
    """L2: heuristic structure restore with real page numbers + section_path."""
    blocks: list[StructuredBlock] = []
    section_stack: list[str] = []
    title_set = False

    for page in parse.pages:
        page_blocks = _blocks_from_page(page)
        for b in page_blocks:
            role = b.role
            if role == "title" and title_set:
                role = "section_heading"
            if role == "title":
                title_set = True
                section_stack = [b.text[:80]]
            elif role == "section_heading":
                section_stack = (section_stack[:1] if section_stack else []) + [b.text[:80]]
            path = " / ".join(section_stack) if section_stack else (file_name or "")
            b.section_path = path
            b.role = role
            # reserved compliance keys
            b.extra.setdefault("clause_id", None)
            b.extra.setdefault("metric_key", None)
            blocks.append(b)

    if not blocks:
        # fallback: one paragraph block per page with text
        for page in parse.pages:
            if not page.text.strip():
                continue
            blocks.append(
                StructuredBlock(
                    block_id=str(uuid4()),
                    page_start=page.page_no,
                    page_end=page.page_no,
                    role="paragraph",
                    text=page.text.strip(),
                    section_path=file_name or "",
                    extra={"clause_id": None, "metric_key": None, "route": page.route},
                )
            )
    return blocks


def _blocks_from_page(page: PageBundle) -> list[StructuredBlock]:
    out: list[StructuredBlock] = []
    if page.spans:
        sizes = [s.font_size for s in page.spans if s.font_size]
        med = _median([float(x) for x in sizes if x])
        # merge consecutive spans of same role into paragraphs
        buf_text: list[str] = []
        buf_role: str | None = None
        buf_bbox: list[float] | None = None

        def flush() -> None:
            nonlocal buf_text, buf_role, buf_bbox
            if not buf_text or not buf_role:
                buf_text, buf_role, buf_bbox = [], None, None
                return
            text = " ".join(buf_text).strip()
            if text:
                out.append(
                    StructuredBlock(
                        block_id=str(uuid4()),
                        page_start=page.page_no,
                        page_end=page.page_no,
                        role=buf_role,
                        text=text,
                        bbox=buf_bbox,
                        extra={"route": page.route},
                    )
                )
            buf_text, buf_role, buf_bbox = [], None, None

        for span in page.spans:
            role = _role_for_span(span.text, span.font_size, med)
            if role in {"title", "section_heading", "caption", "footnote", "table"}:
                flush()
                out.append(
                    StructuredBlock(
                        block_id=str(uuid4()),
                        page_start=page.page_no,
                        page_end=page.page_no,
                        role=role,
                        text=span.text.strip(),
                        bbox=span.bbox,
                        extra={"route": page.route},
                    )
                )
                continue
            if buf_role and buf_role != role:
                flush()
            buf_role = role
            buf_text.append(span.text)
            if span.bbox:
                buf_bbox = span.bbox
        flush()
    else:
        # OCR / vision pages: split by blank lines
        paras = [p.strip() for p in re.split(r"\n\s*\n", page.text) if p.strip()]
        for para in paras:
            role = "caption" if CAPTION_RE.match(para) else (
                "section_heading" if HEADING_RE.match(para) else "paragraph"
            )
            if para.startswith("[图/版面视觉描述]"):
                role = "caption"
            out.append(
                StructuredBlock(
                    block_id=str(uuid4()),
                    page_start=page.page_no,
                    page_end=page.page_no,
                    role=role,
                    text=para,
                    extra={"route": page.route},
                )
            )

    # attach vision descriptions as caption blocks
    for im in page.images:
        if im.description:
            out.append(
                StructuredBlock(
                    block_id=str(uuid4()),
                    page_start=page.page_no,
                    page_end=page.page_no,
                    role="caption",
                    text=im.description.strip(),
                    bbox=im.bbox,
                    extra={"route": "vision", "image_index": im.index},
                )
            )
    return out


def extract_tables_from_page_text(text: str) -> list[str]:
    """Lightweight pipe/whitespace table extraction → markdown rows."""
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    tables: list[str] = []
    buf: list[str] = []
    for ln in lines:
        if "|" in ln and ln.count("|") >= 2:
            cells = [c.strip() for c in ln.split("|") if c.strip()]
            if len(cells) >= 2:
                buf.append("| " + " | ".join(cells) + " |")
                continue
        if buf:
            tables.append("\n".join(buf))
            buf = []
    if buf:
        tables.append("\n".join(buf))
    return tables
