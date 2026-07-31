from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Document, DocumentStatus, IndexJob, IndexJobStatus
from app.services.indexing import index_document_with_retries

logger = logging.getLogger(__name__)
PERMANENT = frozenset({"EMPTY_TEXT", "NO_CHUNKS"})


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lease_is_active(job: IndexJob | None, now: datetime) -> bool:
    if job is None or job.status != IndexJobStatus.running.value:
        return False
    expires_at = _as_utc(job.lease_expires_at)
    return bool(expires_at and expires_at > now)


def _latest_job(db, document_id: str) -> IndexJob | None:  # noqa: ANN001
    stmt = (
        select(IndexJob)
        .where(IndexJob.document_id == document_id)
        .order_by(IndexJob.created_at.desc())
    )
    if not get_settings().is_sqlite:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def reclaim_and_retry_indexing(*, execute: bool = True) -> int:
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
            job = _latest_job(db, str(doc.id))
            if _lease_is_active(job, now):
                continue
            # Leased jobs are reclaimed immediately after lease expiry. Legacy
            # or inline jobs without a lease keep the wider stale threshold.
            if job is None or job.lease_expires_at is None:
                stamp = _as_utc(
                    (job.updated_at if job is not None else None)
                    or doc.updated_at
                    or doc.created_at
                )
                if stamp is not None and stamp >= stale_before:
                    continue
            doc.status = DocumentStatus.pending.value
            doc.status_detail = "reclaimed_stale_processing"
            if job is not None and job.status == IndexJobStatus.running.value:
                job.status = IndexJobStatus.pending.value
                job.lease_owner = None
                job.lease_expires_at = None
                job.started_at = None
                job.updated_at = now
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

        pending_docs = list(
            db.scalars(
                select(Document).where(Document.status == DocumentStatus.pending.value)
            ).all()
        )
        # Reconcile durable jobs with document state. This is also the crash
        # recovery path for an external worker that died after claiming a job.
        pending_ids: list[str] = []
        for doc in pending_docs:
            job = _latest_job(db, str(doc.id))
            if _lease_is_active(job, now):
                # The current worker is between retry attempts; do not let a
                # second worker steal the document during its backoff.
                continue
            if job and job.status != IndexJobStatus.done.value:
                job.status = IndexJobStatus.pending.value
                job.started_at = None
                job.finished_at = None
                job.updated_at = now
                job.lease_owner = None
                job.lease_expires_at = None
            elif not job or job.status == IndexJobStatus.done.value:
                db.add(
                    IndexJob(
                        document_id=doc.id,
                        owner_id=doc.owner_id,
                        status=IndexJobStatus.pending.value,
                        attempts=0,
                    )
                )
            pending_ids.append(str(doc.id))
        db.commit()
    finally:
        db.close()

    if execute:
        for document_id in pending_ids:
            _run_index_safe(document_id)

    if pending_ids:
        logger.info(
            "index recovery %s %s document(s)",
            "executed" if execute else "requeued",
            len(pending_ids),
        )
    return len(pending_ids)


def _run_index_safe(document_id: str) -> None:
    db = SessionLocal()
    try:
        index_document_with_retries(db, document_id)
    except Exception:  # noqa: BLE001
        logger.exception("recovery index failed for %s", document_id)
    finally:
        db.close()
