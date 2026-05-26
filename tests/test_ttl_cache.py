from deribit_demo.frontend_server.types import _TtlCache


def test_ttl_cache_get_stale_returns_expired_value():
    cache = _TtlCache(ttl_seconds=0.01)
    cache.seed("key", {"ok": True})
    assert cache.try_get("key") == {"ok": True}
    assert cache.get_stale("key") == {"ok": True}
