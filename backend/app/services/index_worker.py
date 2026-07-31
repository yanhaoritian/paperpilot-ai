from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from app.config import get_settings
from app.db import SessionLocal
from app.services.indexing import index_document_with_retries

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_scheduled_lock = threading.Lock()
_scheduled_ids: set[str] = set()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        n = max(1, get_settings().index_worker_threads)
        _executor = ThreadPoolExecutor(max_workers=n, thread_name_prefix="index-worker")
    return _executor


def _run_index(document_id: str) -> None:
    db = SessionLocal()
    try:
        index_document_with_retries(db, document_id)
    except Exception:  # noqa: BLE001
        logger.exception("index worker failed for %s", document_id)
    finally:
        db.close()
        with _scheduled_lock:
            _scheduled_ids.discard(document_id)


def schedule_index(document_id: str, background_tasks=None) -> None:  # noqa: ANN001
    """Schedule indexing on a dedicated thread pool when enabled, else FastAPI BackgroundTasks."""
    with _scheduled_lock:
        if document_id in _scheduled_ids:
            logger.info("index already scheduled for %s", document_id)
            return
        _scheduled_ids.add(document_id)
    settings = get_settings()
    if settings.index_use_thread_pool:
        try:
            _get_executor().submit(_run_index, document_id)
        except Exception:
            with _scheduled_lock:
                _scheduled_ids.discard(document_id)
            raise
        return
    if background_tasks is not None:
        background_tasks.add_task(_run_index, document_id)
        return
    _run_index(document_id)
