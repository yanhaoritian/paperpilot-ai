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


def _best_chunk_for_document(
    db: Session,
    *,
    owner_id: str,
    document_id: str,
    question: str,
    q_vec: list[float] | None,
) -> RetrievedChunk | None:
    rows = db.execute(
        select(Chunk, Document.file_name)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.owner_id == owner_id,
            Chunk.document_id == document_id,
        )
        .limit(400)
    ).all()
    if not rows:
        return None
    best: RetrievedChunk | None = None
    best_score = -1.0
    for chunk, file_name in rows:
        score = _keyword_score(question, chunk.text or "")
        if q_vec and chunk.embedding is not None:
            try:
                emb = chunk.embedding
                if not isinstance(emb, list):
                    emb = list(emb)
                score = max(score, float(_cosine(q_vec, emb)))
            except Exception:  # noqa: BLE001
                pass
        if score > best_score:
            best_score = score
            best = _to_retrieved(chunk, file_name, max(score, 1e-6))
    return best


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
    """Guarantee at least one chunk from each ready document in selected libraries."""
    settings = get_settings()
    k = top_k or max(settings.rag_top_k, 8)
    docs = db.scalars(
        select(Document).where(
            Document.owner_id == owner_id,
            Document.library_id.in_(library_ids),
            Document.status == "ready",
        )
    ).all()
    if not docs:
        return rows[:k]

    by_doc: dict[str, list[RetrievedChunk]] = defaultdict(list)
    for r in rows:
        by_doc[r.document_id].append(r)

    picked: list[RetrievedChunk] = []
    seen_ids: set[str] = set()
    for doc in docs:
        did = str(doc.id)
        existing = by_doc.get(did) or []
        if existing:
            for r in existing[:per_doc]:
                if r.chunk_id not in seen_ids:
                    picked.append(r)
                    seen_ids.add(r.chunk_id)
        else:
            fill = _best_chunk_for_document(
                db,
                owner_id=owner_id,
                document_id=did,
                question=question,
                q_vec=q_vec,
            )
            if fill and fill.chunk_id not in seen_ids:
                picked.append(fill)
                seen_ids.add(fill.chunk_id)

    for r in rows:
        if r.chunk_id in seen_ids:
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
    query_vector: list[float] | None = None,
) -> list[RetrievedChunk]:
    settings = get_settings()
    k = top_k or settings.rag_top_k
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
                top_n=max(k, len(preserve_ids) * 2),
                preserve_doc_ids=preserve_ids,
                min_per_doc=2,
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
            per_doc=2,
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
            per_doc=2,
            top_k=max(k * 2, len(preserve_ids) * 2, k),
        )
        return rerank_chunks(
            question,
            fused,
            top_n=max(k, len(preserve_ids) * 2),
            preserve_doc_ids=preserve_ids,
            min_per_doc=2,
        )
    return rerank_chunks(question, fused, top_n=k)
