"""In-process TTL cache for identical RAG answers."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any


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


def make_query_cache_key(
    *,
    owner_id: str,
    library_ids: list[str],
    question: str,
    model: str | None,
    temperature: float | None,
    intent: str | None = None,
    history_fingerprint: str = "",
) -> str:
    payload = {
        "owner_id": owner_id,
        "library_ids": sorted(library_ids),
        "question": (question or "").strip(),
        "model": model or "",
        "temperature": temperature if temperature is not None else "",
        "intent": intent or "",
        "history": history_fingerprint or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
