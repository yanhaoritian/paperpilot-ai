"""In-process TTL cache for identical RAG answers."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document


class ResponseCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            row = self._store.get(key)
            if not row:
                return None
            expires_at, value = row
            if expires_at <= now:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, *, ttl_ms: int) -> None:
        if ttl_ms <= 0:
            return
        expires_at = time.monotonic() + (ttl_ms / 1000.0)
        with self._lock:
            self._store[key] = (expires_at, value)
            if len(self._store) > 2048:
                self._evict_expired_unlocked(time.monotonic())

    def invalidate_prefix(self, prefix: str) -> int:
        """Remove cached entries in one ownership scope."""
        if not prefix:
            return 0
        with self._lock:
            keys = [key for key in self._store if key.startswith(prefix)]
            for key in keys:
                self._store.pop(key, None)
            return len(keys)

    def invalidate_owner(self, owner_id: str) -> int:
        return self.invalidate_prefix(f"owner:{owner_id}:")

    def _evict_expired_unlocked(self, now: float) -> None:
        dead = [k for k, (exp, _) in self._store.items() if exp <= now]
        for k in dead:
            self._store.pop(k, None)
        # Hard cap: drop oldest half if still huge
        if len(self._store) > 2048:
            ordered = sorted(self._store.items(), key=lambda kv: kv[1][0])
            for k, _ in ordered[: len(ordered) // 2]:
                self._store.pop(k, None)


_QUERY_CACHE = ResponseCache()


def query_cache() -> ResponseCache:
    return _QUERY_CACHE


def corpus_revision(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
) -> str:
    """Return a stable revision shared by every API process for the same corpus."""
    if not library_ids:
        return "empty"
    rows = db.execute(
        select(
            Document.id,
            Document.status,
            Document.updated_at,
            Document.created_at,
            Document.file_hash,
            Document.embedding_version,
            Document.index_attempts,
        )
        .where(
            Document.owner_id == owner_id,
            Document.library_id.in_(sorted(set(library_ids))),
        )
        .order_by(Document.id.asc())
    ).all()
    payload = [
        {
            "id": str(document_id),
            "status": status or "",
            "updated_at": updated_at.isoformat() if updated_at else "",
            "created_at": created_at.isoformat() if created_at else "",
            "file_hash": file_hash or "",
            "embedding_version": embedding_version or "",
            "index_attempts": int(index_attempts or 0),
        }
        for (
            document_id,
            status,
            updated_at,
            created_at,
            file_hash,
            embedding_version,
            index_attempts,
        ) in rows
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_query_cache_key(
    *,
    owner_id: str,
    library_ids: list[str],
    question: str,
    model: str | None,
    temperature: float | None,
    intent: str | None = None,
    history_fingerprint: str = "",
    corpus_revision: str = "",
    prompt_version: str = "",
) -> str:
    payload = {
        "owner_id": owner_id,
        "library_ids": sorted(library_ids),
        "question": (question or "").strip(),
        "model": model or "",
        "temperature": temperature if temperature is not None else "",
        "intent": intent or "",
        "history": history_fingerprint or "",
        "corpus_revision": corpus_revision or "",
        "prompt_version": prompt_version or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    # Keep the opaque digest, but retain an internal ownership prefix so
    # document mutations can invalidate stale answers without a global flush.
    return f"owner:{owner_id}:{digest}"
