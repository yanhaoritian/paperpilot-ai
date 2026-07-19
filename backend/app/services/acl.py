"""Tenant / library ACL filters for all recall paths."""

from __future__ import annotations

from sqlalchemy import ColumnElement, or_

from app.config import get_settings
from app.models import Chunk, Document


def collection_key(owner_id: str, library_id: str) -> str:
    """Logical vector collection id (stands in for per-tenant collections)."""
    return f"{owner_id}:{library_id}"


def chunk_acl_filters(
    *,
    owner_id: str,
    library_ids: list[str],
    require_ready: bool = True,
    require_current_embedding: bool | None = None,
) -> list[ColumnElement[bool]]:
    """Always-on ownership + library scope (+ optional embedding version)."""
    if not library_ids:
        return [Chunk.id == "__no_libraries__"]
    settings = get_settings()
    filters: list[ColumnElement[bool]] = [
        Chunk.owner_id == owner_id,
        Chunk.library_id.in_(list(dict.fromkeys(library_ids))),
    ]
    if require_ready:
        filters.append(Document.status == "ready")
    use_ver = (
        settings.retrieve_require_current_embedding
        if require_current_embedding is None
        else require_current_embedding
    )
    if use_ver and settings.embedding_version:
        filters.append(
            or_(
                Chunk.embedding_version == settings.embedding_version,
                Chunk.embedding_version.is_(None),
            )
        )
    return filters
