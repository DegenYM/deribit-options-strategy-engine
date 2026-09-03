"""Regime liquidity probe caching.

The probe is the dominant cost of a dashboard status rebuild. It is cached on
the dashboard read path only — live entry gates must never act on a stale
liquidity read.
"""

from conftest import FakeClient, make_config

from deribit_engine.engine import DeribitOptionTrialBot
from deribit_engine.engine import regime as regime_module
from deribit_engine.models import OptionInstrument


def _engine_and_markets(tmp_path, **overrides):
    config = make_config(tmp_path, min_liquid_expiries_required=1, **overrides)
    client = FakeClient()
    engine = DeribitOptionTrialBot(config, client)
    markets = [OptionInstrument.from_api(item) for item in client.get_instruments("BTC", kind="option", expired=False)]
    return engine, markets


def _count_probes(engine, monkeypatch):
    calls: list[str] = []
    original = engine.strategy.core_regime_liquidity_detail

    def counting(currency, markets, loader):
        calls.append(currency)
        return original(currency, markets, loader)

    monkeypatch.setattr(engine.strategy, "core_regime_liquidity_detail", counting)
    return calls


def test_dashboard_path_reuses_cached_liquidity(tmp_path, monkeypatch):
    regime_module._REGIME_LIQUIDITY_CACHE.clear()
    engine, markets = _engine_and_markets(tmp_path)
    calls = _count_probes(engine, monkeypatch)

    first = engine._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)
    second = engine._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)

    assert len(calls) == 1
    assert first == second


def test_uncached_call_still_probes_every_time(tmp_path, monkeypatch):
    """cache_liquidity=False is the explicit opt-out."""
    regime_module._REGIME_LIQUIDITY_CACHE.clear()
    engine, markets = _engine_and_markets(tmp_path)
    calls = _count_probes(engine, monkeypatch)

    for _ in range(2):
        engine._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={})

    assert len(calls) == 2


def test_zero_ttl_disables_the_cache(tmp_path, monkeypatch):
    regime_module._REGIME_LIQUIDITY_CACHE.clear()
    monkeypatch.setenv("DERIBIT_REGIME_LIQUIDITY_CACHE_TTL_SEC", "0")
    engine, markets = _engine_and_markets(tmp_path)
    calls = _count_probes(engine, monkeypatch)

    for _ in range(2):
        engine._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)

    assert len(calls) == 2


def test_verdict_is_shared_with_a_sibling_process(tmp_path, monkeypatch):
    """A frontend and a live bot on the same config must reuse each other's
    probe rather than each paying the full order-book walk."""
    regime_module._REGIME_LIQUIDITY_CACHE.clear()
    first, markets = _engine_and_markets(tmp_path)
    first_calls = _count_probes(first, monkeypatch)
    first._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)
    assert len(first_calls) == 1

    # Sibling process: same config, cold in-process cache, shared store intact.
    regime_module._REGIME_LIQUIDITY_CACHE.clear()
    second, _ = _engine_and_markets(tmp_path)
    second_calls = _count_probes(second, monkeypatch)
    second._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)

    assert second_calls == []


def test_cache_is_not_shared_across_configs(tmp_path, monkeypatch):
    """A different strategy config means different entry gates, so it must
    re-probe rather than inherit another account's answer."""
    regime_module._REGIME_LIQUIDITY_CACHE.clear()
    engine_a, markets = _engine_and_markets(tmp_path, option_strategy="naked_short")
    engine_b, _ = _engine_and_markets(tmp_path, option_strategy="covered_call")
    calls_a = _count_probes(engine_a, monkeypatch)
    calls_b = _count_probes(engine_b, monkeypatch)

    engine_a._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)
    engine_b._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)

    assert len(calls_a) == 1
    assert len(calls_b) == 1


def test_cache_expires(tmp_path, monkeypatch):
    regime_module._REGIME_LIQUIDITY_CACHE.clear()
    engine, markets = _engine_and_markets(tmp_path)
    calls = _count_probes(engine, monkeypatch)

    engine._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)
    monkeypatch.setenv("DERIBIT_REGIME_LIQUIDITY_CACHE_TTL_SEC", "0.0001")
    regime_module._REGIME_LIQUIDITY_CACHE.clear()
    engine._determine_regime_with_detail("BTC", markets=markets, orderbook_cache={}, cache_liquidity=True)

    assert len(calls) == 2
