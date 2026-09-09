"""Wave 1–2 strategy dynamism: elevated tighten, APR scaler, target-delta bounds.

Naked short put is conservative vs covered call: elevated halt by default,
OTM-only target-delta tilt, and tighten-only MIN_NET_APR.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from conftest import FakeClient, make_config
from test_entry_gates_per_currency import _minimal_snapshot
from test_strategy import _make_btc_call_payload, _make_btc_put_payload

from deribit_engine.engine import DeribitOptionTrialBot, RuntimeContext
from deribit_engine.entry_gates import (
    build_halt_new_entries_by_currency,
    currency_entry_halt_reasons,
    regime_blocks_new_entries,
)
from deribit_engine.models import (
    AccountSummary,
    OptionInstrument,
    OrderBookSnapshot,
    RiskRegime,
    StrategyState,
)
from deribit_engine.strategy import StrategySelector


def _summary(currency: str, equity: str = "1") -> AccountSummary:
    eq = Decimal(equity)
    return AccountSummary(
        currency=currency,
        balance=eq,
        equity=eq,
        available_funds=eq,
        available_withdrawal_funds=eq,
        initial_margin=Decimal("0"),
        maintenance_margin=Decimal("0"),
        delta_total=Decimal("0"),
        options_delta=Decimal("0"),
        options_gamma=Decimal("0"),
        options_theta=Decimal("0"),
        total_equity_usd=Decimal("0"),
        total_initial_margin_usd=Decimal("0"),
        total_maintenance_margin_usd=Decimal("0"),
    )


def test_dynamic_target_delta_never_moves_hard_delta_bounds(tmp_path):
    config = make_config(
        tmp_path,
        enable_dynamic_target_delta=True,
        dynamic_target_delta_vrp_ref=Decimal("0.05"),
        dynamic_target_delta_strength=Decimal("1"),
    )
    hard_put = config.put_delta_bounds("BTC")
    hard_call = config.call_delta_bounds("BTC")
    pref_put = config.preferred_put_delta_bounds("BTC")
    selector = StrategySelector(config)
    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0.20")})
    target = selector._preferred_target_delta("BTC", "put")
    assert config.put_delta_bounds("BTC") == hard_put
    assert config.call_delta_bounds("BTC") == hard_call
    assert pref_put[0] <= target <= pref_put[1]
    assert target != hard_put[0] or pref_put[0] == hard_put[0]
    assert hard_put[1] == config.btc_put_delta_max


def test_regime_blocks_crisis_always_and_elevated_only_when_disallowed():
    assert regime_blocks_new_entries(RiskRegime.CRISIS) is True
    assert regime_blocks_new_entries(RiskRegime.NORMAL) is False
    assert regime_blocks_new_entries(RiskRegime.ELEVATED) is True
    assert regime_blocks_new_entries(RiskRegime.ELEVATED, allow_elevated_entry=True) is False
    assert (
        regime_blocks_new_entries(
            RiskRegime.ELEVATED,
            regime_detail=("data_unavailable: feeds_down",),
            allow_elevated_entry=True,
        )
        is True
    )


def test_entry_gates_elevated_not_halt_when_allowed():
    reasons = currency_entry_halt_reasons(
        currency="BTC",
        regime=RiskRegime.ELEVATED,
        regime_detail=("index_drawdown_elevated",),
        crisis_open_group=False,
        hard_derisk_on_crisis_open_group=True,
        allow_elevated_entry=True,
    )
    assert reasons == []
    by_ccy = build_halt_new_entries_by_currency(
        managed_currencies=("BTC", "ETH"),
        regime_by_currency={"BTC": RiskRegime.ELEVATED, "ETH": RiskRegime.CRISIS},
        regime_detail_by_currency={"BTC": ("index_drawdown_elevated",), "ETH": ("index_drawdown_crisis",)},
        crisis_currencies_with_open_groups=set(),
        hard_derisk_on_crisis_open_group=True,
        portfolio_blocks_all=False,
        allow_elevated_entry=True,
    )
    assert by_ccy["BTC"] is False
    assert by_ccy["ETH"] is True


def test_naked_elevated_halts_by_default(tmp_path):
    config = make_config(tmp_path, option_strategy="naked_short")
    assert config.allows_elevated_entry() is False
    assert config.naked_allow_elevated_entry is False
    by_ccy = build_halt_new_entries_by_currency(
        managed_currencies=("BTC",),
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        regime_detail_by_currency={"BTC": ("index_drawdown_elevated",)},
        crisis_currencies_with_open_groups=set(),
        hard_derisk_on_crisis_open_group=False,
        portfolio_blocks_all=False,
        allow_elevated_entry=config.allows_elevated_entry(),
    )
    assert by_ccy["BTC"] is True


def test_naked_elevated_allows_when_knob_on(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        naked_allow_elevated_entry=True,
    )
    assert config.allows_elevated_entry() is True
    by_ccy = build_halt_new_entries_by_currency(
        managed_currencies=("BTC",),
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        regime_detail_by_currency={"BTC": ("index_drawdown_elevated",)},
        crisis_currencies_with_open_groups=set(),
        hard_derisk_on_crisis_open_group=False,
        portfolio_blocks_all=False,
        allow_elevated_entry=config.allows_elevated_entry(),
    )
    assert by_ccy["BTC"] is False


def test_naked_elevated_halts_when_knob_off(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        naked_allow_elevated_entry=False,
    )
    assert config.allows_elevated_entry() is False
    by_ccy = build_halt_new_entries_by_currency(
        managed_currencies=("BTC",),
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        regime_detail_by_currency={"BTC": ("index_drawdown_elevated",)},
        crisis_currencies_with_open_groups=set(),
        hard_derisk_on_crisis_open_group=False,
        portfolio_blocks_all=False,
        allow_elevated_entry=config.allows_elevated_entry(),
    )
    assert by_ccy["BTC"] is True


def test_covered_call_snapshot_does_not_halt_elevated(tmp_path):
    config = make_config(tmp_path, option_strategy="covered_call")
    engine = DeribitOptionTrialBot(config, FakeClient())
    snapshot = engine._build_portfolio_snapshot(
        state=StrategyState(),
        summaries={"BTC": _summary("BTC", "0.2"), "ETH": _summary("ETH", "3")},
        regime_by_currency={"BTC": RiskRegime.ELEVATED, "ETH": RiskRegime.NORMAL},
        regime_detail_by_currency={
            "BTC": ("index_drawdown_elevated",),
            "ETH": ("market_conditions_normal",),
        },
        future_positions=[],
        orderbook_cache={},
    )
    assert snapshot.halt_new_entries_by_currency["BTC"] is False
    assert snapshot.halt_new_entries_by_currency["ETH"] is False


def test_elevated_tightens_call_delta_max_and_still_builds(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        btc_call_delta_min=Decimal("0.08"),
        btc_call_delta_max=Decimal("0.12"),
        elevated_delta_max_tighten=Decimal("0.02"),
    )
    selector = StrategySelector(config)
    assert selector.effective_delta_bounds("BTC", "call", RiskRegime.NORMAL) == (
        Decimal("0.08"),
        Decimal("0.12"),
    )
    assert selector.effective_delta_bounds("BTC", "call", RiskRegime.ELEVATED) == (
        Decimal("0.08"),
        Decimal("0.10"),
    )
    assert config.btc_call_delta_max == Decimal("0.12")

    inst_ok, book_ok = _make_btc_call_payload(14, 77000, delta="0.09")
    inst_tight, book_tight = _make_btc_call_payload(14, 78000, delta="0.11")
    instrument_ok = OptionInstrument.from_api(inst_ok)
    instrument_tight = OptionInstrument.from_api(inst_tight)
    books = {
        instrument_ok.instrument_name: OrderBookSnapshot.from_api(book_ok),
        instrument_tight.instrument_name: OrderBookSnapshot.from_api(book_tight),
    }

    def loader(name):
        return books[name]

    normal = selector.build_covered_call_candidates(
        [instrument_ok, instrument_tight],
        loader,
        regime=RiskRegime.NORMAL,
        collateral_currency="BTC",
        currency="BTC",
        available_cover_quantity=Decimal("0.2"),
        summary_equity=Decimal("1"),
    )
    elevated = selector.build_covered_call_candidates(
        [instrument_ok, instrument_tight],
        loader,
        regime=RiskRegime.ELEVATED,
        collateral_currency="BTC",
        currency="BTC",
        available_cover_quantity=Decimal("0.2"),
        summary_equity=Decimal("1"),
    )
    crisis = selector.build_covered_call_candidates(
        [instrument_ok, instrument_tight],
        loader,
        regime=RiskRegime.CRISIS,
        collateral_currency="BTC",
        currency="BTC",
        available_cover_quantity=Decimal("0.2"),
        summary_equity=Decimal("1"),
    )
    normal_names = {c.short_leg.instrument_name for c in normal}
    elevated_names = {c.short_leg.instrument_name for c in elevated}
    assert instrument_ok.instrument_name in normal_names
    assert instrument_tight.instrument_name in normal_names
    assert instrument_ok.instrument_name in elevated_names
    assert instrument_tight.instrument_name not in elevated_names
    assert crisis == []


def test_elevated_delta_tighten_never_below_delta_min(tmp_path):
    config = make_config(
        tmp_path,
        btc_call_delta_min=Decimal("0.10"),
        btc_call_delta_max=Decimal("0.11"),
        elevated_delta_max_tighten=Decimal("0.05"),
    )
    selector = StrategySelector(config)
    dmin, dmax = selector.effective_delta_bounds("BTC", "call", RiskRegime.ELEVATED)
    assert dmin == Decimal("0.10")
    assert dmax == Decimal("0.10")
    assert config.btc_call_delta_min == Decimal("0.10")
    assert config.btc_call_delta_max == Decimal("0.11")


def test_dynamic_min_net_apr_low_vs_high_ivr(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        min_net_apr=Decimal("0.10"),
        enable_dynamic_min_net_apr=True,
        dynamic_min_net_apr_max_shift=Decimal("0.005"),
        dynamic_min_net_apr_ivr_ref=Decimal("0.50"),
    )
    selector = StrategySelector(config)
    assert selector.effective_min_net_apr("BTC") == Decimal("0.10")

    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("0")})
    low = selector.effective_min_net_apr("BTC")
    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("1")})
    high = selector.effective_min_net_apr("BTC")
    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("0.50")})
    mid = selector.effective_min_net_apr("BTC")
    assert low == Decimal("0.095")
    assert high == Decimal("0.105")
    assert mid == Decimal("0.10")
    assert low >= config.dynamic_min_net_apr_bound()


def test_dynamic_min_net_apr_floor(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        min_net_apr=Decimal("0.10"),
        enable_dynamic_min_net_apr=True,
        dynamic_min_net_apr_max_shift=Decimal("0.005"),
        dynamic_min_net_apr_floor=Decimal("0.098"),
    )
    selector = StrategySelector(config)
    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("0")})
    assert selector.effective_min_net_apr("BTC") == Decimal("0.098")


def test_dynamic_min_net_apr_gate_low_vs_high_ivr(tmp_path, monkeypatch):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        min_net_apr=Decimal("0.10"),
        enable_dynamic_min_net_apr=True,
        dynamic_min_net_apr_max_shift=Decimal("0.005"),
        entry_dte_min=7,
        entry_dte_max=24,
    )
    selector = StrategySelector(config)
    inst_payload, book_payload = _make_btc_call_payload(14, 77000, delta="0.09")
    instrument = OptionInstrument.from_api(inst_payload)
    book = OrderBookSnapshot.from_api(book_payload)
    monkeypatch.setattr(selector, "_screening_net_apr", lambda **_kwargs: Decimal("0.097"))

    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("0")})
    low_ivr, low_reason = selector._try_build_covered_call_for_quantity(
        instrument=instrument,
        book=book,
        regime=RiskRegime.NORMAL,
        collateral_currency="BTC",
        currency="BTC",
        quantity=Decimal("0.1"),
        summary_equity=Decimal("1"),
    )
    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("1")})
    high_ivr, high_reason = selector._try_build_covered_call_for_quantity(
        instrument=instrument,
        book=book,
        regime=RiskRegime.NORMAL,
        collateral_currency="BTC",
        currency="BTC",
        quantity=Decimal("0.1"),
        summary_equity=Decimal("1"),
    )
    assert low_ivr is not None
    assert high_ivr is None
    assert high_reason == "net_apr_below_min"


def test_scan_candidates_covered_call_elevated_does_not_early_return(tmp_path, monkeypatch):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        min_net_apr=Decimal("0.01"),
        max_groups_per_currency=3,
        max_concurrent_groups=5,
    )
    engine = DeribitOptionTrialBot(config, FakeClient())
    inst_payload, _book = _make_btc_call_payload(14, 77000, delta="0.09")
    instrument = OptionInstrument.from_api(inst_payload)
    called: list[RiskRegime] = []

    def fake_build(*_args, **kwargs):
        called.append(kwargs["regime"])
        return [SimpleNamespace(net_apr=Decimal("0.20"))]

    monkeypatch.setattr(engine.strategy, "build_covered_call_candidates", fake_build)
    monkeypatch.setattr(engine, "_available_covered_call_quantity", lambda *_a, **_k: Decimal("0.2"))
    monkeypatch.setattr(engine, "_covered_call_open_group_count", lambda *_a, **_k: 0)
    monkeypatch.setattr(engine, "_covered_call_spot_exit_blocks_entry", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_underlying_entry_halted", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_book_entry_cooldown_active", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_strategy_at_book_limit", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_currency_index_price", lambda *_a, **_k: Decimal("70000"))
    monkeypatch.setattr(engine.strategy, "take_top_scan_candidates", lambda cands, **_kw: cands)

    snapshot = _minimal_snapshot(
        halt_new_entries=False,
        portfolio_wide_entry_halt=False,
        halt_new_entries_by_currency={"BTC": False},
        halt_entries_by_book={"BTC": False},
        open_max_loss_pct=Decimal("0"),
        regime=RiskRegime.ELEVATED,
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        regime_detail_by_currency={"BTC": ("index_drawdown_elevated",)},
    )
    context = RuntimeContext(
        state=StrategyState(),
        summaries={"BTC": _summary("BTC", "1")},
        open_orders=[],
        positions=[],
        option_positions=[],
        future_positions=[],
        future_markets_by_name={},
        markets_by_currency={"BTC": [instrument]},
        orderbook_cache={},
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        snapshot=snapshot,
    )
    found = engine._scan_candidates(context, currencies=("BTC",), top_n=5)
    assert called == [RiskRegime.ELEVATED]
    assert found and found[0].net_apr == Decimal("0.20")


def test_scan_candidates_crisis_still_skips_covered_call(tmp_path, monkeypatch):
    config = make_config(tmp_path, option_strategy="covered_call")
    engine = DeribitOptionTrialBot(config, FakeClient())
    called = []
    monkeypatch.setattr(
        engine.strategy,
        "build_covered_call_candidates",
        lambda *_a, **_k: called.append("built") or [],
    )
    snapshot = _minimal_snapshot(
        halt_new_entries=False,
        portfolio_wide_entry_halt=False,
        halt_new_entries_by_currency={"BTC": True},
        halt_entries_by_book={},
        open_max_loss_pct=Decimal("0"),
        regime=RiskRegime.CRISIS,
        regime_by_currency={"BTC": RiskRegime.CRISIS},
        regime_detail_by_currency={"BTC": ("index_drawdown_crisis",)},
    )
    context = RuntimeContext(
        state=StrategyState(),
        summaries={"BTC": _summary("BTC", "1")},
        open_orders=[],
        positions=[],
        option_positions=[],
        future_positions=[],
        future_markets_by_name={},
        markets_by_currency={"BTC": []},
        orderbook_cache={},
        regime_by_currency={"BTC": RiskRegime.CRISIS},
        snapshot=snapshot,
    )
    assert engine._scan_candidates(context, currencies=("BTC",), top_n=5) == []
    assert called == []


def test_scan_candidates_naked_elevated_does_not_early_return_when_allowed(tmp_path, monkeypatch):
    config = make_config(tmp_path, option_strategy="naked_short", naked_allow_elevated_entry=True)
    engine = DeribitOptionTrialBot(config, FakeClient())
    inst_payload, _book = _make_btc_put_payload(14, 65000, delta="-0.10")
    instrument = OptionInstrument.from_api(inst_payload)
    called: list[RiskRegime] = []

    def fake_build(*_args, **kwargs):
        called.append(kwargs["regime"])
        return [SimpleNamespace(net_apr=Decimal("0.20"))]

    monkeypatch.setattr(engine.strategy, "build_naked_short_put_candidates", fake_build)
    monkeypatch.setattr(engine, "_underlying_entry_halted", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_book_entry_cooldown_active", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_strategy_at_book_limit", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_strategy_at_concurrent_limit", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_strategy_at_currency_limit", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_naked_im_by_expiry", lambda *_a, **_k: {})
    monkeypatch.setattr(engine, "_naked_candidate_matches_open_group", lambda *_a, **_k: False)
    monkeypatch.setattr(engine, "_currency_index_price", lambda *_a, **_k: Decimal("70000"))
    monkeypatch.setattr(engine.strategy, "take_top_scan_candidates", lambda cands, **_kw: cands)

    snapshot = _minimal_snapshot(
        halt_new_entries=False,
        portfolio_wide_entry_halt=False,
        halt_new_entries_by_currency={"BTC": False},
        halt_entries_by_book={"BTC": False},
        open_max_loss_pct=Decimal("0"),
        regime=RiskRegime.ELEVATED,
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        regime_detail_by_currency={"BTC": ("index_drawdown_elevated",)},
    )
    context = RuntimeContext(
        state=StrategyState(),
        summaries={"BTC": _summary("BTC", "1")},
        open_orders=[],
        positions=[],
        option_positions=[],
        future_positions=[],
        future_markets_by_name={},
        markets_by_currency={"BTC": [instrument]},
        orderbook_cache={},
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        snapshot=snapshot,
    )
    found = engine._scan_candidates(context, currencies=("BTC",), top_n=5)
    assert called == [RiskRegime.ELEVATED]
    assert found and found[0].net_apr == Decimal("0.20")


def test_scan_candidates_naked_elevated_skips_when_knob_off(tmp_path, monkeypatch):
    config = make_config(tmp_path, option_strategy="naked_short", naked_allow_elevated_entry=False)
    engine = DeribitOptionTrialBot(config, FakeClient())
    called = []
    monkeypatch.setattr(
        engine.strategy,
        "build_naked_short_put_candidates",
        lambda *_a, **_k: called.append("built") or [],
    )
    snapshot = _minimal_snapshot(
        halt_new_entries=False,
        portfolio_wide_entry_halt=False,
        halt_new_entries_by_currency={"BTC": True},
        halt_entries_by_book={},
        open_max_loss_pct=Decimal("0"),
        regime=RiskRegime.ELEVATED,
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        regime_detail_by_currency={"BTC": ("index_drawdown_elevated",)},
    )
    context = RuntimeContext(
        state=StrategyState(),
        summaries={"BTC": _summary("BTC", "1")},
        open_orders=[],
        positions=[],
        option_positions=[],
        future_positions=[],
        future_markets_by_name={},
        markets_by_currency={"BTC": []},
        orderbook_cache={},
        regime_by_currency={"BTC": RiskRegime.ELEVATED},
        snapshot=snapshot,
    )
    assert engine._scan_candidates(context, currencies=("BTC",), top_n=5) == []
    assert called == []


def test_elevated_tightens_put_delta_max_and_still_builds(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        min_net_apr=Decimal("0.01"),
        entry_dte_min=7,
        entry_dte_max=24,
        btc_put_delta_min=Decimal("0.08"),
        btc_put_delta_max=Decimal("0.12"),
        elevated_delta_max_tighten=Decimal("0.02"),
        naked_elevated_delta_max_tighten=Decimal("0.02"),
    )
    selector = StrategySelector(config)
    assert selector.effective_delta_bounds("BTC", "put", RiskRegime.NORMAL) == (
        Decimal("0.08"),
        Decimal("0.12"),
    )
    assert selector.effective_delta_bounds("BTC", "put", RiskRegime.ELEVATED) == (
        Decimal("0.08"),
        Decimal("0.10"),
    )
    inst_ok, book_ok = _make_btc_put_payload(14, 62000, delta="-0.09")
    inst_tight, book_tight = _make_btc_put_payload(14, 61000, delta="-0.11")
    instrument_ok = OptionInstrument.from_api(inst_ok)
    instrument_tight = OptionInstrument.from_api(inst_tight)
    books = {
        instrument_ok.instrument_name: OrderBookSnapshot.from_api(book_ok),
        instrument_tight.instrument_name: OrderBookSnapshot.from_api(book_tight),
    }

    def loader(name):
        return books[name]

    kwargs = dict(
        summary_equity=Decimal("1"),
        summary_maintenance_margin=Decimal("0"),
        collateral_currency="BTC",
        currency="BTC",
        existing_im_by_expiry={},
    )
    normal = selector.build_naked_short_put_candidates(
        [instrument_ok, instrument_tight], loader, regime=RiskRegime.NORMAL, **kwargs
    )
    elevated = selector.build_naked_short_put_candidates(
        [instrument_ok, instrument_tight], loader, regime=RiskRegime.ELEVATED, **kwargs
    )
    crisis = selector.build_naked_short_put_candidates(
        [instrument_ok, instrument_tight], loader, regime=RiskRegime.CRISIS, **kwargs
    )
    normal_names = {c.short_leg.instrument_name for c in normal}
    elevated_names = {c.short_leg.instrument_name for c in elevated}
    assert instrument_ok.instrument_name in normal_names
    assert instrument_tight.instrument_name in normal_names
    assert instrument_ok.instrument_name in elevated_names
    assert instrument_tight.instrument_name not in elevated_names
    assert crisis == []


def test_bull_put_does_not_allow_elevated_entry(tmp_path):
    config = make_config(tmp_path, option_strategy="bull_put_spread")
    assert config.allows_elevated_entry() is False


def test_dynamic_min_net_apr_vrp_fallback_when_ivr_missing(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="covered_call",
        min_net_apr=Decimal("0.10"),
        enable_dynamic_min_net_apr=True,
        dynamic_min_net_apr_max_shift=Decimal("0.005"),
        dynamic_target_delta_vrp_ref=Decimal("0.05"),
    )
    selector = StrategySelector(config)
    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0")})
    assert selector.effective_min_net_apr("BTC") == Decimal("0.095")
    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0.10")})
    assert selector.effective_min_net_apr("BTC") == Decimal("0.105")


def test_effective_max_groups_tighten_default_off(tmp_path):
    config = make_config(tmp_path, max_groups_per_currency=3, elevated_max_groups_tighten=0)
    assert config.effective_max_groups_per_currency(elevated=True) == 3
    tight = make_config(tmp_path, max_groups_per_currency=3, elevated_max_groups_tighten=1)
    assert tight.effective_max_groups_per_currency(elevated=True) == 2
    assert tight.effective_max_groups_per_currency(elevated=False) == 3
    one = make_config(tmp_path, max_groups_per_currency=1, elevated_max_groups_tighten=1)
    assert one.effective_max_groups_per_currency(elevated=True) == 1


def test_naked_dynamic_target_delta_is_otm_only(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        enable_dynamic_target_delta=True,
        dynamic_target_delta_vrp_ref=Decimal("0.05"),
        dynamic_target_delta_strength=Decimal("1"),
    )
    selector = StrategySelector(config)
    pdmin, pdmax = config.preferred_put_delta_bounds("BTC")
    center = (pdmin + pdmax) / Decimal("2")

    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0.10")})
    rich = selector._preferred_target_delta("BTC", "put")
    assert rich < center
    assert rich >= pdmin

    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0")})
    thin = selector._preferred_target_delta("BTC", "put")
    assert thin == center


def test_naked_dynamic_target_delta_can_tilt_closer_when_opted_in(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        enable_dynamic_target_delta=True,
        dynamic_target_delta_vrp_ref=Decimal("0.05"),
        dynamic_target_delta_strength=Decimal("1"),
        naked_dynamic_delta_allow_closer=True,
    )
    selector = StrategySelector(config)
    pdmin, pdmax = config.preferred_put_delta_bounds("BTC")
    center = (pdmin + pdmax) / Decimal("2")
    selector.update_vol_entry_context(iv_minus_rv_by_currency={"BTC": Decimal("0")})
    thin = selector._preferred_target_delta("BTC", "put")
    assert thin > center
    assert thin <= pdmax


def test_naked_dynamic_min_net_apr_tighten_only(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        min_net_apr=Decimal("0.10"),
        enable_dynamic_min_net_apr=True,
        dynamic_min_net_apr_max_shift=Decimal("0.005"),
        dynamic_min_net_apr_ivr_ref=Decimal("0.50"),
    )
    selector = StrategySelector(config)
    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("0")})
    assert selector.effective_min_net_apr("BTC") == Decimal("0.10")
    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("1")})
    assert selector.effective_min_net_apr("BTC") == Decimal("0.105")


def test_naked_dynamic_min_net_apr_loosens_when_opted_in(tmp_path):
    config = make_config(
        tmp_path,
        option_strategy="naked_short",
        min_net_apr=Decimal("0.10"),
        enable_dynamic_min_net_apr=True,
        dynamic_min_net_apr_max_shift=Decimal("0.005"),
        dynamic_min_net_apr_ivr_ref=Decimal("0.50"),
        naked_dynamic_min_net_apr_allow_loosen=True,
    )
    selector = StrategySelector(config)
    selector.update_vol_entry_context(iv_rank_by_currency={"BTC": Decimal("0")})
    assert selector.effective_min_net_apr("BTC") == Decimal("0.095")


def test_naked_elevated_default_tighten_is_larger_than_covered_call(tmp_path):
    naked = make_config(
        tmp_path,
        option_strategy="naked_short",
        btc_put_delta_min=Decimal("0.08"),
        btc_put_delta_max=Decimal("0.16"),
    )
    covered = make_config(
        tmp_path,
        option_strategy="covered_call",
        btc_call_delta_min=Decimal("0.08"),
        btc_call_delta_max=Decimal("0.16"),
    )
    assert naked.elevated_delta_tighten_amount() == Decimal("0.04")
    assert covered.elevated_delta_tighten_amount() == Decimal("0.02")
    naked_sel = StrategySelector(naked)
    covered_sel = StrategySelector(covered)
    assert naked_sel.effective_delta_bounds("BTC", "put", RiskRegime.ELEVATED) == (
        Decimal("0.08"),
        Decimal("0.12"),
    )
    assert covered_sel.effective_delta_bounds("BTC", "call", RiskRegime.ELEVATED) == (
        Decimal("0.08"),
        Decimal("0.14"),
    )
