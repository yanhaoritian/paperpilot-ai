from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app.config import get_settings
from app.db import SessionLocal
from app.services.indexing import index_document

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        n = max(1, get_settings().index_worker_threads)
        _executor = ThreadPoolExecutor(max_workers=n, thread_name_prefix="index-worker")
    return _executor


def _run_index(document_id: str) -> None:
    db = SessionLocal()
    try:
        index_document(db, document_id)
    except Exception:  # noqa: BLE001
        logger.exception("index worker failed for %s", document_id)
    finally:
        db.close()


def schedule_index(document_id: str, background_tasks=None) -> None:  # noqa: ANN001
    """Schedule indexing on a dedicated thread pool when enabled, else FastAPI BackgroundTasks."""
    settings = get_settings()
    if settings.index_use_thread_pool:
        _get_executor().submit(_run_index, document_id)
        return
    if background_tasks is not None:
        background_tasks.add_task(_run_index, document_id)
        return
    _run_index(document_id)
