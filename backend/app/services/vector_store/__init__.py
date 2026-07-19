from __future__ import annotations

from app.config import get_settings
from app.services.vector_store.base import VectorHit, VectorStore
from app.services.vector_store.pgvector_store import PgVectorStore


def get_vector_store(db=None) -> VectorStore:  # noqa: ANN001
    backend = (get_settings().vector_backend or "pgvector").strip().lower()
    if backend not in {"pgvector", "postgres", "postgresql"}:
        pass
    return PgVectorStore(db)


__all__ = ["VectorHit", "VectorStore", "PgVectorStore", "get_vector_store"]
