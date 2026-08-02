from datetime import datetime, timezone

from app.services.response_cache import (
    ResponseCache,
    corpus_revision,
    make_query_cache_key,
)


def test_response_cache_ttl_and_key():
    cache = ResponseCache()
    key = make_query_cache_key(
        owner_id="u1",
        library_ids=["b", "a"],
        question="hello",
        model=None,
        temperature=0.2,
        intent="qa",
    )
    key2 = make_query_cache_key(
        owner_id="u1",
        library_ids=["a", "b"],
        question="hello",
        model=None,
        temperature=0.2,
        intent="qa",
    )
    assert key == key2
    cache.set(key, {"answer": "ok"}, ttl_ms=60_000)
    assert cache.get(key) == {"answer": "ok"}
    cache.set(key, {"answer": "gone"}, ttl_ms=0)
    # ttl 0 means set is no-op; previous value remains until overwritten with positive ttl
    assert cache.get(key) == {"answer": "ok"}


def test_response_cache_can_invalidate_one_owner():
    cache = ResponseCache()
    key_a = make_query_cache_key(
        owner_id="u1",
        library_ids=["a"],
        question="hello",
        model=None,
        temperature=0.2,
    )
    key_b = make_query_cache_key(
        owner_id="u2",
        library_ids=["a"],
        question="hello",
        model=None,
        temperature=0.2,
    )
    cache.set(key_a, {"answer": "a"}, ttl_ms=60_000)
    cache.set(key_b, {"answer": "b"}, ttl_ms=60_000)
    assert cache.invalidate_owner("u1") == 1
    assert cache.get(key_a) is None
    assert cache.get(key_b) == {"answer": "b"}


def test_response_cache_key_tracks_corpus_prompt_and_resolved_model():
    base = {
        "owner_id": "u1",
        "library_ids": ["a"],
        "question": "hello",
        "model": "resolved-default-model",
        "temperature": 0.2,
        "corpus_revision": "rev-1",
        "prompt_version": "prompt-1",
    }
    original = make_query_cache_key(**base)
    assert original != make_query_cache_key(
        **{**base, "corpus_revision": "rev-2"}
    )
    assert original != make_query_cache_key(
        **{**base, "prompt_version": "prompt-2"}
    )
    assert original != make_query_cache_key(
        **{**base, "model": "new-default-model"}
    )
    assert original != make_query_cache_key(
        **{**base, "skill_id": "paper_deep_read", "skill_version": "v1"}
    )


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RevisionSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _statement):
        return _Rows(self._rows)


def test_corpus_revision_changes_with_document_state():
    now = datetime.now(timezone.utc)
    row = ("doc-1", "ready", now, now, "hash", "v1", 1)
    first = corpus_revision(
        _RevisionSession([row]),
        owner_id="u1",
        library_ids=["a"],
    )
    second = corpus_revision(
        _RevisionSession([(*row[:-1], 2)]),
        owner_id="u1",
        library_ids=["a"],
    )
    assert first != second
