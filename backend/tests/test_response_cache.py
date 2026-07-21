from app.services.response_cache import ResponseCache, make_query_cache_key


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
