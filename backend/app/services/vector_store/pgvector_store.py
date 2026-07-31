"""Postgres + pgvector VectorStore implementation."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Chunk, Document
from app.services.acl import chunk_acl_filters
from app.services.vector_store.base import VectorHit


def _cosine(a: list[float], b: list[float]) -> float:
    from math import sqrt

    n = min(len(a), len(b))
    if n == 0:
        return -1.0
    dot = sum(a[i] * b[i] for i in range(n))
    na = sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    nb = sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0
    return dot / (na * nb)


def _score_from_distance(distance: float | None) -> float:
    distance_value = float(distance) if distance is not None else 1.0
    return 1.0 - distance_value


class PgVectorStore:
    def __init__(self, db: Session | None = None) -> None:
        self._db = db

    def search(
        self,
        *,
        owner_id: str,
        library_ids: list[str],
        query_vector: list[float],
        top_n: int,
    ) -> list[VectorHit]:
        if self._db is None:
            raise RuntimeError("PgVectorStore requires a DB session")
        if not library_ids or top_n <= 0:
            return []
        db = self._db
        settings = get_settings()
        filters = chunk_acl_filters(owner_id=owner_id, library_ids=library_ids)
        filters.append(Chunk.embedding.is_not(None))

        if settings.is_sqlite:
            rows = db.execute(
                select(Chunk, Document.file_name)
                .join(Document, Document.id == Chunk.document_id)
                .where(*filters)
            ).all()
            scored: list[VectorHit] = []
            for chunk, file_name in rows:
                emb = chunk.embedding
                if not isinstance(emb, list):
                    try:
                        emb = list(emb) if emb is not None else None
                    except Exception:  # noqa: BLE001
                        continue
                if not emb:
                    continue
                score = _cosine(query_vector, emb)
                scored.append(_hit(chunk, file_name, score))
            scored.sort(key=lambda x: x.score, reverse=True)
            return scored[:top_n]

        distance = Chunk.embedding.cosine_distance(query_vector)
        # Raise recall for HNSW when available (no-op if GUC unsupported).
        try:
            db.execute(text("SET LOCAL hnsw.ef_search = 64"))
        except Exception:  # noqa: BLE001
            pass
        stmt = (
            select(Chunk, Document.file_name, distance.label("distance"))
            .join(Document, Document.id == Chunk.document_id)
            .where(*filters)
            .order_by(distance)
            .limit(top_n)
        )
        out: list[VectorHit] = []
        for chunk, file_name, dist in db.execute(stmt).all():
            # A perfect cosine match has distance 0.0. Do not use ``or`` here:
            # ``0.0 or 1.0`` would incorrectly turn an exact match into score 0.
            score = _score_from_distance(dist)
            out.append(_hit(chunk, file_name, score))
        return out


def _hit(chunk: Chunk, file_name: str, score: float) -> VectorHit:
    return VectorHit(
        chunk_id=str(chunk.id),
        document_id=str(chunk.document_id),
        library_id=str(chunk.library_id),
        owner_id=str(chunk.owner_id),
        file_name=str(file_name),
        text=str(chunk.text),
        score=float(score),
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        chunk_index=int(chunk.chunk_index),
        section_path=getattr(chunk, "section_path", None),
        role=getattr(chunk, "role", None),
        collection_key=getattr(chunk, "collection_key", None),
        embedding_version=getattr(chunk, "embedding_version", None),
    )
