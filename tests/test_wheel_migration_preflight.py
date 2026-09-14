"""The wheel migration preflight reads live config and state, writes nothing, and says what changes."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from deribit_engine.models import StrategyState, TradeGroup
from deribit_engine.state import StrategyStateStore
from deribit_engine.utils import utc_now_ms

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "wheel_migration_preflight.py"
_spec = importlib.util.spec_from_file_location("wheel_migration_preflight", _SCRIPT)
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)


def _called_away(group_id: str, **overrides) -> TradeGroup:
    payload = {
        "group_id": group_id,
        "currency": "BTC",
        "short_instrument_name": "BTC-28AUG26-100000-C",
        "status": "closed",
        "strategy": "covered_call",
        "option_type": "call",
        "collateral_currency": "BTC",
        "quantity": "1",
        "covered_underlying_quantity": "1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 1_787_904_000_000,
        "closed_timestamp_ms": 1_787_904_000_000,
        "short_strike": "100000",
        "entry_credit": "0.01",
        "original_entry_credit": "0.01",
        "max_loss": "1000",
        "regime_at_entry": "normal",
        "spot_exit_status": "filled",
        "spot_exit_amount": "1",
        "spot_exit_instrument_name": "BTC_USDC",
        "spot_exit_quote_proceeds": "100000",
        "spot_exit_quote_proceeds_lifetime": "100000",
        "spot_exit_reason": "covered_call_settlement_exit",
    }
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def _put(group_id: str, parent_id: str, *, credit: str, status: str = "closed", **overrides) -> TradeGroup:
    payload = {
        "group_id": group_id,
        "currency": "BTC",
        "short_instrument_name": "BTC_USDC-04SEP26-100000-P",
        "status": status,
        "strategy": "cash_secured",
        "option_type": "put",
        "collateral_currency": "USDC",
        "quantity": "1",
        "entry_timestamp_ms": 1,
        "expiration_timestamp_ms": 1_788_508_800_000,
        "short_strike": "100000",
        "entry_credit": credit,
        "original_entry_credit": credit,
        "max_loss": "100000",
        "regime_at_entry": "normal",
        "cash_secured_from_group_id": parent_id,
    }
    if status == "closed":
        payload["close_index_usd"] = "104000"  # expired out of the money
    payload.update(overrides)
    return TradeGroup.from_dict(payload)


def _book() -> list[TradeGroup]:
    return [
        # Never wheeled: the wheel will try its first put on the first cycle.
        _called_away("0101"),
        # Three rounds in, waiting for the next put; one round's premium was swapped into coin.
        _called_away("0102", cash_secured_status="entered", cash_secured_group_id="0203"),
        _put("0201", "0102", credit="900"),
        _put("0202", "0102", credit="1100", realized_close_fee="50"),
        _put("0203", "0102", credit="500", csp_premium_swap_amount="200", csp_premium_swap_status="filled"),
        # Auto-restore left a buy order resting.
        _called_away(
            "0103", spot_restore_status="submitted", spot_restore_order_id="restore-1", spot_restore_amount="1"
        ),
        # A put is open right now.
        _called_away("0104", cash_secured_status="entered", cash_secured_group_id="0204"),
        _put("0204", "0104", credit="700", status="open", expiration_timestamp_ms=utc_now_ms() + 3 * 86_400_000),
    ]


@contextmanager
def _investor(*, account_extra: str = "") -> Iterator[tuple[str, Path]]:
    # The investor layout is what puts the sub-account env after the shared profile; it has to
    # live under config/investors/ (git-ignored) for env_layer_paths to recognise it.
    with tempfile.TemporaryDirectory(dir=REPO / "config" / "investors", prefix="zzpreflight") as tmp:
        root = Path(tmp)
        (root / "accounts").mkdir()
        (root / "accounts.toml").write_text(
            f'[investor]\nid = "{root.name}"\n\n[[accounts]]\nslug = "covered_call"\nstrategy = "covered_call"\n'
        )
        # Set before the shared profile, so it loses: exactly what the preflight must point out.
        (root / ".env.investor").write_text("COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED=false\n")
        state_path = root / "state.json"
        (root / "accounts" / ".env.covered_call").write_text(
            f"DERIBIT_ENV=mainnet\nSTATE_FILE={state_path}\n{account_extra}\n"
        )
        state = StrategyState()
        state.groups.extend(_book())
        StrategyStateStore(state_path).save(state)
        yield root.name, state_path


def test_each_wheel_stage_is_reported_and_nothing_is_written(capsys):
    with _investor() as (investor_id, state_path):
        before = state_path.read_bytes()
        code = preflight.main(["--investor", investor_id, "--json", "--no-network"])
        assert state_path.read_bytes() == before
    (account,) = json.loads(capsys.readouterr().out)

    assert code == 0
    assert account["effective"]["covered_call_itm_to_cash_secured_enabled"] == "True"
    assert account["effective"]["covered_call_csp_premium_ladder"] == "True"
    assert account["effective"]["covered_call_csp_active_roll_enabled"] == "False"
    assert account["warnings"] == []
    lost = {(item["key"], item["value"]) for item in account["overridden"]}
    assert ("COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED", "false") in lost

    parents = {row["group_id"]: row for row in account["parents"]}
    assert parents["0101"]["stage"] == "first_put"
    wheeling = parents["0102"]
    assert wheeling["stage"] == "next_put"
    assert wheeling["rounds_closed"] == 3
    # 900 + (1100 − 50) + (500 − 200 already swapped into coin)
    assert wheeling["ledger_usdc"] == "2250.00"
    assert wheeling["swapped_usdc"] == "200.00"
    assert wheeling["window"] == ["95000", "100000"]
    assert wheeling["window_with_ladder"][1] == "102250"
    assert wheeling["ceiling_lift_pct"] == "+2.25"
    assert "premium_swapped_into_coin" in wheeling["warnings"]
    assert parents["0103"]["stage"] == "not_eligible"
    assert parents["0103"]["reason"] == "restore_in_flight"
    assert "auto_restore_order_resting" in parents["0103"]["warnings"]
    assert parents["0104"]["stage"] == "put_open"
    assert [put["group_id"] for put in account["open_puts"]] == ["0204"]


def test_a_sub_account_still_swapping_premium_is_reported_as_failing_to_load(capsys):
    with _investor(account_extra="COVERED_CALL_CSP_PREMIUM_TARGET=spot") as (investor_id, _state_path):
        code = preflight.main(["--investor", investor_id, "--json", "--no-network"])
    (account,) = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "CSP_PREMIUM_LADDER" in account["config_error"]


def test_the_text_report_names_the_stage_and_the_override_that_loses(capsys):
    with _investor() as (investor_id, _state_path):
        preflight.main(["--investor", investor_id, "--no-network"])
    out = capsys.readouterr().out
    assert "從未接回" in out
    assert "被 config/shared/strategies/.env.covered_call 的 true 蓋掉" in out
    assert "自動買回單還掛著" in out


def test_an_active_roll_that_could_move_an_open_put_onto_the_ladder_is_flagged(capsys):
    """Open puts wait for settlement before the ladder applies — unless the active roll replaces them early."""
    with _investor(account_extra="COVERED_CALL_CSP_ACTIVE_ROLL_ENABLED=true") as (investor_id, _state_path):
        preflight.main(["--investor", investor_id, "--json", "--no-network"])
    (account,) = json.loads(capsys.readouterr().out)
    assert account["warnings"] == ["active_roll_reaches_open_puts"]
