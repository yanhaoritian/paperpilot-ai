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
from app.services.conversation_memory import (
    ConversationContext,
    format_conversation_context,
    prepare_conversation_context,
)
from app.services.document_storage import resolve_document_path
from app.services.generate import citations_from_retrieved, empty_answer
from app.services.hybrid_retrieve import (
    comparison_evidence_coverage,
    format_comparison_evidence_coverage,
    hybrid_retrieve,
)
from app.services.intent import (
    QueryIntent,
    detect_intent,
    format_catalog_answer,
    format_inventory_block,
    list_library_documents,
)
from app.services.openai_client import chat_json, chat_stream
from app.services.prompt_budget import budget_history, clip_text
from app.services.pdf_parse import parse_pdf_pages
from app.services.prompts import RAG_AGENT_FINAL_SYSTEM
from app.services.research_skills import (
    comparison_answer_violations,
    sanitize_comparison_answer,
)
from app.services.structure import extract_tables_from_page_text

logger = logging.getLogger(__name__)


def _document_in_scope(
    db: Session,
    *,
    owner_id: str,
    document_id: str,
    library_ids: list[str] | None,
) -> Document | None:
    filters = [Document.id == document_id, Document.owner_id == owner_id]
    if library_ids is not None:
        scoped = list(dict.fromkeys(str(x) for x in library_ids if x))
        if not scoped:
            return None
        filters.append(Document.library_id.in_(scoped))
    return db.scalar(select(Document).where(*filters))


def search_pdf(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    query: str,
    top_k: int | None = None,
    query_vector: list[float] | None = None,
) -> dict[str, Any]:
    rows = hybrid_retrieve(
        db,
        owner_id=owner_id,
        library_ids=library_ids,
        question=query,
        top_k=top_k,
        query_vector=query_vector,
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
    library_ids: list[str] | None = None,
) -> dict[str, Any]:
    doc = _document_in_scope(
        db,
        owner_id=owner_id,
        document_id=document_id,
        library_ids=library_ids,
    )
    if not doc:
        return {"ok": False, "error": "文档不存在或不在当前所选知识库"}
    if page < 1 or (int(doc.page_count or 0) > 0 and page > int(doc.page_count)):
        return {"ok": False, "error": "页码不存在"}
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
        data = resolve_document_path(doc).read_bytes()
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
    library_ids: list[str] | None = None,
) -> dict[str, Any]:
    page_res = read_page(
        db,
        owner_id=owner_id,
        document_id=document_id,
        page=page,
        library_ids=library_ids,
    )
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
    library_ids: list[str] | None = None,
) -> dict[str, Any]:
    doc = _document_in_scope(
        db,
        owner_id=owner_id,
        document_id=document_id,
        library_ids=library_ids,
    )
    if not doc:
        return {"ok": False, "error": "文档不存在或不在当前所选知识库"}
    if page < 1 or (int(doc.page_count or 0) > 0 and page > int(doc.page_count)):
        return {"ok": False, "error": "页码不存在"}
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
    page_res = read_page(
        db,
        owner_id=owner_id,
        document_id=document_id,
        page=page,
        library_ids=library_ids,
    )
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
    library_ids: list[str] | None = None,
) -> dict[str, Any]:
    if chunk_id:
        filters = [Chunk.id == chunk_id, Chunk.owner_id == owner_id]
        if library_ids is not None:
            scoped = list(dict.fromkeys(str(x) for x in library_ids if x))
            if not scoped:
                return {"ok": False, "error": "chunk 不在当前所选知识库"}
            filters.append(Chunk.library_id.in_(scoped))
        row = db.execute(
            select(Chunk, Document.file_name)
            .join(Document, Document.id == Chunk.document_id)
            .where(*filters)
        ).first()
        if not row:
            return {"ok": False, "error": "chunk 不存在或不在当前所选知识库"}
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
        page_res = read_page(
            db,
            owner_id=owner_id,
            document_id=document_id,
            page=page,
            library_ids=library_ids,
        )
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
    query_vector: list[float] | None = None,
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
            query_vector=query_vector,
        )
    if name == "read_page":
        return read_page(
            db,
            owner_id=owner_id,
            document_id=str(args.get("document_id") or ""),
            page=int(args.get("page") or 1),
            library_ids=library_ids,
        )
    if name == "extract_table":
        return extract_table(
            db,
            owner_id=owner_id,
            document_id=str(args.get("document_id") or ""),
            page=int(args.get("page") or 1),
            library_ids=library_ids,
        )
    if name == "analyze_chart":
        return analyze_chart(
            db,
            owner_id=owner_id,
            document_id=str(args.get("document_id") or ""),
            page=int(args.get("page") or 1),
            library_ids=library_ids,
        )
    if name == "quote_source":
        return quote_source(
            db,
            owner_id=owner_id,
            chunk_id=args.get("chunk_id"),
            document_id=args.get("document_id"),
            page=args.get("page"),
            excerpt=args.get("excerpt"),
            library_ids=library_ids,
        )
    return {"ok": False, "error": f"未知工具: {name}"}


def plan_tools(question: str, *, library_ids: list[str]) -> list[dict[str, Any]]:
    """The first Agent step is deterministic; later steps observe real hits."""
    return [{"name": "search_pdf", "arguments": {"query": question}}]


def plan_followup_tools(
    question: str,
    search_result: dict[str, Any],
    *,
    remaining_steps: int,
) -> list[dict[str, Any]]:
    """Plan page/table/chart/quote tools after search IDs and pages are known."""
    settings = get_settings()
    if remaining_steps <= 0 or not settings.openai_api_key:
        return []
    hits = [h for h in (search_result.get("hits") or []) if isinstance(h, dict)]
    if not hits:
        return []
    allowed_docs = {str(h.get("document_id") or "") for h in hits if h.get("document_id")}
    allowed_chunks = {str(h.get("chunk_id") or "") for h in hits if h.get("chunk_id")}
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "你已拿到论文检索结果。按需选择后续工具: read_page, extract_table, "
                    "analyze_chart, quote_source。只能使用输入 hits 中真实出现的 document_id、"
                    "chunk_id 和页码；无需额外工具时返回空 steps。只输出 JSON: "
                    "{\"steps\":[{\"name\":\"...\",\"arguments\":{...}}]}，"
                    f"最多 {remaining_steps} 步。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "hits": hits[:8],
                        "tools": TOOL_SPECS[1:],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        raw = chat_json(messages, temperature=0.1, operation="agent_plan")
        steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
        cleaned: list[dict[str, Any]] = []
        for step in steps[:remaining_steps]:
            if not isinstance(step, dict):
                continue
            name = str(step.get("name") or "").strip()
            args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
            if name not in {"read_page", "extract_table", "analyze_chart", "quote_source"}:
                continue
            if name == "quote_source":
                chunk_id = str(args.get("chunk_id") or "")
                document_id = str(args.get("document_id") or "")
                if chunk_id and chunk_id not in allowed_chunks:
                    continue
                if document_id and document_id not in allowed_docs:
                    continue
                if not chunk_id and not document_id:
                    continue
            else:
                document_id = str(args.get("document_id") or "")
                if document_id not in allowed_docs:
                    continue
                try:
                    args["page"] = max(1, int(args.get("page") or 1))
                except (TypeError, ValueError):
                    continue
            cleaned.append({"name": name, "arguments": args})
        return cleaned
    except Exception:  # noqa: BLE001
        logger.exception("plan_followup_tools failed")
        return []


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
    conversation_id: str | None = None,
    library_ids: list[str],
    question: str,
    history: list[dict[str, str]] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    skill_prompt: str | None = None,
    force_document_coverage: bool = False,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (event_name, payload). Final event is 'final' with answer/citations meta."""
    settings = get_settings()
    conversation_context = ConversationContext(retrieval_query=question)
    if conversation_id and settings.conversation_memory_enabled:
        yield (
            "status",
            {
                "phase": "memory",
                "text": "正在解析多轮指代并召回相关会话记忆…",
            },
        )
        conversation_context = prepare_conversation_context(
            db,
            owner_id=owner_id,
            conversation_id=conversation_id,
            question=question,
            history=history,
            library_ids=library_ids,
            model=model,
        )
    retrieval_question = (
        conversation_context.retrieval_query.strip() or question
    )
    memory_meta = {
        "enabled": bool(conversation_context.enabled),
        "summary_used": bool(conversation_context.summary),
        "recalled_count": len(conversation_context.recalled),
        "query_rewritten": bool(conversation_context.query_rewritten),
        "retrieval_query": (
            retrieval_question
            if conversation_context.query_rewritten
            else None
        ),
    }
    if conversation_context.enabled:
        yield ("meta", {"memory": memory_meta})
    intent = detect_intent(retrieval_question)
    inventory = list_library_documents(db, owner_id=owner_id, library_ids=library_ids)
    inventory_text = format_inventory_block(
        inventory,
        max_items=settings.inventory_prompt_max_documents,
    )
    compare = intent == QueryIntent.COMPARE
    cover_documents = compare or force_document_coverage
    comparison_mode = compare or force_document_coverage
    cards_text = None
    if cover_documents or len(inventory) <= 3:
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
                "memory": memory_meta,
            },
        )
        return

    ready_count = sum(1 for item in inventory if item.status == "ready")
    if cover_documents and ready_count > settings.compare_max_documents:
        ans = (
            f"当前选择中有 {ready_count} 篇可检索文献，超过单次全量对比上限 "
            f"{settings.compare_max_documents} 篇。请缩小知识库范围后再进行全量对比。"
        )
        yield ("token", {"text": ans})
        yield (
            "final",
            {
                "answer": ans,
                "citations": [],
                "retrieval_hit": 0,
                "degraded": True,
                "confidence": "low",
                "memory": memory_meta,
            },
        )
        return

    # Compare / small libraries: hybrid + full-doc coverage (skip agent tool planner)
    use_hybrid = (not settings.agent_enabled) or cover_documents or len(inventory) <= 3
    if use_hybrid:
        yield (
            "status",
            {
                "phase": "retrieving",
                "text": "正在混合检索（多文献覆盖）…" if cover_documents else "正在混合检索…",
            },
        )
        rows = hybrid_retrieve(
            db,
            owner_id=owner_id,
            library_ids=library_ids,
            question=retrieval_question,
            cover_all_docs=cover_documents or len(inventory) <= 3,
            coverage_per_doc=(
                max(2, int(settings.compare_chunks_per_document))
                if comparison_mode
                else 2
            ),
            top_k=max(settings.rag_top_k, 8) if cover_documents else None,
            query_vector=conversation_context.query_vector,
        )
        if not rows:
            ans = empty_answer()["answer"]
            yield ("token", {"text": ans})
            yield (
                "final",
                {
                    "answer": ans,
                    "citations": [],
                    "retrieval_hit": 0,
                    "degraded": True,
                    "confidence": "low",
                    "memory": memory_meta,
                },
            )
            return
        expected_compare_documents = [
            (item.document_id, item.file_name)
            for item in inventory
            if item.status == "ready"
        ] if comparison_mode else []
        comparison_coverage = (
            comparison_evidence_coverage(rows) if comparison_mode else None
        )
        if comparison_coverage is not None:
            for document_id, _file_name in expected_compare_documents:
                comparison_coverage.setdefault(document_id, [])
        meta_payload: dict[str, Any] = {"retrieval_hit": len(rows)}
        if comparison_coverage is not None:
            meta_payload["comparison_evidence_coverage"] = comparison_coverage
        yield ("meta", meta_payload)
        yield (
            "status",
            {"phase": "generating", "text": f"已命中 {len(rows)} 段（覆盖多篇文献），正在生成…"},
        )
        from app.services.generate import build_rag_stream_messages

        intent_hint = (
            "这是跨文献对比/共同点问题：必须覆盖文献清单中 status=ready 的各篇；"
            "未完成索引的文献只说明状态，不纳入内容比较。禁止声称只检索到一篇；"
            "某篇证据不足时单独说明，不得否认该篇在库中。"
            "排版：先自然段总述，异同处可用 Markdown 表格，最后一段小结；不要用 --- 装饰线。"
            f"\n{format_comparison_evidence_coverage(rows, expected_documents=expected_compare_documents)}"
            if comparison_mode
            else None
        )
        messages = build_rag_stream_messages(
            question,
            rows,
            history=history,
            conversation_context=conversation_context,
            inventory_text=inventory_text,
            document_cards_text=cards_text,
            intent_hint=intent_hint,
            skill_prompt=skill_prompt,
            balance_documents=comparison_mode,
        )
        parts: list[str] = []
        if comparison_mode:
            # Buffer compare answers until their table/evidence contract has
            # passed. Otherwise an invalid table is already visible in the UI
            # before a repair can replace it.
            for tok in chat_stream(
                messages,
                model=model,
                temperature=temperature,
                operation="answer_generate",
            ):
                if tok is None:
                    yield ("heartbeat", {"phase": "model"})
                    continue
                parts.append(tok)
            answer = "".join(parts).strip()
            violations = comparison_answer_violations(answer)
            if not answer or violations:
                yield (
                    "status",
                    {
                        "phase": "repairing",
                        "text": "正在校验多文献证据并修订对照结构…",
                    },
                )
                repair_messages = messages + [
                    {"role": "assistant", "content": answer},
                    {
                        "role": "user",
                        "content": (
                            "上一版未通过证据结构校验。请完整重写答案：删除含有证据不足占位语的"
                            "表格列，只保留每篇都有原文支持的可比维度；把缺失项集中写入一次"
                            "“证据缺口”，并明确本轮未检索到不等于论文未报告。不要解释修订过程。"
                        ),
                    },
                ]
                repaired_parts: list[str] = []
                try:
                    for tok in chat_stream(
                        repair_messages,
                        model=model,
                        temperature=0.0,
                        operation="answer_repair",
                    ):
                        if tok is None:
                            yield ("heartbeat", {"phase": "repair"})
                            continue
                        repaired_parts.append(tok)
                except Exception:  # noqa: BLE001
                    logger.exception("comparison answer repair failed; sanitizing draft")
                repaired = "".join(repaired_parts).strip()
                if repaired:
                    answer = repaired
            if comparison_answer_violations(answer):
                answer = sanitize_comparison_answer(answer)
            if not answer:
                answer = empty_answer()["answer"]
            parts = [answer]
            replay_step = max(32, len(answer) // 80 or 32)
            for offset in range(0, len(answer), replay_step):
                yield ("token", {"text": answer[offset : offset + replay_step]})
        else:
            for tok in chat_stream(
                messages,
                model=model,
                temperature=temperature,
                operation="answer_generate",
            ):
                if tok is None:
                    yield ("heartbeat", {"phase": "model"})
                    continue
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
                "memory": memory_meta,
                "comparison_evidence_coverage": comparison_coverage,
            },
        )
        return

    steps = plan_tools(retrieval_question, library_ids=library_ids)
    yield ("status", {"phase": "planning", "text": "先检索原文，再按命中结果规划工具"})
    evidence: list[dict[str, Any]] = []
    citations_acc: list[dict[str, Any]] = []
    pages_read = 0
    followups_planned = False

    step_index = 0
    while step_index < len(steps) and step_index < settings.agent_max_tool_rounds:
        step = steps[step_index]
        step_index += 1
        name = step["name"]
        args = step.get("arguments") or {}
        yield ("tool", {"name": name, "arguments": args, "phase": "start"})
        if name in {"read_page", "extract_table", "analyze_chart"}:
            pages_read += 1
            if pages_read > settings.agent_max_pages_read:
                result: dict[str, Any] = {"ok": False, "error": "已达最大读页次数"}
            else:
                result = run_tool(
                    db,
                    name,
                    args,
                    owner_id=owner_id,
                    library_ids=library_ids,
                    query_vector=(
                        conversation_context.query_vector
                        if name == "search_pdf"
                        else None
                    ),
                )
        else:
            result = run_tool(
                db,
                name,
                args,
                owner_id=owner_id,
                library_ids=library_ids,
                query_vector=(
                    conversation_context.query_vector
                    if name == "search_pdf"
                    else None
                ),
            )
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
            if not followups_planned:
                followups_planned = True
                remaining = settings.agent_max_tool_rounds - len(steps)
                followups = plan_followup_tools(
                    retrieval_question,
                    result,
                    remaining_steps=max(0, remaining),
                )
                if followups:
                    steps.extend(followups)
                    yield (
                        "status",
                        {
                            "phase": "planning",
                            "text": f"根据检索命中追加 {len(followups)} 个核验步骤",
                        },
                    )
        if name == "quote_source" and result.get("citation"):
            citations_acc.append(result["citation"])

    if not evidence:
        ans = empty_answer()["answer"]
        yield ("token", {"text": ans})
        yield (
            "final",
            {
                "answer": ans,
                "citations": [],
                "retrieval_hit": 0,
                "degraded": True,
                "confidence": "low",
                "memory": memory_meta,
            },
        )
        return

    yield ("status", {"phase": "generating", "text": "正在根据工具证据生成回答…"})
    agent_system = RAG_AGENT_FINAL_SYSTEM
    if skill_prompt:
        agent_system = f"{agent_system}\n\n{skill_prompt.strip()}"
    messages: list[dict[str, str]] = [{"role": "system", "content": agent_system}]
    memory_text = format_conversation_context(
        conversation_context,
        max_chars=int(settings.memory_recall_max_chars),
    )
    if memory_text:
        messages.append({"role": "system", "content": memory_text})
    messages.extend(
        budget_history(history, int(settings.rag_history_max_chars))
    )
    messages.append(
        {
            "role": "user",
            "content": (
                f"问题：{clip_text(question, 4000)}\n\n"
                f"{clip_text(inventory_text, settings.rag_inventory_max_chars)}\n\n"
                f"{clip_text(cards_text, settings.rag_cards_max_chars)}\n\n"
                "工具证据：\n"
                f"{clip_text(json.dumps(evidence, ensure_ascii=False), settings.rag_tool_evidence_max_chars)}"
            ),
        }
    )
    parts = []
    for tok in chat_stream(
        messages,
        model=model,
        temperature=temperature,
        operation="answer_generate",
    ):
        if tok is None:
            yield ("heartbeat", {"phase": "model"})
            continue
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
            "memory": memory_meta,
        },
    )
