from __future__ import annotations

import json
import re
from typing import Any

from app.services.openai_client import chat_json
from app.services.prompts import RAG_JSON_SYSTEM, RAG_STREAM_SYSTEM
from app.services.retrieve import RetrievedChunk


def _format_context_block(row: RetrievedChunk, *, include_ids: bool) -> str:
    page_line = (
        f"页码约 {row.page_start}"
        + (f"–{row.page_end}" if row.page_end is not None and row.page_end != row.page_start else "")
        if row.page_start is not None
        else "页码不可用"
    )
    lines = []
    if include_ids:
        lines.append(f"[chunk_id={row.chunk_id}]")
    lines.append(f"[文献={row.file_name}]")
    if include_ids:
        lines.append(f"[library_id={row.library_id}]")
        lines.append(f"[段落序号={row.chunk_index}]")
    section = getattr(row, "section_path", None)
    if section:
        lines.append(f"[章节={section}]")
    lines.append(f"[{page_line}]")
    lines.append(row.text)
    return "\n".join(lines)


def _format_context_grouped(retrieved: list[RetrievedChunk], *, include_ids: bool) -> str:
    """Group retrieval hits by document so each paper has a clear Context section."""
    if not retrieved:
        return "（空：无可用检索片段）"
    by_doc: dict[str, list[RetrievedChunk]] = {}
    order: list[str] = []
    for row in retrieved:
        if row.document_id not in by_doc:
            by_doc[row.document_id] = []
            order.append(row.document_id)
        by_doc[row.document_id].append(row)
    sections: list[str] = []
    for i, did in enumerate(order, 1):
        rows = by_doc[did]
        name = rows[0].file_name
        body = "\n\n".join(_format_context_block(r, include_ids=include_ids) for r in rows)
        sections.append(f"#### 文献 {i}: 《{name}》（document_id={did}，片段数={len(rows)}）\n{body}")
    return "\n\n==========\n\n".join(sections)


def _context_source_summary(retrieved: list[RetrievedChunk]) -> str:
    names = list(dict.fromkeys(r.file_name for r in retrieved if r.file_name))
    if not names:
        return "本次 Context 覆盖的文献文件：（无）"
    return "本次 Context 覆盖的文献文件（共 {n} 篇）：{files}".format(
        n=len(names),
        files="；".join(f"《{name}》" for name in names),
    )


def _build_user_payload(
    question: str,
    retrieved: list[RetrievedChunk],
    *,
    include_ids: bool,
    inventory_text: str | None = None,
    intent_hint: str | None = None,
    document_cards_text: str | None = None,
) -> str:
    context = _format_context_grouped(retrieved, include_ids=include_ids)
    parts = [f"当前问题：{question}"]
    if intent_hint:
        parts.extend(["", f"意图提示：{intent_hint}"])
    if inventory_text:
        parts.extend(["", inventory_text])
    if document_cards_text:
        parts.extend(["", document_cards_text])
    parts.extend(
        [
            "",
            _context_source_summary(retrieved),
            "",
            "Context（按文献分组；各组之外的内容不得当作依据）：",
            context,
        ]
    )
    return "\n".join(parts)


def build_rag_messages(
    question: str,
    retrieved: list[RetrievedChunk],
    *,
    inventory_text: str | None = None,
    intent_hint: str | None = None,
    document_cards_text: str | None = None,
) -> list[dict[str, str]]:
    user = _build_user_payload(
        question,
        retrieved,
        include_ids=True,
        inventory_text=inventory_text,
        intent_hint=intent_hint,
        document_cards_text=document_cards_text,
    )
    return [{"role": "system", "content": RAG_JSON_SYSTEM}, {"role": "user", "content": user}]


def build_rag_stream_messages(
    question: str,
    retrieved: list[RetrievedChunk],
    *,
    history: list[dict[str, str]] | None = None,
    inventory_text: str | None = None,
    intent_hint: str | None = None,
    document_cards_text: str | None = None,
) -> list[dict[str, str]]:
    """Messages for plain-text streaming answers (citations attached separately)."""
    messages: list[dict[str, str]] = [{"role": "system", "content": RAG_STREAM_SYSTEM}]
    if history:
        for turn in history:
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": _build_user_payload(
                question,
                retrieved,
                include_ids=False,
                inventory_text=inventory_text,
                intent_hint=intent_hint,
                document_cards_text=document_cards_text,
            ),
        }
    )
    return messages


def citations_from_retrieved(retrieved: list[RetrievedChunk], *, limit: int = 4) -> list[dict[str, Any]]:
    """Rule-based citations from retrieval hits (used after streaming)."""
    out: list[dict[str, Any]] = []
    for row in retrieved[:limit]:
        out.append(
            {
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "file_name": row.file_name,
                "library_id": row.library_id,
                "excerpt": (row.text or "")[:400],
                "page_start": row.page_start,
                "page_end": row.page_end,
                "score": row.score,
                "section_path": getattr(row, "section_path", None),
            }
        )
    return out


def empty_answer(reason: str = "所选知识库中未检索到足够相关的原文片段，无法从文献中得出可靠结论。") -> dict[str, Any]:
    return {
        "answer": reason,
        "citations": [],
        "confidence": "low",
        "out_of_scope": False,
        "retrieval_hit": 0,
        "degraded": True,
    }


def generate_answer(
    question: str,
    retrieved: list[RetrievedChunk],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    inventory_text: str | None = None,
    intent_hint: str | None = None,
    document_cards_text: str | None = None,
) -> dict[str, Any]:
    if not retrieved:
        return empty_answer()

    by_id = {r.chunk_id: r for r in retrieved}
    raw = chat_json(
        build_rag_messages(
            question,
            retrieved,
            inventory_text=inventory_text,
            intent_hint=intent_hint,
            document_cards_text=document_cards_text,
        ),
        model=model,
        temperature=temperature,
    )

    citations_in = raw.get("citations") if isinstance(raw.get("citations"), list) else []
    citations: list[dict[str, Any]] = []
    for c in citations_in:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("chunk_id") or "").strip()
        if cid not in by_id:
            continue
        src = by_id[cid]
        excerpt = str(c.get("excerpt") or "").strip()
        if not excerpt:
            excerpt = src.text[:240]
        # Prefer substring of source text
        if excerpt not in src.text:
            # try shorten to first sentence-like slice present in source
            for n in (180, 120, 80):
                candidate = src.text[:n]
                if candidate:
                    excerpt = candidate
                    break
        citations.append(
            {
                "chunk_id": cid,
                "document_id": src.document_id,
                "file_name": src.file_name,
                "library_id": src.library_id,
                "excerpt": excerpt[:500],
                "page_start": c.get("page_start", src.page_start),
                "page_end": c.get("page_end", src.page_end),
                "score": src.score,
                "section_path": getattr(src, "section_path", None),
            }
        )

    answer = str(raw.get("answer") or "").strip()
    if not answer:
        answer = "无法从文献中得出可靠结论。"

    return {
        "answer": answer,
        "citations": citations,
        "confidence": str(raw.get("confidence") or "medium"),
        "out_of_scope": bool(raw.get("out_of_scope")),
        "retrieval_hit": len(retrieved),
        "degraded": False,
    }


def extract_json_loose(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))
