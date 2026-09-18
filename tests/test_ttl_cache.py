from deribit_engine.frontend_server.types import _TtlCache


def test_ttl_cache_get_stale_returns_expired_value():
    cache = _TtlCache(ttl_seconds=0.01)
    cache.seed("key", {"ok": True})
    assert cache.try_get("key") == {"ok": True}
    assert cache.get_stale("key") == {"ok": True}


def test_ttl_cache_evicts_coldest_entry_past_the_bound():
    """Fingerprint keys change on every bot write; the store must stay bounded."""
    cache = _TtlCache(ttl_seconds=60, max_entries=3)
    for i in range(50):
        cache.seed(("bundle", f"mtime-{i}"), {"payload": i})
    assert len(cache._store) == 3
    assert cache.get_stale(("bundle", "mtime-0")) is None
    assert cache.get_stale(("bundle", "mtime-49")) == {"payload": 49}


def test_ttl_cache_keeps_a_reused_key_alive_under_churn():
    """A steadily read key ('status') must survive a flood of one-shot keys."""
    cache = _TtlCache(ttl_seconds=60, max_entries=3)
    cache.seed("status", {"live": True})
    for i in range(50):
        cache.seed(("bundle", f"mtime-{i}"), {"payload": i})
        assert cache.try_get("status") == {"live": True}
    assert cache.try_get("status") == {"live": True}


def test_ttl_cache_get_or_set_store_is_bounded():
    cache = _TtlCache(ttl_seconds=0.0, max_entries=2)
    for i in range(20):
        assert cache.get_or_set(f"key-{i}", lambda i=i: i) == i
    assert len(cache._store) == 2
