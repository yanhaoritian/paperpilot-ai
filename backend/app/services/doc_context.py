"""Per-document durable context snapshots (stored on documents.context_snapshot)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Block, Chunk, Document


SNAPSHOT_SCHEMA_VERSION = 2

_FACET_LABELS = {
    "objective": "研究目标",
    "method": "方法",
    "data": "数据/样本",
    "metrics": "指标",
    "results": "结果",
    "limitations": "局限",
    "overview": "内容概览",
}
_FACET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "objective": (
        "objective",
        "aim",
        "purpose",
        "research question",
        "we investigate",
        "we study",
        "研究目标",
        "研究目的",
        "研究问题",
        "本文旨在",
        "本文研究",
    ),
    "method": (
        "method",
        "methodology",
        "approach",
        "algorithm",
        "framework",
        "architecture",
        "protocol",
        "procedure",
        "experimental design",
        "inversion",
        "imaging",
        "simulation",
        "方法",
        "算法",
        "框架",
        "架构",
        "流程",
        "实验设计",
        "研究设计",
        "反演",
        "成像",
        "模拟",
    ),
    "data": (
        "dataset",
        "data set",
        "sample",
        "cohort",
        "participant",
        "subject",
        "corpus",
        "benchmark",
        "profile",
        "survey",
        "observation",
        "数据集",
        "数据",
        "样本",
        "队列",
        "受试者",
        "参与者",
        "语料",
        "剖面",
        "地震资料",
        "观测",
    ),
    "metrics": (
        "metric",
        "measure",
        "evaluation",
        "accuracy",
        "precision",
        "recall",
        "f1",
        "auc",
        "指标",
        "评价",
        "评估",
        "准确率",
        "精确率",
        "召回率",
    ),
    "results": (
        "result",
        "finding",
        "performance",
        "outperform",
        "improvement",
        "statistically significant",
        "conclusion",
        "demonstrate",
        "indicate",
        "show",
        "结果",
        "发现",
        "性能",
        "提升",
        "优于",
        "显著",
        "实验表明",
        "结论",
        "表明",
        "显示",
    ),
    "limitations": (
        "limitation",
        "drawback",
        "weakness",
        "threat to validity",
        "future work",
        "remain",
        "uncertainty",
        "assumption",
        "局限",
        "限制",
        "不足",
        "缺陷",
        "有效性威胁",
        "未来工作",
        "有待",
        "不确定性",
        "假设",
    ),
}
_FACET_ORDER = ("method", "data", "results", "limitations", "metrics", "objective")
_NON_SUBSTANTIVE_ROLES = {"title", "section_heading", "section"}
_LOW_VALUE_SECTION_RE = re.compile(
    r"references?|bibliograph|acknowledg|参考文献|致谢",
    re.I,
)


def _field(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clip(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit == 1:
        return "…"
    return value[: limit - 1].rstrip() + "…"


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    haystack = text.lower()
    hits = 0
    for keyword in keywords:
        needle = keyword.lower()
        if any("\u4e00" <= char <= "\u9fff" for char in needle):
            matched = needle in haystack
        else:
            suffix = r"(?:s|es)?" if " " not in needle else ""
            matched = re.search(
                rf"\b{re.escape(needle)}{suffix}\b",
                haystack,
            ) is not None
        if matched:
            hits += 1
    return hits


def _page_label(page_start: int | None, page_end: int | None) -> str:
    if page_start is None:
        return "页码未知"
    if page_end is not None and page_end != page_start:
        return f"p.{page_start}–{page_end}"
    return f"p.{page_start}"


def _candidate_rows(blocks: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        role = str(_field(block, "role", "paragraph") or "paragraph")
        text = _clean_text(_field(block, "text", ""))
        section = _clean_text(_field(block, "section_path", ""))
        if not text or role in _NON_SUBSTANTIVE_ROLES:
            continue
        if _LOW_VALUE_SECTION_RE.search(section):
            continue
        # Keep abstracts compact, but reject heading-like fragments that were
        # misclassified as paragraphs.
        has_cjk = any("\u4e00" <= char <= "\u9fff" for char in text)
        min_chars = 20 if role == "abstract" else 24 if has_cjk else 36
        if len(text) < min_chars:
            continue

        combined = f"{section}\n{text}"
        facet_scores: dict[str, int] = {}
        for facet, keywords in _FACET_KEYWORDS.items():
            text_hits = _keyword_hits(text, keywords)
            section_hits = _keyword_hits(section, keywords)
            if text_hits or section_hits:
                facet_scores[facet] = section_hits * 6 + text_hits * 2

        rows.append(
            {
                "index": index,
                "role": role,
                "text": text,
                "page_start": _field(block, "page_start"),
                "page_end": _field(block, "page_end"),
                "section_path": section or None,
                "facet_scores": facet_scores,
                "quality": (
                    (3 if role in {"paragraph", "table"} else 2 if role == "abstract" else 1)
                    + min(3, len(combined) // 180)
                ),
            }
        )
    return rows


def _pick_evidence_snippets(blocks: list[Any]) -> list[dict[str, Any]]:
    candidates = _candidate_rows(blocks)
    chosen: dict[int, dict[str, Any]] = {}

    for facet in _FACET_ORDER:
        ranked = sorted(
            (row for row in candidates if facet in row["facet_scores"]),
            key=lambda row: (
                row["facet_scores"][facet],
                row["quality"],
                len(row["text"]),
                -int(row["page_start"] or 10**9),
            ),
            reverse=True,
        )
        if not ranked:
            continue
        unused = next((row for row in ranked if row["index"] not in chosen), None)
        selected = unused or ranked[0]
        entry = chosen.setdefault(
            selected["index"],
            {
                "facets": [],
                "text": _clip(selected["text"], 420),
                "page_start": selected["page_start"],
                "page_end": selected["page_end"],
                "section_path": selected["section_path"],
                "role": selected["role"],
            },
        )
        entry["facets"].append(facet)

    if not chosen and candidates:
        fallback = sorted(
            candidates,
            key=lambda row: (
                row["role"] == "abstract",
                row["quality"],
                len(row["text"]),
            ),
            reverse=True,
        )[0]
        chosen[fallback["index"]] = {
            "facets": ["overview"],
            "text": _clip(fallback["text"], 420),
            "page_start": fallback["page_start"],
            "page_end": fallback["page_end"],
            "section_path": fallback["section_path"],
            "role": fallback["role"],
        }

    facet_rank = {facet: index for index, facet in enumerate(_FACET_ORDER)}
    return sorted(
        chosen.values(),
        key=lambda item: min(
            (facet_rank.get(facet, len(facet_rank)) for facet in item["facets"]),
            default=len(facet_rank),
        ),
    )


def _preview_text(
    *,
    title: str | None,
    snippets: list[dict[str, Any]],
    max_chars: int,
) -> str:
    lines: list[str] = []
    if title:
        lines.append(f"[题名] {_clip(title, 180)}")
    remaining = max(0, int(max_chars) - sum(len(line) + 1 for line in lines))
    if not snippets or remaining <= 0:
        return _clip("\n".join(lines), max_chars)

    per_snippet = max(90, remaining // len(snippets))
    for snippet in snippets:
        labels = "/".join(_FACET_LABELS.get(key, key) for key in snippet["facets"])
        provenance = _page_label(snippet.get("page_start"), snippet.get("page_end"))
        if snippet.get("section_path"):
            provenance += f"；{_clip(str(snippet['section_path']), 80)}"
        prefix = f"[{labels} | {provenance}] "
        lines.append(prefix + _clip(str(snippet.get("text") or ""), max(40, per_snippet - len(prefix))))
    return _clip("\n".join(lines), max_chars)


def build_context_snapshot(
    *,
    file_name: str,
    page_count: int,
    blocks: list[Any],
    max_preview_chars: int = 1400,
) -> dict[str, Any]:
    """Build a versioned, facet-aware evidence card from structured blocks."""

    sections: list[str] = []
    section_hints: list[dict[str, Any]] = []
    roles_used: list[str] = []
    title: str | None = None
    for block in blocks:
        role = str(_field(block, "role", "paragraph") or "paragraph")
        text = _clean_text(_field(block, "text", ""))
        if not text:
            continue
        if role not in roles_used:
            roles_used.append(role)
        if title is None and role == "title":
            title = _clip(text, 240)
        if role in {"section_heading", "section"} and len(sections) < 16:
            heading = _clip(text, 120)
            sections.append(heading)
            section_hints.append(
                {
                    "title": heading,
                    "page_start": _field(block, "page_start"),
                }
            )

    snippets = _pick_evidence_snippets(blocks)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "file_name": file_name,
        "page_count": int(page_count or 0),
        "title": title,
        "roles_used": roles_used,
        "sections": sections,
        "section_hints": section_hints,
        "facet_coverage": list(
            dict.fromkeys(
                facet
                for snippet in snippets
                for facet in snippet.get("facets", [])
                if facet != "overview"
            )
        ),
        "evidence_snippets": snippets,
        # Kept for backward-compatible consumers; unlike v1 it contains
        # substantive, provenance-bearing evidence rather than heading lists.
        "preview": _preview_text(
            title=title,
            snippets=snippets,
            max_chars=max_preview_chars,
        )
        or _clip(f"[文件] {file_name}", max_preview_chars),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def snapshot_from_db_blocks(db: Session, document: Document) -> dict[str, Any]:
    blocks = db.scalars(
        select(Block)
        .where(Block.document_id == document.id)
        .order_by(Block.page_start.asc(), Block.id.asc())
    ).all()
    if not blocks:
        # Older installations may have ready documents and searchable chunks
        # but no L2 block rows. Reuse those chunks instead of replacing a useful
        # legacy preview with a filename-only v2 snapshot.
        blocks = db.scalars(
            select(Chunk)
            .where(Chunk.document_id == document.id)
            .order_by(Chunk.chunk_index.asc(), Chunk.id.asc())
        ).all()
    return build_context_snapshot(
        file_name=document.file_name,
        page_count=int(document.page_count or 0),
        blocks=blocks,
    )


def _snapshot_is_current(snapshot: dict[str, Any] | None) -> bool:
    if not snapshot:
        return False
    try:
        version = int(snapshot.get("schema_version") or 0)
    except (TypeError, ValueError):
        return False
    return (
        version == SNAPSHOT_SCHEMA_VERSION
        and isinstance(snapshot.get("evidence_snippets"), list)
        and bool(str(snapshot.get("preview") or "").strip())
    )


def ensure_document_snapshots(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    persist: bool = True,
) -> list[tuple[Document, dict[str, Any]]]:
    """Return ready-document snapshots, rebuilding missing or legacy schemas."""

    if not library_ids:
        return []
    docs = db.scalars(
        select(Document)
        .where(
            Document.owner_id == owner_id,
            Document.library_id.in_(library_ids),
            Document.status == "ready",
        )
        .order_by(Document.created_at.asc())
    ).all()
    out: list[tuple[Document, dict[str, Any]]] = []
    dirty = False
    for doc in docs:
        snap = doc.context_snapshot if isinstance(doc.context_snapshot, dict) else None
        if not _snapshot_is_current(snap):
            rebuilt = snapshot_from_db_blocks(db, doc)
            if (
                not rebuilt.get("evidence_snippets")
                and snap
                and str(snap.get("preview") or "").strip()
            ):
                # Truly sparse legacy rows still retain their last known useful
                # preview. The v2 marker prevents repeated rebuild work; a later
                # successful re-index writes a fresh facet-aware snapshot.
                rebuilt["preview"] = str(snap["preview"])
                legacy_sections = snap.get("sections")
                legacy_roles = snap.get("roles_used")
                rebuilt["sections"] = (
                    list(legacy_sections) if isinstance(legacy_sections, list) else []
                )
                rebuilt["roles_used"] = (
                    list(legacy_roles) if isinstance(legacy_roles, list) else []
                )
                rebuilt["legacy_preview_preserved"] = True
            snap = rebuilt
            if persist:
                doc.context_snapshot = snap
                dirty = True
        out.append((doc, snap))
    if dirty:
        db.commit()
    return out


def _format_v2_snippets(snapshot: dict[str, Any], *, budget: int) -> list[str]:
    snippets = snapshot.get("evidence_snippets")
    if not isinstance(snippets, list) or not snippets:
        return []
    # One line per facet-bearing snippet, with an equal share of the card.
    lines: list[str] = []
    selected = [snippet for snippet in snippets[:4] if isinstance(snippet, dict)]
    if not selected or budget <= 0:
        return []
    per_line = max(1, (budget - max(0, len(selected) - 1)) // len(selected))
    for snippet in selected:
        facets = snippet.get("facets") if isinstance(snippet.get("facets"), list) else []
        label = "/".join(_FACET_LABELS.get(str(facet), str(facet)) for facet in facets) or "证据"
        provenance = _page_label(snippet.get("page_start"), snippet.get("page_end"))
        section = _clean_text(snippet.get("section_path"))
        if section:
            provenance += f"；{_clip(section, 48)}"
        prefix = f"- {label}（{provenance}）："
        lines.append(_clip(prefix + str(snippet.get("text") or ""), per_line))
    return lines


def format_document_context_cards(
    pairs: list[tuple[Document, dict[str, Any]]],
    *,
    max_chars: int = 5_000,
) -> str:
    """Format compact cards with an equal character budget for every document."""

    if not pairs:
        return "文献 Context 卡片：（无）"
    header = f"文献 Context 卡片（每篇均衡抽样，共 {len(pairs)} 篇）："
    separators = 2 * max(0, len(pairs) - 1)
    available = max(0, int(max_chars) - len(header) - separators - 1)
    per_card = max(1, available // len(pairs))
    cards: list[str] = []

    for index, (doc, snapshot) in enumerate(pairs, 1):
        name = _clip(str(doc.file_name or "未命名文献"), 140)
        page_count = snapshot.get("page_count") or doc.page_count or 0
        sections = snapshot.get("sections") if isinstance(snapshot.get("sections"), list) else []
        section_line = "；".join(_clip(str(section), 32) for section in sections[:2])
        base = [
            f"### 卡片 {index}: 《{name}》",
            f"- document_id={doc.id}；约 {page_count} 页",
        ]
        if section_line:
            base.append(f"- 章节：{section_line}")

        used = sum(len(line) + 1 for line in base)
        evidence_lines = _format_v2_snippets(
            snapshot,
            budget=max(0, per_card - used),
        )
        if not evidence_lines:
            preview = str(snapshot.get("preview") or "").strip() or "（暂无实质片段，请重新索引）"
            evidence_lines = [f"- 摘要：{_clip(preview, max(40, per_card - used - 5))}"]
        cards.append(_clip("\n".join(base + evidence_lines), per_card))

    return _clip(header + "\n" + "\n\n".join(cards), max_chars)
