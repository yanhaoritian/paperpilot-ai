"""Durable index job queue helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IndexJob, IndexJobStatus

logger = logging.getLogger(__name__)


def enqueue_index_job(
    db: Session,
    *,
    document_id: str,
    owner_id: str,
    commit: bool = True,
) -> IndexJob | None:
    settings = get_settings()
    if not settings.index_job_enabled:
        return None
    existing = db.scalar(
        select(IndexJob).where(
            IndexJob.document_id == document_id,
            IndexJob.status.in_([IndexJobStatus.pending.value, IndexJobStatus.running.value]),
        )
    )
    if existing:
        return existing
    job = IndexJob(
        document_id=document_id,
        owner_id=owner_id,
        status=IndexJobStatus.pending.value,
        attempts=0,
    )
    db.add(job)
    if commit:
        db.commit()
        db.refresh(job)
    else:
        db.flush()
    return job


def mark_job_running(db: Session, document_id: str) -> None:
    if not get_settings().index_job_enabled:
        return
    job = db.scalar(
        select(IndexJob)
        .where(
            IndexJob.document_id == document_id,
            IndexJob.status.in_(
                [
                    IndexJobStatus.pending.value,
                    IndexJobStatus.running.value,
                    IndexJobStatus.failed.value,
                ]
            ),
        )
        .order_by(IndexJob.created_at.desc())
    )
    if not job:
        return
    job.status = IndexJobStatus.running.value
    job.attempts = int(job.attempts or 0) + 1
    job.started_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


def mark_job_done(db: Session, document_id: str) -> None:
    if not get_settings().index_job_enabled:
        return
    job = db.scalar(
        select(IndexJob)
        .where(IndexJob.document_id == document_id)
        .order_by(IndexJob.created_at.desc())
    )
    if not job:
        return
    job.status = IndexJobStatus.done.value
    job.finished_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    job.last_error = None
    job.lease_owner = None
    job.lease_expires_at = None
    db.commit()


def mark_job_retrying(db: Session, document_id: str, error: str) -> None:
    """Keep ownership while the same worker performs an in-process retry."""
    if not get_settings().index_job_enabled:
        return
    job = db.scalar(
        select(IndexJob)
        .where(IndexJob.document_id == document_id)
        .order_by(IndexJob.created_at.desc())
    )
    if not job:
        return
    job.status = IndexJobStatus.running.value
    job.updated_at = datetime.now(timezone.utc)
    job.last_error = (error or "")[:500]
    job.finished_at = None
    db.commit()


def mark_job_failed(db: Session, document_id: str, error: str) -> None:
    if not get_settings().index_job_enabled:
        return
    job = db.scalar(
        select(IndexJob)
        .where(IndexJob.document_id == document_id)
        .order_by(IndexJob.created_at.desc())
    )
    if not job:
        return
    job.status = IndexJobStatus.failed.value
    job.finished_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    job.last_error = (error or "")[:500]
    job.lease_owner = None
    job.lease_expires_at = None
    db.commit()
