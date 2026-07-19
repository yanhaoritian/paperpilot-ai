from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Block, Chunk, Document
from app.services.doc_context import ensure_document_snapshots, format_document_context_cards
from app.services.generate import citations_from_retrieved, empty_answer
from app.services.hybrid_retrieve import hybrid_retrieve
from app.services.intent import (
    QueryIntent,
    detect_intent,
    format_catalog_answer,
    format_inventory_block,
    list_library_documents,
)
from app.services.openai_client import chat_json, chat_stream
from app.services.pdf_parse import parse_pdf_pages
from app.services.prompts import RAG_AGENT_FINAL_SYSTEM
from app.services.structure import extract_tables_from_page_text
from pathlib import Path

logger = logging.getLogger(__name__)


def search_pdf(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    query: str,
    top_k: int | None = None,
) -> dict[str, Any]:
    rows = hybrid_retrieve(
        db,
        owner_id=owner_id,
        library_ids=library_ids,
        question=query,
        top_k=top_k,
    )
    hits = [
        {
            "chunk_id": r.chunk_id,
            "document_id": r.document_id,
            "file_name": r.file_name,
            "page_start": r.page_start,
            "page_end": r.page_end,
            "section_path": r.section_path,
            "role": r.role,
            "excerpt": r.text[:400],
            "score": r.score,
        }
        for r in rows
    ]
    return {"ok": True, "hits": hits, "count": len(hits)}


def read_page(
    db: Session,
    *,
    owner_id: str,
    document_id: str,
    page: int,
) -> dict[str, Any]:
    doc = db.scalar(
        select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
    )
    if not doc:
        return {"ok": False, "error": "文档不存在"}
    blocks = db.scalars(
        select(Block)
        .where(
            Block.document_id == document_id,
            Block.owner_id == owner_id,
            Block.page_start <= page,
            Block.page_end >= page,
        )
        .order_by(Block.page_start.asc())
    ).all()
    if blocks:
        text = "\n\n".join(f"[{b.role}] {b.text}" for b in blocks)
        return {
            "ok": True,
            "document_id": document_id,
            "file_name": doc.file_name,
            "page": page,
            "text": text[:8000],
            "block_count": len(blocks),
        }
    try:
        data = Path(doc.file_path).read_bytes()
        parse = parse_pdf_pages(data)
        page_bundle = next((p for p in parse.pages if p.page_no == page), None)
        if not page_bundle:
            return {"ok": False, "error": "页码不存在"}
        return {
            "ok": True,
            "document_id": document_id,
            "file_name": doc.file_name,
            "page": page,
            "text": page_bundle.text[:8000],
            "route": page_bundle.route,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def extract_table(
    db: Session,
    *,
    owner_id: str,
    document_id: str,
    page: int,
) -> dict[str, Any]:
    page_res = read_page(db, owner_id=owner_id, document_id=document_id, page=page)
    if not page_res.get("ok"):
        return page_res
    tables = extract_tables_from_page_text(str(page_res.get("text") or ""))
    table_blocks = db.scalars(
        select(Block).where(
            Block.document_id == document_id,
            Block.owner_id == owner_id,
            Block.page_start <= page,
            Block.page_end >= page,
            Block.role == "table",
        )
    ).all()
    for b in table_blocks:
        tables.append(b.text)
    return {
        "ok": True,
        "document_id": document_id,
        "page": page,
        "tables": tables[:10],
        "count": len(tables),
    }


def analyze_chart(
    db: Session,
    *,
    owner_id: str,
    document_id: str,
    page: int,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.vision_enabled:
        caps = db.scalars(
            select(Block).where(
                Block.document_id == document_id,
                Block.owner_id == owner_id,
                Block.page_start <= page,
                Block.page_end >= page,
                Block.role == "caption",
            )
        ).all()
        return {
            "ok": True,
            "document_id": document_id,
            "page": page,
            "description": "\n".join(c.text for c in caps)
            or "未启用视觉分析；请开启 VISION_ENABLED 或查看图注。",
            "mode": "caption_fallback",
        }
    page_res = read_page(db, owner_id=owner_id, document_id=document_id, page=page)
    if not page_res.get("ok"):
        return page_res
    return {
        "ok": True,
        "document_id": document_id,
        "page": page,
        "description": str(page_res.get("text") or "")[:2000],
        "mode": "page_text",
    }


def quote_source(
    db: Session,
    *,
    owner_id: str,
    chunk_id: str | None = None,
    document_id: str | None = None,
    page: int | None = None,
    excerpt: str | None = None,
) -> dict[str, Any]:
    if chunk_id:
        row = db.execute(
            select(Chunk, Document.file_name)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id == chunk_id, Chunk.owner_id == owner_id)
        ).first()
        if not row:
            return {"ok": False, "error": "chunk 不存在"}
        chunk, file_name = row
        text = chunk.text
        if excerpt and excerpt in text:
            text = excerpt
        return {
            "ok": True,
            "citation": {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "file_name": file_name,
                "library_id": chunk.library_id,
                "excerpt": text[:500],
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "section_path": chunk.section_path,
            },
        }
    if document_id and page is not None:
        page_res = read_page(db, owner_id=owner_id, document_id=document_id, page=page)
        if not page_res.get("ok"):
            return page_res
        return {
            "ok": True,
            "citation": {
                "chunk_id": "",
                "document_id": document_id,
                "file_name": page_res.get("file_name"),
                "excerpt": (excerpt or str(page_res.get("text") or ""))[:500],
                "page_start": page,
                "page_end": page,
            },
        }
    return {"ok": False, "error": "需要 chunk_id 或 document_id+page"}


TOOL_SPECS = [
    {"name": "search_pdf", "description": "混合检索论文片段", "parameters": {"query": "string"}},
    {"name": "read_page", "description": "读指定页", "parameters": {"document_id": "string", "page": "int"}},
    {"name": "extract_table", "description": "抽表格", "parameters": {"document_id": "string", "page": "int"}},
    {"name": "analyze_chart", "description": "分析图表", "parameters": {"document_id": "string", "page": "int"}},
    {"name": "quote_source", "description": "固化引用", "parameters": {"chunk_id": "string?"}},
]


def run_tool(
    db: Session,
    name: str,
    args: dict[str, Any],
    *,
    owner_id: str,
    library_ids: list[str],
) -> dict[str, Any]:
    name = (name or "").strip()
    args = args or {}
    if name == "search_pdf":
        return search_pdf(
            db,
            owner_id=owner_id,
            library_ids=library_ids,
            query=str(args.get("query") or ""),
            top_k=args.get("top_k"),
        )
    if name == "read_page":
        return read_page(
            db,
            owner_id=owner_id,
            document_id=str(args.get("document_id") or ""),
            page=int(args.get("page") or 1),
        )
    if name == "extract_table":
        return extract_table(
            db,
            owner_id=owner_id,
            document_id=str(args.get("document_id") or ""),
            page=int(args.get("page") or 1),
        )
    if name == "analyze_chart":
        return analyze_chart(
            db,
            owner_id=owner_id,
            document_id=str(args.get("document_id") or ""),
            page=int(args.get("page") or 1),
        )
    if name == "quote_source":
        return quote_source(
            db,
            owner_id=owner_id,
            chunk_id=args.get("chunk_id"),
            document_id=args.get("document_id"),
            page=args.get("page"),
            excerpt=args.get("excerpt"),
        )
    return {"ok": False, "error": f"未知工具: {name}"}


def plan_tools(question: str, *, library_ids: list[str]) -> list[dict[str, Any]]:
    settings = get_settings()
    fallback = [{"name": "search_pdf", "arguments": {"query": question}}]
    if not settings.openai_api_key:
        return fallback
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是论文知识库 Agent 规划器。可选工具: search_pdf, read_page, extract_table, analyze_chart, quote_source。"
                    "通常先 search_pdf。只输出 JSON: {\"steps\":[{\"name\":\"...\",\"arguments\":{...}}]}，"
                    f"最多 {settings.agent_max_tool_rounds} 步。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"question": question, "library_ids": library_ids, "tools": TOOL_SPECS},
                    ensure_ascii=False,
                ),
            },
        ]
        raw = chat_json(messages, temperature=0.1)
        steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
        cleaned: list[dict[str, Any]] = []
        for step in steps[: settings.agent_max_tool_rounds]:
            if not isinstance(step, dict):
                continue
            name = str(step.get("name") or "").strip()
            args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
            if name:
                cleaned.append({"name": name, "arguments": args})
        return cleaned or fallback
    except Exception:  # noqa: BLE001
        logger.exception("plan_tools failed")
        return fallback


def _tool_summary(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return str(result.get("error") or "失败")[:120]
    if "hits" in result:
        return f"命中 {result.get('count', 0)} 条"
    if "tables" in result:
        return f"表格 {result.get('count', 0)} 个"
    if "text" in result:
        return f"已读第 {result.get('page')} 页"
    if "description" in result:
        return "已生成图表描述"
    if "citation" in result:
        return "已固化引用"
    return "完成"


def iter_agent_events(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    question: str,
    history: list[dict[str, str]] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (event_name, payload). Final event is 'final' with answer/citations meta."""
    settings = get_settings()
    intent = detect_intent(question)
    inventory = list_library_documents(db, owner_id=owner_id, library_ids=library_ids)
    inventory_text = format_inventory_block(inventory)
    cards = ensure_document_snapshots(db, owner_id=owner_id, library_ids=library_ids)
    cards_text = format_document_context_cards(cards)

    if intent == QueryIntent.CATALOG:
        ans = format_catalog_answer(inventory)
        yield ("status", {"phase": "catalog", "text": "正在读取知识库文献清单…"})
        yield ("token", {"text": ans})
        yield (
            "final",
            {
                "answer": ans,
                "citations": [],
                "retrieval_hit": len(inventory),
                "degraded": False,
                "confidence": "high",
            },
        )
        return

    compare = intent == QueryIntent.COMPARE
    # Compare / small libraries: hybrid + full-doc coverage (skip agent tool planner)
    use_hybrid = (not settings.agent_enabled) or compare or len(inventory) <= 3
    if use_hybrid:
        yield (
            "status",
            {
                "phase": "retrieving",
                "text": "正在混合检索（多文献覆盖）…" if compare else "正在混合检索…",
            },
        )
        rows = hybrid_retrieve(
            db,
            owner_id=owner_id,
            library_ids=library_ids,
            question=question,
            cover_all_docs=compare or len(inventory) <= 3,
            top_k=max(settings.rag_top_k, 8) if compare else None,
        )
        if not rows:
            ans = empty_answer()["answer"]
            yield ("token", {"text": ans})
            yield (
                "final",
                {"answer": ans, "citations": [], "retrieval_hit": 0, "degraded": True, "confidence": "low"},
            )
            return
        yield ("meta", {"retrieval_hit": len(rows)})
        yield (
            "status",
            {"phase": "generating", "text": f"已命中 {len(rows)} 段（覆盖多篇文献），正在生成…"},
        )
        from app.services.generate import build_rag_stream_messages

        intent_hint = (
            "这是跨文献对比/共同点问题：必须覆盖文献清单中的各篇；"
            "禁止声称只检索到一篇；某篇证据不足时单独说明，不得否认该篇在库中。"
            "排版：先自然段总述，异同处可用 Markdown 表格，最后一段小结；不要用 --- 装饰线。"
            if compare
            else None
        )
        messages = build_rag_stream_messages(
            question,
            rows,
            history=history,
            inventory_text=inventory_text,
            document_cards_text=cards_text,
            intent_hint=intent_hint,
        )
        parts: list[str] = []
        for tok in chat_stream(messages, model=model, temperature=temperature):
            parts.append(tok)
            yield ("token", {"text": tok})
        answer = "".join(parts).strip()
        cites = citations_from_retrieved(rows, limit=max(6, len(rows)))
        yield (
            "final",
            {
                "answer": answer,
                "citations": cites,
                "retrieval_hit": len(rows),
                "degraded": False,
                "confidence": "medium",
            },
        )
        return

    steps = plan_tools(question, library_ids=library_ids)
    yield ("status", {"phase": "planning", "text": f"已规划 {len(steps)} 个工具步骤"})
    evidence: list[dict[str, Any]] = []
    citations_acc: list[dict[str, Any]] = []
    pages_read = 0

    for step in steps:
        name = step["name"]
        args = step.get("arguments") or {}
        yield ("tool", {"name": name, "arguments": args, "phase": "start"})
        if name in {"read_page", "extract_table", "analyze_chart"}:
            pages_read += 1
            if pages_read > settings.agent_max_pages_read:
                result: dict[str, Any] = {"ok": False, "error": "已达最大读页次数"}
            else:
                result = run_tool(db, name, args, owner_id=owner_id, library_ids=library_ids)
        else:
            result = run_tool(db, name, args, owner_id=owner_id, library_ids=library_ids)
        evidence.append({"tool": name, "arguments": args, "result": result})
        yield (
            "tool",
            {
                "name": name,
                "phase": "done",
                "ok": bool(result.get("ok")),
                "summary": _tool_summary(result),
            },
        )
        if name == "search_pdf":
            for h in (result.get("hits") or [])[:6]:
                citations_acc.append(
                    {
                        "chunk_id": h.get("chunk_id"),
                        "document_id": h.get("document_id"),
                        "file_name": h.get("file_name"),
                        "excerpt": h.get("excerpt"),
                        "page_start": h.get("page_start"),
                        "page_end": h.get("page_end"),
                        "score": h.get("score"),
                        "section_path": h.get("section_path"),
                    }
                )
        if name == "quote_source" and result.get("citation"):
            citations_acc.append(result["citation"])

    if not evidence:
        ans = empty_answer()["answer"]
        yield ("token", {"text": ans})
        yield (
            "final",
            {"answer": ans, "citations": [], "retrieval_hit": 0, "degraded": True, "confidence": "low"},
        )
        return

    yield ("status", {"phase": "generating", "text": "正在根据工具证据生成回答…"})
    messages: list[dict[str, str]] = [{"role": "system", "content": RAG_AGENT_FINAL_SYSTEM}]
    if history:
        for turn in history[-6:]:
            if turn.get("role") in {"user", "assistant"} and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append(
        {
            "role": "user",
            "content": (
                f"问题：{question}\n\n{inventory_text}\n\n{cards_text}\n\n"
                f"工具证据：\n{json.dumps(evidence, ensure_ascii=False)[:12000]}"
            ),
        }
    )
    parts = []
    for tok in chat_stream(messages, model=model, temperature=temperature):
        parts.append(tok)
        yield ("token", {"text": tok})
    answer = "".join(parts).strip() or "无法从文献中得出可靠结论。"
    yield (
        "final",
        {
            "answer": answer,
            "citations": citations_acc[:8],
            "retrieval_hit": len(citations_acc),
            "degraded": False,
            "confidence": "medium",
        },
    )
