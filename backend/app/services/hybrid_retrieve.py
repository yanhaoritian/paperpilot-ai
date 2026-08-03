from __future__ import annotations

import logging
import math
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Chunk, Document
from app.services.openai_client import chat_json, embed_texts
from app.services.retrieve import RetrievedChunk, _cosine, _diversify_by_document

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    # CJK bigrams + latin words
    toks: list[str] = []
    toks.extend(re.findall(r"[a-z0-9_]+", text))
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for span in cjk:
        if len(span) == 1:
            toks.append(span)
        else:
            toks.extend(span[i : i + 2] for i in range(len(span) - 1))
    return toks


def _keyword_score(query: str, doc_text: str) -> float:
    q = set(_tokenize(query))
    if not q:
        return 0.0
    d = _tokenize(doc_text)
    if not d:
        return 0.0
    # TF of overlapping tokens
    tf: dict[str, int] = defaultdict(int)
    for t in d:
        if t in q:
            tf[t] += 1
    if not tf:
        return 0.0
    # simple BM25-ish without IDF (lab scale)
    score = sum(1.0 + math.log(1.0 + c) for c in tf.values())
    return score / (1.0 + math.log(1.0 + len(d)))


def _vector_candidates(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    q_vec: list[float],
    top_n: int,
) -> list[RetrievedChunk]:
    from app.services.vector_store import get_vector_store

    hits = get_vector_store(db).search(
        owner_id=owner_id,
        library_ids=library_ids,
        query_vector=q_vec,
        top_n=top_n,
    )
    return [
        RetrievedChunk(
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            library_id=h.library_id,
            file_name=h.file_name,
            text=h.text,
            page_start=h.page_start,
            page_end=h.page_end,
            chunk_index=h.chunk_index,
            score=h.score,
            section_path=h.section_path,
            role=h.role,
        )
        for h in hits
    ]


def _keyword_candidates(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    question: str,
    top_n: int,
) -> list[RetrievedChunk]:
    from app.services.acl import chunk_acl_filters
    from app.services.sparse_bm25 import bm25_rank

    settings = get_settings()
    filters = chunk_acl_filters(owner_id=owner_id, library_ids=library_ids)
    cap = max(top_n, settings.bm25_candidate_cap)
    base = (
        select(Chunk, Document.file_name)
        .join(Document, Document.id == Chunk.document_id)
        .where(*filters)
    )
    if not settings.is_sqlite and settings.postgres_trigram_enabled:
        candidate_cap = max(
            top_n,
            min(cap, int(settings.postgres_trigram_candidate_cap)),
        )
        try:
            rows = db.execute(
                base.order_by(Chunk.text.op("<->")(question).asc())
                .limit(candidate_cap)
            ).all()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception(
                "PostgreSQL trigram candidate search failed; falling back"
            )
            rows = db.execute(base.limit(cap)).all()
    else:
        rows = db.execute(base.limit(cap)).all()
    if settings.bm25_enabled:
        return bm25_rank(question, rows, top_n=top_n, to_retrieved=_to_retrieved)

    scored: list[RetrievedChunk] = []
    for chunk, file_name in rows:
        score = _keyword_score(question, f"{chunk.section_path or ''} {chunk.text}")
        if score <= 0:
            continue
        scored.append(_to_retrieved(chunk, file_name, score))
    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:top_n]


def _to_retrieved(chunk: Chunk, file_name: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(chunk.id),
        document_id=str(chunk.document_id),
        library_id=str(chunk.library_id),
        file_name=str(file_name),
        text=str(chunk.text),
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        chunk_index=int(chunk.chunk_index),
        score=float(score),
        section_path=getattr(chunk, "section_path", None),
        role=getattr(chunk, "role", None),
    )


def _rrf_fuse(
    lists: list[list[RetrievedChunk]],
    *,
    k: int,
    limit: int,
) -> list[RetrievedChunk]:
    scores: dict[str, float] = defaultdict(float)
    by_id: dict[str, RetrievedChunk] = {}
    for rows in lists:
        for rank, row in enumerate(rows, start=1):
            scores[row.chunk_id] += 1.0 / (k + rank)
            by_id[row.chunk_id] = row
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    out: list[RetrievedChunk] = []
    for cid, s in ordered[:limit]:
        row = by_id[cid]
        out.append(
            RetrievedChunk(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                library_id=row.library_id,
                file_name=row.file_name,
                text=row.text,
                page_start=row.page_start,
                page_end=row.page_end,
                chunk_index=row.chunk_index,
                score=float(s),
                section_path=row.section_path,
                role=row.role,
            )
        )
    return out


def rerank_chunks(
    question: str,
    rows: list[RetrievedChunk],
    *,
    top_n: int,
    preserve_doc_ids: set[str] | None = None,
    min_per_doc: int = 1,
) -> list[RetrievedChunk]:
    """Rerank candidates. When preserve_doc_ids is set, never drop those documents."""
    settings = get_settings()
    provider = (settings.rerank_provider or "off").strip().lower()
    preserve = {str(x) for x in (preserve_doc_ids or set()) if x}
    min_per_doc = max(1, int(min_per_doc))

    def _truncate_preserving(ordered: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not preserve:
            return ordered[:top_n]
        by_doc: dict[str, list[RetrievedChunk]] = defaultdict(list)
        for r in ordered:
            by_doc[r.document_id].append(r)
        picked: list[RetrievedChunk] = []
        seen: set[str] = set()
        # Slot budget: at least min_per_doc per preserved doc, then fill by rank
        floor = max(top_n, len(preserve) * min_per_doc)
        for did in preserve:
            for r in by_doc.get(did, [])[:min_per_doc]:
                if r.chunk_id not in seen:
                    picked.append(r)
                    seen.add(r.chunk_id)
        for r in ordered:
            if r.chunk_id in seen:
                continue
            picked.append(r)
            seen.add(r.chunk_id)
            if len(picked) >= floor:
                break
        # Still missing a preserved doc? keep whatever we have (coverage pass should have filled)
        return picked[:floor]

    if provider == "off" or len(rows) <= 1:
        return _truncate_preserving(rows)

    if provider == "llm":
        try:
            payload = {
                "question": question,
                "candidates": [
                    {"id": r.chunk_id, "text": r.text[:500], "file": r.file_name, "page": r.page_start}
                    for r in rows[: max(top_n * 2, settings.rerank_top_n, len(preserve) * 3)]
                ],
            }
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是检索精排器。根据问题相关性对 candidates 排序，"
                        '只输出 JSON: {"ordered_ids":["id",...]}，id 必须来自输入。'
                        + (
                            "多文献问题时不得把某一文献的全部候选排到末尾故意淘汰；各文献都应保留相关片段。"
                            if preserve
                            else ""
                        )
                    ),
                },
                {"role": "user", "content": str(payload)},
            ]
            raw = chat_json(messages, temperature=0.0, operation="rerank")
            ids = raw.get("ordered_ids") if isinstance(raw.get("ordered_ids"), list) else []
            by_id = {r.chunk_id: r for r in rows}
            ordered: list[RetrievedChunk] = []
            seen = set()
            for cid in ids:
                cid = str(cid)
                if cid in by_id and cid not in seen:
                    ordered.append(by_id[cid])
                    seen.add(cid)
            for r in rows:
                if r.chunk_id not in seen:
                    ordered.append(r)
            return _truncate_preserving(ordered)
        except Exception:  # noqa: BLE001
            logger.exception("llm rerank failed; falling back")
            return _truncate_preserving(rows)

    # api placeholder: fall back to score order
    return _truncate_preserving(rows)


_NON_SUBSTANTIVE_COVERAGE_ROLES = frozenset(
    {"title", "section_heading", "section", "heading"}
)
_MIN_SUBSTANTIVE_CHARS = 80
_EVIDENCE_FACET_PATTERNS = {
    "method": re.compile(
        r"(?:\bmethods?\b|\bmethodology\b|\bexperimental(?: design)?\b|"
        r"\bprotocol\b|\bprocedure\b|\balgorithm\b|\bimplementation\b|"
        r"\binversion\b|\bimaging\b|\bsimulation\b|"
        r"方法|材料与方法|研究设计|实验设计|实验方法|模型|算法|实现|流程|"
        r"反演|成像|模拟|受试者|参与者)",
        re.IGNORECASE,
    ),
    "data": re.compile(
        r"(?:\bdata(?:set)?s?\b|\bcorpus\b|\bcorpora\b|\bcohort\b|"
        r"\bsamples?\b|\bbenchmark\b|\bmeasurements?\b|\bprofiles?\b|"
        r"\bsurveys?\b|\bobservations?\b|"
        r"数据|数据集|语料|队列|样本|基准|测量|统计|剖面|地震资料|观测)",
        re.IGNORECASE,
    ),
    "result": re.compile(
        r"(?:\bresults?\b|\bfindings?\b|\bperformance\b|\bevaluation\b|"
        r"\baccuracy\b|\bsignificant(?:ly)?\b|\beffects?\b|\bconclusions?\b|"
        r"\bdemonstrat(?:e|es|ed)\b|\bindicat(?:e|es|ed)\b|\bshows?\b|"
        r"结果|发现|性能|评估|准确率|显著|效果|提升|结论|表明|显示)",
        re.IGNORECASE,
    ),
    "limitation": re.compile(
        r"(?:\blimitations?\b|\bdiscussion\b|\bfuture work\b|"
        r"\bthreats? to validity\b|\bweakness(?:es)?\b|\bconstraints?\b|"
        r"\buncertaint(?:y|ies)\b|\bassumptions?\b|"
        r"局限|局限性|讨论|不足|未来工作|有效性威胁|约束|不确定性|假设)",
        re.IGNORECASE,
    ),
}


def _coverage_text_length(row: RetrievedChunk) -> int:
    return len(re.sub(r"\s+", "", row.text or ""))


def _is_substantive_coverage_chunk(row: RetrievedChunk) -> bool:
    role = (row.role or "").strip().lower()
    return (
        role not in _NON_SUBSTANTIVE_COVERAGE_ROLES
        and _coverage_text_length(row) >= _MIN_SUBSTANTIVE_CHARS
    )


def _evidence_facets(row: RetrievedChunk) -> set[str]:
    haystack = f"{row.section_path or ''}\n{row.text or ''}"
    return {
        name
        for name, pattern in _EVIDENCE_FACET_PATTERNS.items()
        if pattern.search(haystack)
    }


def _facet_priority(question: str) -> list[str]:
    requested = [
        name
        for name, pattern in _EVIDENCE_FACET_PATTERNS.items()
        if pattern.search(question or "")
    ]
    return requested + [
        name for name in _EVIDENCE_FACET_PATTERNS if name not in requested
    ]


def _select_facet_coverage_chunks(
    rows: list[RetrievedChunk],
    *,
    limit: int,
    question: str,
) -> list[RetrievedChunk]:
    """Fill independent evidence slots, preferring a facet's own section.

    A broad abstract can mention method, data, results, and limitations in one
    paragraph. It is useful context, but it must not impersonate four
    independent evidence sections when fuller text is available.
    """
    if limit <= 0:
        return []
    candidates: list[RetrievedChunk] = []
    seen_ids: set[str] = set()
    for row in rows:
        if row.chunk_id in seen_ids or not _is_substantive_coverage_chunk(row):
            continue
        seen_ids.add(row.chunk_id)
        candidates.append(row)

    selected: list[RetrievedChunk] = []
    selected_ids: set[str] = set()
    for facet in _facet_priority(question):
        if len(selected) >= limit:
            break
        pattern = _EVIDENCE_FACET_PATTERNS[facet]
        matching = [
            row
            for row in candidates
            if row.chunk_id not in selected_ids and facet in _evidence_facets(row)
        ]
        if not matching:
            continue

        def facet_key(row: RetrievedChunk) -> tuple[float | int, ...]:
            section = row.section_path or ""
            role = (row.role or "").strip().lower()
            facets = _evidence_facets(row)
            return (
                int(bool(section) and pattern.search(section) is not None),
                int(role != "abstract"),
                -len(facets),
                float(row.score),
                _coverage_text_length(row),
                -int(row.chunk_index),
            )

        best = max(matching, key=facet_key)
        selected.append(best)
        selected_ids.add(best.chunk_id)

    if len(selected) < limit:
        selected.extend(
            _select_diverse_coverage_chunks(
                [row for row in candidates if row.chunk_id not in selected_ids],
                limit=limit - len(selected),
                seed=selected,
                substantive_only=True,
            )
        )
    return selected


def comparison_evidence_coverage(
    rows: list[RetrievedChunk],
) -> dict[str, list[str]]:
    """Summarize substantive compare-evidence facets by document.

    The helper is intentionally model-free and cheap so prompt construction can
    expose evidence gaps without running retrieval or classification again.
    Documents represented only by a heading/short fallback remain present with
    an empty facet list.
    """
    facet_order = tuple(_EVIDENCE_FACET_PATTERNS)
    coverage: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        did = str(row.document_id)
        coverage.setdefault(did, set())
        if _is_substantive_coverage_chunk(row):
            coverage[did].update(_evidence_facets(row))
    return {
        did: [facet for facet in facet_order if facet in facets]
        for did, facets in coverage.items()
    }


def format_comparison_evidence_coverage(
    rows: list[RetrievedChunk],
    *,
    expected_documents: list[tuple[str, str]] | None = None,
) -> str:
    """Format a compact, model-readable coverage check for compare prompts."""
    labels = {
        "method": "方法",
        "data": "数据/样本",
        "result": "结果/指标",
        "limitation": "局限/讨论",
    }
    coverage = comparison_evidence_coverage(rows)
    names: dict[str, str] = {}
    order: list[str] = []
    for document_id, file_name in expected_documents or []:
        did = str(document_id)
        if did not in names:
            names[did] = str(file_name)
            order.append(did)
    for row in rows:
        did = str(row.document_id)
        if did not in names:
            names[did] = row.file_name
            order.append(did)
    lines = [
        "结构化证据覆盖检查（关键词粗检，仅用于发现补查项；最终以所给原文片段为准）："
    ]
    target = tuple(labels)
    for did in order:
        present = set(coverage.get(did, []))
        present_text = "、".join(labels[key] for key in target if key in present) or "暂无明确维度标签"
        missing_text = "、".join(labels[key] for key in target if key not in present) or "无"
        lines.append(
            f"- 《{names[did]}》：粗检已覆盖 {present_text}；待核对 {missing_text}。"
        )
    lines.append(
        "生成时逐段核对；若待核对项在原文中仍无足够证据，只在一次“证据缺口”中汇总，"
        "不要把它们填成主表占位单元格。"
    )
    return "\n".join(lines)


def _select_diverse_coverage_chunks(
    rows: list[RetrievedChunk],
    *,
    limit: int,
    seed: list[RetrievedChunk] | None = None,
    substantive_only: bool = False,
) -> list[RetrievedChunk]:
    """Select useful per-document evidence without collapsing onto one section.

    The first choice follows retrieval relevance. Subsequent choices prefer an
    unseen evidence facet (method/data/result/limitation), then a new section or
    page. This keeps compare answers broad while retaining a deterministic,
    score-based fallback for papers whose structure metadata is sparse.
    """
    if limit <= 0:
        return []

    deduped: list[RetrievedChunk] = []
    seen_ids: set[str] = set()
    for row in rows:
        if row.chunk_id in seen_ids:
            continue
        seen_ids.add(row.chunk_id)
        if substantive_only and not _is_substantive_coverage_chunk(row):
            continue
        deduped.append(row)

    selected: list[RetrievedChunk] = []
    context = list(seed or [])
    while deduped and len(selected) < limit:
        seen_facets = set().union(*(_evidence_facets(r) for r in context)) if context else set()
        seen_sections = {
            (r.section_path or "").strip().lower()
            for r in context
            if (r.section_path or "").strip()
        }
        seen_pages = {r.page_start for r in context if r.page_start is not None}

        def selection_key(row: RetrievedChunk) -> tuple[float | int, ...]:
            facets = _evidence_facets(row)
            section = (row.section_path or "").strip().lower()
            if not context:
                return (
                    float(row.score),
                    len(facets),
                    _coverage_text_length(row),
                    -int(row.chunk_index),
                )
            unseen_facets = facets - seen_facets
            return (
                int(bool(unseen_facets)),
                len(unseen_facets),
                int(bool(section) and section not in seen_sections),
                int(row.page_start is not None and row.page_start not in seen_pages),
                float(row.score),
                _coverage_text_length(row),
                -int(row.chunk_index),
            )

        best = max(deduped, key=selection_key)
        deduped.remove(best)
        selected.append(best)
        context.append(best)
    return selected


def _best_chunks_for_document(
    db: Session,
    *,
    owner_id: str,
    document_id: str,
    question: str,
    q_vec: list[float] | None,
    limit: int = 1,
    exclude_ids: set[str] | None = None,
) -> list[RetrievedChunk]:
    """Return several quality-ranked, section-diverse chunks for one document."""
    limit = max(1, int(limit))
    excluded = {str(x) for x in (exclude_ids or set())}
    rows = db.execute(
        select(Chunk, Document.file_name)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.owner_id == owner_id,
            Chunk.document_id == document_id,
        )
        .order_by(Chunk.chunk_index.asc())
        .limit(400)
    ).all()
    if not rows:
        return []

    candidates: list[RetrievedChunk] = []
    for chunk, file_name in rows:
        chunk_id = str(chunk.id)
        if chunk_id in excluded:
            continue
        search_text = "\n".join(
            part
            for part in (
                getattr(chunk, "section_path", None),
                getattr(chunk, "context_prefix", None),
                chunk.text,
            )
            if part
        )
        score = _keyword_score(question, search_text)
        if q_vec and chunk.embedding is not None:
            try:
                emb = chunk.embedding
                if not isinstance(emb, list):
                    emb = list(emb)
                score = max(score, float(_cosine(q_vec, emb)))
            except Exception:  # noqa: BLE001
                pass
        retrieved = _to_retrieved(chunk, file_name, max(score, 1e-6))
        # A small quality prior prevents a zero-similarity heading from beating
        # actual prose, while leaving real lexical/vector relevance dominant.
        if _is_substantive_coverage_chunk(retrieved):
            retrieved.score += 0.05 + 0.01 * len(_evidence_facets(retrieved))
        elif (retrieved.role or "").strip().lower() in _NON_SUBSTANTIVE_COVERAGE_ROLES:
            retrieved.score *= 0.1
        else:
            retrieved.score *= 0.5
        candidates.append(retrieved)

    substantive = _select_facet_coverage_chunks(
        candidates,
        limit=limit,
        question=question,
    )
    if len(substantive) >= limit:
        return substantive

    selected_ids = {row.chunk_id for row in substantive}
    fallback = _select_diverse_coverage_chunks(
        [row for row in candidates if row.chunk_id not in selected_ids],
        limit=limit - len(substantive),
        seed=substantive,
    )
    return substantive + fallback


def _best_chunk_for_document(
    db: Session,
    *,
    owner_id: str,
    document_id: str,
    question: str,
    q_vec: list[float] | None,
) -> RetrievedChunk | None:
    """Backward-compatible singular wrapper used by older callers/tests."""
    rows = _best_chunks_for_document(
        db,
        owner_id=owner_id,
        document_id=document_id,
        question=question,
        q_vec=q_vec,
        limit=1,
    )
    return rows[0] if rows else None


def ensure_document_coverage(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    question: str,
    rows: list[RetrievedChunk],
    q_vec: list[float] | None = None,
    per_doc: int = 2,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """Fill each ready document to ``per_doc`` evidence chunks when possible.

    Headings and very short chunks do not consume a coverage slot while usable
    prose exists. If a sparse or damaged index contains no substantive prose,
    the best available short/heading chunks are still returned as a safe
    fallback rather than silently dropping the document.
    """
    settings = get_settings()
    per_doc = max(1, int(per_doc))
    k = top_k or max(settings.rag_top_k, 8)
    docs = db.scalars(
        select(Document).where(
            Document.owner_id == owner_id,
            Document.library_id.in_(library_ids),
            Document.status == "ready",
        ).order_by(Document.created_at.asc(), Document.id.asc())
    ).all()
    if not docs:
        return rows[:k]
    # Coverage is the contract here: never truncate below its minimum capacity.
    k = max(k, len(docs) * per_doc)

    by_doc: dict[str, list[RetrievedChunk]] = defaultdict(list)
    for r in rows:
        by_doc[r.document_id].append(r)

    picked: list[RetrievedChunk] = []
    seen_ids: set[str] = set()
    for doc in docs:
        did = str(doc.id)
        existing = by_doc.get(did) or []

        # Preserve relevant prose already retrieved, but do not let a lone
        # title/section heading falsely mark the document as covered.
        if per_doc >= len(_EVIDENCE_FACET_PATTERNS) or any(
            pattern.search(question or "")
            for pattern in _EVIDENCE_FACET_PATTERNS.values()
        ):
            selected = _select_facet_coverage_chunks(
                existing,
                limit=per_doc,
                question=question,
            )
        else:
            selected = _select_diverse_coverage_chunks(
                existing,
                limit=per_doc,
                substantive_only=True,
            )
        selected_facets = set().union(
            *(_evidence_facets(row) for row in selected)
        ) if selected else set()
        target_facet_count = min(per_doc, len(_EVIDENCE_FACET_PATTERNS))
        needs_facet_fill = len(selected_facets) < target_facet_count
        fetched: list[RetrievedChunk] = []
        if (
            len(selected) < per_doc
            or needs_facet_fill
            or per_doc >= len(_EVIDENCE_FACET_PATTERNS)
        ):
            fetched = _best_chunks_for_document(
                db,
                owner_id=owner_id,
                document_id=did,
                question=question,
                q_vec=q_vec,
                # Keep a wider pool so the selector can cover distinct facets.
                limit=max(8, per_doc * 4),
                exclude_ids={r.chunk_id for r in existing},
            )
            # Re-select from the joint pool. This replaces redundant
            # introduction/method hits when another section can fill a missing
            # data/result/limitation facet, even if the numeric quota was
            # already full before the gap check.
            selected = _select_facet_coverage_chunks(
                existing + fetched,
                limit=per_doc,
                question=question,
            )

        # Sparse-index fallback: only after exhausting substantive candidates.
        if len(selected) < per_doc:
            selected_ids = {r.chunk_id for r in selected}
            fallback_pool = [
                r
                for r in existing + fetched
                if r.chunk_id not in selected_ids
            ]
            selected.extend(
                _select_diverse_coverage_chunks(
                    fallback_pool,
                    limit=per_doc - len(selected),
                    seed=selected,
                )
            )

        for r in selected:
            if r.chunk_id not in seen_ids:
                picked.append(r)
                seen_ids.add(r.chunk_id)

    substantively_covered_docs = {
        r.document_id for r in picked if _is_substantive_coverage_chunk(r)
    }
    for r in rows:
        if r.chunk_id in seen_ids:
            continue
        # Once real prose has been selected for a document, do not reintroduce
        # the title/heading/short fragment that triggered the false-coverage
        # failure in the first place merely because spare top-k slots remain.
        if (
            r.document_id in substantively_covered_docs
            and not _is_substantive_coverage_chunk(r)
        ):
            continue
        picked.append(r)
        seen_ids.add(r.chunk_id)
        if len(picked) >= k:
            break
    return picked[:k]


def hybrid_retrieve(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    question: str,
    top_k: int | None = None,
    cover_all_docs: bool = False,
    coverage_per_doc: int = 2,
    query_vector: list[float] | None = None,
) -> list[RetrievedChunk]:
    settings = get_settings()
    k = top_k or settings.rag_top_k
    coverage_per_doc = max(1, int(coverage_per_doc))
    if cover_all_docs:
        k = max(k, 8)
    if not library_ids:
        return []

    q_vec = (
        query_vector
        if query_vector is not None
        else embed_texts([question], operation="query_embedding")[0]
    )
    if not settings.hybrid_recall_enabled:
        from app.services.retrieve import retrieve_chunks

        rows = retrieve_chunks(
            db,
            owner_id=owner_id,
            library_ids=library_ids,
            question=question,
            top_k=k,
            query_vector=q_vec,
        )
        if cover_all_docs:
            rows = ensure_document_coverage(
                db,
                owner_id=owner_id,
                library_ids=library_ids,
                question=question,
                rows=rows,
                q_vec=q_vec,
                per_doc=coverage_per_doc,
                top_k=max(k, 8),
            )
            preserve_ids = {r.document_id for r in rows} | {
                str(d.id)
                for d in db.scalars(
                    select(Document).where(
                        Document.owner_id == owner_id,
                        Document.library_id.in_(library_ids),
                        Document.status == "ready",
                    )
                ).all()
            }
            return rerank_chunks(
                question,
                rows,
                top_n=max(k, len(preserve_ids) * coverage_per_doc),
                preserve_doc_ids=preserve_ids,
                min_per_doc=coverage_per_doc,
            )
        return rows

    vec = _vector_candidates(
        db,
        owner_id=owner_id,
        library_ids=library_ids,
        q_vec=q_vec,
        top_n=settings.hybrid_vector_top_n,
    )
    kw = _keyword_candidates(
        db,
        owner_id=owner_id,
        library_ids=library_ids,
        question=question,
        top_n=settings.hybrid_keyword_top_n,
    )
    fused = _rrf_fuse([vec, kw], k=settings.hybrid_rrf_k, limit=30)
    # filter by min similarity proxy: keep all fused then diversify+rerank
    fused = [r for r in fused if r.score > 0]
    fused = _diversify_by_document(fused, max(k * 3, k))
    if cover_all_docs:
        fused = ensure_document_coverage(
            db,
            owner_id=owner_id,
            library_ids=library_ids,
            question=question,
            rows=fused,
            q_vec=q_vec,
            per_doc=coverage_per_doc,
            top_k=max(k * 2, k),
        )
        preserve_ids = {r.document_id for r in fused}
        # Also preserve every ready doc id even if fill failed earlier
        ready_ids = {
            str(d.id)
            for d in db.scalars(
                select(Document).where(
                    Document.owner_id == owner_id,
                    Document.library_id.in_(library_ids),
                    Document.status == "ready",
                )
            ).all()
        }
        preserve_ids |= ready_ids
        # Re-fill any still-missing docs before coverage-safe rerank
        fused = ensure_document_coverage(
            db,
            owner_id=owner_id,
            library_ids=library_ids,
            question=question,
            rows=fused,
            q_vec=q_vec,
            per_doc=coverage_per_doc,
            top_k=max(k * 2, len(preserve_ids) * coverage_per_doc, k),
        )
        return rerank_chunks(
            question,
            fused,
            top_n=max(k, len(preserve_ids) * coverage_per_doc),
            preserve_doc_ids=preserve_ids,
            min_per_doc=coverage_per_doc,
        )
    return rerank_chunks(question, fused, top_n=k)
