"""Per-document durable context snapshots (stored on documents.context_snapshot)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Block, Document


def build_context_snapshot(
    *,
    file_name: str,
    page_count: int,
    blocks: list[Any],
    max_preview_chars: int = 1400,
) -> dict[str, Any]:
    """Build a compact card from structured blocks (title/abstract/early paragraphs)."""
    role_priority = {"title": 0, "abstract": 1, "section": 2, "paragraph": 3, "caption": 4}
    ordered = sorted(
        blocks,
        key=lambda b: (
            role_priority.get(getattr(b, "role", None) or (b.get("role") if isinstance(b, dict) else None) or "paragraph", 9),
            getattr(b, "page_start", None) or (b.get("page_start") if isinstance(b, dict) else None) or 10**9,
        ),
    )
    sections: list[str] = []
    preview_parts: list[str] = []
    roles_used: list[str] = []
    for b in ordered:
        role = getattr(b, "role", None) or (b.get("role") if isinstance(b, dict) else None) or "paragraph"
        text = (getattr(b, "text", None) or (b.get("text") if isinstance(b, dict) else "") or "").strip()
        if not text:
            continue
        if role not in roles_used:
            roles_used.append(role)
        if role == "section" and len(sections) < 12:
            sections.append(text[:120])
        if role in {"title", "abstract", "paragraph", "section"} and sum(len(p) for p in preview_parts) < max_preview_chars:
            preview_parts.append(f"[{role}] {text[:500]}")
        if sum(len(p) for p in preview_parts) >= max_preview_chars:
            break

    preview = "\n".join(preview_parts)
    if len(preview) > max_preview_chars:
        preview = preview[: max_preview_chars - 1] + "…"

    return {
        "file_name": file_name,
        "page_count": int(page_count or 0),
        "roles_used": roles_used,
        "sections": sections,
        "preview": preview,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def snapshot_from_db_blocks(db: Session, document: Document) -> dict[str, Any]:
    blocks = db.scalars(
        select(Block)
        .where(Block.document_id == document.id)
        .order_by(Block.page_start.asc())
        .limit(80)
    ).all()
    return build_context_snapshot(
        file_name=document.file_name,
        page_count=int(document.page_count or 0),
        blocks=blocks,
    )


def ensure_document_snapshots(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    persist: bool = True,
) -> list[tuple[Document, dict[str, Any]]]:
    """Return (doc, snapshot) for ready docs; backfill missing snapshots from blocks."""
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
        if not snap or not str(snap.get("preview") or "").strip():
            snap = snapshot_from_db_blocks(db, doc)
            if persist:
                doc.context_snapshot = snap
                dirty = True
        out.append((doc, snap))
    if dirty:
        db.commit()
    return out


def format_document_context_cards(pairs: list[tuple[Document, dict[str, Any]]]) -> str:
    if not pairs:
        return "文献 Context 卡片：（无）"
    lines = [f"文献 Context 卡片（每篇入库文档持久摘要，共 {len(pairs)} 篇）："]
    for i, (doc, snap) in enumerate(pairs, 1):
        preview = str(snap.get("preview") or "").strip() or "（暂无预览，请重新索引）"
        sections = snap.get("sections") or []
        sec_line = "；".join(sections[:8]) if sections else "（未抽取章节）"
        lines.append(
            f"### 卡片 {i}: 《{doc.file_name}》\n"
            f"- document_id={doc.id}\n"
            f"- 页数约 {snap.get('page_count') or doc.page_count or 0}\n"
            f"- 章节线索：{sec_line}\n"
            f"- 预览：\n{preview}"
        )
    return "\n\n".join(lines)
