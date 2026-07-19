"""Vector store protocol — swap pgvector for Milvus/Qdrant later without rewriting RAG."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class VectorHit:
    chunk_id: str
    document_id: str
    library_id: str
    owner_id: str
    file_name: str
    text: str
    score: float
    page_start: int | None = None
    page_end: int | None = None
    chunk_index: int = 0
    section_path: str | None = None
    role: str | None = None
    collection_key: str | None = None
    embedding_version: str | None = None


class VectorStore(Protocol):
    def search(
        self,
        *,
        owner_id: str,
        library_ids: list[str],
        query_vector: list[float],
        top_n: int,
    ) -> list[VectorHit]:
        """Dense ANN search scoped by tenant + libraries."""
        ...
