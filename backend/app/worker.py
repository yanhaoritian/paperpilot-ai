"""Standalone durable indexing worker.

Run with:
    python -m app.worker
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    Document,
    DocumentStatus,
    IndexJob,
    IndexJobStatus,
    WorkerHeartbeat,
)
from app.services.index_recovery import reclaim_and_retry_indexing
from app.services.indexing import index_document_with_retries

logger = logging.getLogger("paperpilot.worker")
_stop = threading.Event()
_worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _lease_duration_seconds(settings) -> int:  # noqa: ANN001
    heartbeat_seconds = max(1, int(settings.index_worker_heartbeat_seconds))
    return max(
        30,
        int(settings.index_worker_lease_seconds),
        heartbeat_seconds * 3,
    )


def _claim_next_job() -> str | None:
    """Atomically claim one pending job.

    PostgreSQL workers use SKIP LOCKED, so multiple worker processes can poll
    the same queue without executing one document twice.
    """
    settings = get_settings()
    db = SessionLocal()
    try:
        stmt = (
            select(IndexJob)
            .where(IndexJob.status == IndexJobStatus.pending.value)
            .order_by(IndexJob.created_at.asc())
            .limit(1)
        )
        if not settings.is_sqlite:
            stmt = stmt.with_for_update(skip_locked=True)
        job = db.scalar(stmt)
        if not job:
            db.rollback()
            return None

        doc = db.get(Document, job.document_id)
        if not doc:
            job.status = IndexJobStatus.failed.value
            job.last_error = "document_missing"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return None
        if doc.status == DocumentStatus.ready.value:
            job.status = IndexJobStatus.done.value
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return None
        if (
            doc.status == DocumentStatus.failed.value
            and int(doc.index_attempts or 0) >= settings.index_max_attempts
        ):
            job.status = IndexJobStatus.failed.value
            job.last_error = doc.status_detail or "attempts_exhausted"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return None

        claimed_at = datetime.now(timezone.utc)
        job.status = IndexJobStatus.running.value
        job.started_at = claimed_at
        job.updated_at = claimed_at
        job.lease_owner = _worker_id
        job.lease_expires_at = claimed_at + timedelta(
            seconds=_lease_duration_seconds(settings)
        )
        # Mark processing in the same claim transaction. A recovery pass will
        # then leave this fresh claim alone and only reclaim it after staleness.
        doc.status = DocumentStatus.processing.value
        doc.status_detail = "worker_claimed"
        doc.updated_at = claimed_at
        db.commit()
        return str(job.document_id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _run_claimed_job(document_id: str) -> None:
    settings = get_settings()
    lease_stop = threading.Event()
    lease_interval = min(
        max(1, int(settings.index_worker_heartbeat_seconds)),
        max(1, int(settings.index_worker_lease_seconds) // 3),
    )
    lease_thread = threading.Thread(
        target=_job_lease_loop,
        args=(document_id, lease_stop, lease_interval),
        name=f"index-job-lease-{document_id[:8]}",
        daemon=True,
    )
    lease_thread.start()
    db = SessionLocal()
    try:
        index_document_with_retries(db, document_id)
    except Exception:  # noqa: BLE001
        logger.exception("worker failed document=%s", document_id)
    finally:
        db.close()
        lease_stop.set()
        lease_thread.join(timeout=3)


def _renew_job_lease(document_id: str) -> bool | None:
    settings = get_settings()
    db = SessionLocal()
    try:
        job = db.scalar(
            select(IndexJob)
            .where(
                IndexJob.document_id == document_id,
                IndexJob.status == IndexJobStatus.running.value,
                IndexJob.lease_owner == _worker_id,
            )
            .order_by(IndexJob.created_at.desc())
        )
        if job is None:
            db.rollback()
            return False
        now = datetime.now(timezone.utc)
        job.updated_at = now
        job.lease_expires_at = now + timedelta(seconds=_lease_duration_seconds(settings))
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("job lease renewal failed document=%s", document_id)
        return None
    finally:
        db.close()


def _job_lease_loop(
    document_id: str,
    lease_stop: threading.Event,
    interval_seconds: int,
) -> None:
    while not lease_stop.wait(max(1, interval_seconds)):
        renewed = _renew_job_lease(document_id)
        if renewed is False:
            return


def _write_heartbeat() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        row = db.get(WorkerHeartbeat, _worker_id)
        if row is None:
            row = WorkerHeartbeat(
                worker_id=_worker_id,
                worker_kind="index",
                started_at=now,
                last_seen_at=now,
                extra={"pid": os.getpid(), "host": socket.gethostname()},
            )
            db.add(row)
        else:
            row.last_seen_at = now
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("worker heartbeat failed")
    finally:
        db.close()


def _heartbeat_loop(interval_seconds: int) -> None:
    while not _stop.is_set():
        _write_heartbeat()
        _stop.wait(max(1, interval_seconds))


def _remove_heartbeat() -> None:
    db = SessionLocal()
    try:
        row = db.get(WorkerHeartbeat, _worker_id)
        if row is not None:
            db.delete(row)
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("worker heartbeat cleanup failed")
    finally:
        db.close()


def run_worker(*, once: bool = False) -> None:
    settings = get_settings()
    init_db()
    reclaim_and_retry_indexing(execute=False)
    poll_seconds = max(0.2, float(settings.index_worker_poll_seconds))
    recovery_seconds = max(10, int(settings.index_worker_recovery_seconds))
    next_recovery = 0.0
    logger.info(
        "index worker started db=%s poll=%.1fs",
        "sqlite" if settings.is_sqlite else "postgres",
        poll_seconds,
    )
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(max(1, int(settings.index_worker_heartbeat_seconds)),),
        name="index-worker-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    import time

    try:
        while not _stop.is_set():
            now = time.monotonic()
            if now >= next_recovery:
                reclaim_and_retry_indexing(execute=False)
                next_recovery = now + recovery_seconds
            document_id = _claim_next_job()
            if document_id:
                logger.info("claimed document=%s", document_id)
                _run_claimed_job(document_id)
            elif once:
                break
            else:
                _stop.wait(poll_seconds)
    finally:
        _stop.set()
        heartbeat_thread.join(timeout=3)
        _remove_heartbeat()
        logger.info("index worker stopped")


def _handle_stop(_signum, _frame) -> None:  # noqa: ANN001
    _stop.set()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='{"level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    )
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_stop)
    run_worker()


if __name__ == "__main__":
    main()
