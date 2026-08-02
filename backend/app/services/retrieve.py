from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.openai_client import embed_texts
from app.services.vector_store import get_vector_store


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    library_id: str
    file_name: str
    text: str
    page_start: int | None
    page_end: int | None
    chunk_index: int
    score: float
    section_path: str | None = None
    role: str | None = None


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return -1.0
    dot = sum(a[i] * b[i] for i in range(n))
    na = sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    nb = sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0
    return dot / (na * nb)


def retrieve_chunks(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
    question: str,
    top_k: int | None = None,
    min_similarity: float | None = None,
    query_vector: list[float] | None = None,
) -> list[RetrievedChunk]:
    if not library_ids:
        return []
    settings = get_settings()
    q_vec = (
        query_vector
        if query_vector is not None
        else embed_texts([question], operation="query_embedding")[0]
    )
    k = top_k or settings.rag_top_k
    min_sim = settings.rag_min_similarity if min_similarity is None else min_similarity

    hits = get_vector_store(db).search(
        owner_id=owner_id,
        library_ids=library_ids,
        query_vector=q_vec,
        top_n=max(k * 5, k),
    )
    scored: list[RetrievedChunk] = []
    for h in hits:
        if h.score < min_sim:
            continue
        scored.append(
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
        )
    return _diversify_by_document(scored, k)


def _diversify_by_document(rows: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Prefer coverage across documents so one paper does not monopolize top-k."""
    if not rows:
        return []
    picked: list[RetrievedChunk] = []
    per_doc = 2
    counts: dict[str, int] = {}
    for row in rows:
        c = counts.get(row.document_id, 0)
        if c >= per_doc:
            continue
        picked.append(row)
        counts[row.document_id] = c + 1
        if len(picked) >= top_k:
            return picked
    for row in rows:
        if row in picked:
            continue
        picked.append(row)
        if len(picked) >= top_k:
            break
    return picked
