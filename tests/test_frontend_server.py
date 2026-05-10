from __future__ import annotations

from decimal import Decimal

import deribit_demo.frontend_server as frontend_server
from deribit_demo.current_stress import CurrentStressResult
from deribit_demo.frontend_server import DashboardAccount
from deribit_demo.models import StrategyState, TradeGroup
from deribit_demo.state import StrategyStateStore, performance_exclusions_path
from deribit_demo.stress import black_swan_strategy_analysis
from conftest import future_expiry, make_config


def _stress_result(strategy: str, book: str, loss: Decimal) -> CurrentStressResult:
    return CurrentStressResult(
        generated_at="now",
        option_strategy=strategy,
        strategy_analysis=black_swan_strategy_analysis(strategy),
        index_by_ccy={"BTC": Decimal("100000"), "ETH": Decimal("5000"), "USDC": Decimal("1")},
        equity_usdc_by_book={book: Decimal("1000")},
        positions=[{"instrument_name": f"{strategy}-leg"}],
        scenarios=[
            {
                "shock": "-0.10",
                "slippage": "0.05",
                "loss_usdc_total": str(loss),
                "loss_by_book_usdc": {book: str(loss)},
                "components_total_usdc": {"base_move_usdc": str(loss)},
                "worst_legs": [{"instrument_name": f"{strategy}-leg", "loss_usdc": str(loss)}],
            }
        ],
        notes=[],
    )


def test_closed_groups_payload_excludes_performance_groups(tmp_path):
    state_path = tmp_path / "covered_call.json"
    state = StrategyState()
    for group_id in ("0001", "0002"):
        group = TradeGroup(
            group_id=group_id,
            currency="ETH",
            collateral_currency="ETH",
            quantity=Decimal("1"),
            entry_timestamp_ms=1,
            expiration_timestamp_ms=future_expiry(7),
            short_instrument_name=f"ETH-14APR30-26{group_id[-1]}0-C",
            short_strike=Decimal("2600"),
            entry_credit=Decimal("30"),
            original_entry_credit=Decimal("30"),
            max_loss=Decimal("250"),
            regime_at_entry="normal",
            status="closed",
            closed_timestamp_ms=2,
            realized_pnl=Decimal("1"),
            option_type="call",
            strategy="covered_call",
        )
        state.groups.append(group)
    StrategyStateStore(state_path).save(state)
    performance_exclusions_path(state_path).write_text(
        '{"excluded_group_ids": ["0001"]}',
        encoding="utf-8",
    )

    payload = frontend_server._closed_groups_payload(state_path)

    assert [group["group_id"] for group in payload["closed"]] == ["0002"]
    assert payload["performance_excluded_closed_group_count"] == 1


def test_aggregate_stress_returns_per_strategy_sections(tmp_path, monkeypatch):
    put_cfg = make_config(tmp_path, option_strategy="naked_short", client_id="id-put")
    call_cfg = make_config(tmp_path, option_strategy="covered_call", client_id="id-call")
    put_env = tmp_path / "put.env"
    call_env = tmp_path / "call.env"
    configs = {put_env: put_cfg, call_env: call_cfg}
    accounts = [
        DashboardAccount("put", put_env, put_cfg, put_cfg.state_file, tmp_path / "ledger-put"),
        DashboardAccount("call", call_env, call_cfg, call_cfg.state_file, tmp_path / "ledger-call"),
    ]

    def fake_compute_current_stress(config, _client, *, shocks):
        assert shocks == [Decimal("-0.10")]
        if config.option_strategy == "covered_call":
            return _stress_result("covered_call", "BTC", Decimal("-50"))
        return _stress_result("naked_short", "USDC", Decimal("-100"))

    monkeypatch.setattr(frontend_server, "load_config", lambda env_file, require_private=False: configs[env_file])
    monkeypatch.setattr(frontend_server, "DeribitClient", lambda _config: object())
    monkeypatch.setattr(frontend_server, "compute_current_stress", fake_compute_current_stress)

    payload = frontend_server._aggregate_stress(accounts, shocks=[Decimal("-0.10")])

    assert payload["option_strategy"] == "multi_account"
    assert payload["scenarios"][0]["loss_usdc_total"] == Decimal("-150")
    assert payload["scenarios"][0]["loss_usdc_pct_of_total_equity"] == Decimal("-0.075")

    per_strategy = {item["option_strategy"]: item for item in payload["strategy_stresses"]}
    assert set(per_strategy) == {"naked_short", "covered_call"}
    assert per_strategy["naked_short"]["scenarios"][0]["loss_by_book_usdc"] == {"USDC": Decimal("-100")}
    assert per_strategy["covered_call"]["strategy_analysis"]["label"] == "covered_call"
    assert per_strategy["covered_call"]["accounts"][0]["name"] == "call"


def test_aggregate_stress_dedupes_shared_api_credentials(tmp_path, monkeypatch):
    """Same DERIBIT_CLIENT_ID/SECRET in two rows is one exchange account — aggregate risk once."""
    cfg_a = make_config(tmp_path, option_strategy="naked_short", client_id="dup")
    cfg_b = make_config(tmp_path, option_strategy="covered_call", client_id="dup")
    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    configs = {env_a: cfg_a, env_b: cfg_b}
    accounts = [
        DashboardAccount("put", env_a, cfg_a, cfg_a.state_file, tmp_path / "ledger-a"),
        DashboardAccount("call", env_b, cfg_b, cfg_b.state_file, tmp_path / "ledger-b"),
    ]

    def fake_compute_current_stress(config, _client, *, shocks):
        assert shocks == [Decimal("-0.10")]
        if config.option_strategy == "covered_call":
            return _stress_result("covered_call", "BTC", Decimal("-50"))
        return _stress_result("naked_short", "USDC", Decimal("-100"))

    monkeypatch.setattr(frontend_server, "load_config", lambda env_file, require_private=False: configs[env_file])
    monkeypatch.setattr(frontend_server, "DeribitClient", lambda _config: object())
    monkeypatch.setattr(frontend_server, "compute_current_stress", fake_compute_current_stress)

    payload = frontend_server._aggregate_stress(accounts, shocks=[Decimal("-0.10")])

    assert payload["option_strategy"] == "multi_account"
    assert payload["scenarios"][0]["loss_usdc_total"] == Decimal("-100")
    assert payload["scenarios"][0]["loss_usdc_pct_of_total_equity"] == Decimal("-0.1")

    per_strategy = {item["option_strategy"]: item for item in payload["strategy_stresses"]}
    assert set(per_strategy) == {"naked_short", "covered_call"}
    assert len(per_strategy["naked_short"]["accounts"]) == 1
    assert len(per_strategy["covered_call"]["accounts"]) == 1


def test_aggregate_stress_normalizes_put_spread_alias(tmp_path, monkeypatch):
    cfg = make_config(tmp_path, option_strategy="bull_put_spread")
    env = tmp_path / "spread.env"
    accounts = [DashboardAccount("spread", env, cfg, cfg.state_file, tmp_path / "ledger-spread")]

    def fake_compute_current_stress(config, _client, *, shocks):
        assert config.option_strategy == "bull_put_spread"
        return _stress_result("put_spread", "USDC", Decimal("-25"))

    monkeypatch.setattr(frontend_server, "load_config", lambda env_file, require_private=False: cfg)
    monkeypatch.setattr(frontend_server, "DeribitClient", lambda _config: object())
    monkeypatch.setattr(frontend_server, "compute_current_stress", fake_compute_current_stress)

    payload = frontend_server._aggregate_stress(accounts, shocks=[Decimal("-0.10")])

    per_strategy = {item["option_strategy"]: item for item in payload["strategy_stresses"]}
    assert set(per_strategy) == {"bull_put_spread"}
    assert per_strategy["bull_put_spread"]["strategy_analysis"]["label"] == "bull_put_spread"


def test_aggregate_portfolios_sums_spot_excluded_day_pnl(tmp_path):
    cfg = make_config(tmp_path)
    accounts = [
        DashboardAccount("a", tmp_path / "a.env", cfg, cfg.state_file, tmp_path / "ledger-a"),
        DashboardAccount("b", tmp_path / "b.env", cfg, cfg.state_file, tmp_path / "ledger-b"),
    ]
    statuses = [
        {
            "portfolio": {
                "total_equity_usdc": Decimal("1000"),
                "day_start_equity_usdc": Decimal("950"),
                "day_net_flow_usdc": Decimal("10"),
                "day_pnl_usdc_ex_flow": Decimal("40"),
                "day_pnl_usdc_ex_flow_ex_spot": Decimal("5"),
                "equity_by_book": {"ETH": Decimal("1000")},
                "day_start_equity_by_book": {"ETH": Decimal("950")},
                "day_net_flow_usdc_by_book": {"ETH": Decimal("10")},
                "day_pnl_usdc_ex_flow_by_book": {"ETH": Decimal("40")},
                "day_pnl_usdc_ex_flow_ex_spot_by_book": {"ETH": Decimal("5")},
            }
        },
        {
            "portfolio": {
                "total_equity_usdc": Decimal("500"),
                "day_start_equity_usdc": Decimal("500"),
                "day_net_flow_usdc": Decimal("0"),
                "day_pnl_usdc_ex_flow": Decimal("0"),
                "day_pnl_usdc_ex_flow_ex_spot": Decimal("12"),
                "equity_by_book": {"USDC": Decimal("500")},
                "day_start_equity_by_book": {"USDC": Decimal("500")},
                "day_net_flow_usdc_by_book": {"USDC": Decimal("0")},
                "day_pnl_usdc_ex_flow_by_book": {"USDC": Decimal("0")},
                "day_pnl_usdc_ex_flow_ex_spot_by_book": {"USDC": Decimal("12")},
            }
        },
    ]

    portfolio = frontend_server._aggregate_portfolios(accounts, statuses)

    assert portfolio["day_pnl_usdc_ex_flow"] == Decimal("40")
    assert portfolio["day_pnl_usdc_ex_flow_ex_spot"] == Decimal("17")
    assert portfolio["day_pnl_usdc_ex_flow_ex_spot_by_book"] == {
        "ETH": Decimal("5"),
        "USDC": Decimal("12"),
    }


def test_aggregate_portfolios_dedupes_shared_api_credentials(tmp_path):
    shared = make_config(tmp_path, client_id="shared-key", client_secret="s")
    other = make_config(tmp_path, client_id="other-key", client_secret="s", state_file=tmp_path / "other.json")
    accounts = [
        DashboardAccount("s1", tmp_path / "x.env", shared, shared.state_file, tmp_path / "ledger-x"),
        DashboardAccount("s2", tmp_path / "y.env", shared, shared.state_file, tmp_path / "ledger-y"),
        DashboardAccount("o", tmp_path / "z.env", other, other.state_file, tmp_path / "ledger-z"),
    ]
    statuses: list[dict] = [
        {"portfolio": {"total_equity_usdc": Decimal("900"), "day_start_equity_usdc": Decimal("900")}},
        {"portfolio": {"total_equity_usdc": Decimal("900"), "day_start_equity_usdc": Decimal("900")}},
        {"portfolio": {"total_equity_usdc": Decimal("100"), "day_start_equity_usdc": Decimal("100")}},
    ]
    equity_statuses = frontend_server._dedupe_statuses_for_equity_aggregate(accounts, statuses)
    assert len(equity_statuses) == 2
    portfolio = frontend_server._aggregate_portfolios(accounts, statuses, equity_statuses=equity_statuses)
    assert portfolio["total_equity_usdc"] == Decimal("1000")
