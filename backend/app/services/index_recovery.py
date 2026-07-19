from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Document, DocumentStatus
from app.services.indexing import index_document

logger = logging.getLogger(__name__)
PERMANENT = frozenset({"EMPTY_TEXT", "NO_CHUNKS"})


def reclaim_and_retry_indexing() -> int:
    """Reset stuck jobs and re-run pending / retryable failed documents.

    Returns number of documents indexed in this pass.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=settings.index_stale_minutes)

    db = SessionLocal()
    try:
        stuck = list(
            db.scalars(
                select(Document).where(Document.status == DocumentStatus.processing.value)
            ).all()
        )
        for doc in stuck:
            stamp = doc.updated_at or doc.created_at
            if stamp is not None:
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp >= stale_before:
                    continue
            doc.status = DocumentStatus.pending.value
            doc.status_detail = "reclaimed_stale_processing"
            logger.warning("reclaimed stale processing document %s", doc.id)

        failed = list(
            db.scalars(
                select(Document).where(
                    Document.status == DocumentStatus.failed.value,
                    Document.index_attempts < settings.index_max_attempts,
                )
            ).all()
        )
        for doc in failed:
            detail = (doc.status_detail or "").strip()
            if detail in PERMANENT:
                continue
            doc.status = DocumentStatus.pending.value
            doc.status_detail = "queued_retry"
            logger.info(
                "queued retry for failed document %s attempts=%s",
                doc.id,
                doc.index_attempts,
            )

        db.commit()

        pending_ids = list(
            db.scalars(
                select(Document.id).where(Document.status == DocumentStatus.pending.value)
            ).all()
        )
    finally:
        db.close()

    for document_id in pending_ids:
        _run_index_safe(document_id)

    if pending_ids:
        logger.info("index recovery scheduled %s document(s)", len(pending_ids))
    return len(pending_ids)


def _run_index_safe(document_id: str) -> None:
    db = SessionLocal()
    try:
        index_document(db, document_id)
    except Exception:  # noqa: BLE001
        logger.exception("recovery index failed for %s", document_id)
    finally:
        db.close()
