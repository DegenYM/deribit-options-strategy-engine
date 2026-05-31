from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from deribit_engine.client import DeribitClient
from deribit_engine.exceptions import ConfigurationError
from deribit_engine.investor_ops import investor_init
from deribit_engine.models import OptionInstrument, OrderBookSnapshot
from deribit_engine.wallet_ops import (
    internal_transfer,
    place_protected_spot_order,
    resolve_fee_subaccount_id,
    resolve_protected_spot_order,
    spot_instrument_name,
    trade_spot,
)


def _bootstrap_repo(tmp_path: Path) -> Path:
    (tmp_path / "deribit_engine").mkdir()
    example = Path(__file__).resolve().parents[1] / "config" / "investors" / "_example"
    (tmp_path / "config" / "investors" / "_example").mkdir(parents=True)
    for rel in (
        "accounts.toml",
        ".env.investor.example",
        "accounts/.env.naked.example",
        "accounts/.env.covered_call.example",
    ):
        src = example / rel
        dest = tmp_path / "config" / "investors" / "_example" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    (tmp_path / "config" / "platform").mkdir(parents=True)
    (tmp_path / "config" / "launchd").mkdir(parents=True)
    for name in ("com.deribit.live.plist.template", "com.deribit.frontend.plist.template"):
        src = Path(__file__).resolve().parents[1] / "config" / "launchd" / name
        (tmp_path / "config" / "launchd" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config" / "systemd").mkdir(parents=True)
    for name in ("com.deribit.live.service.template", "com.deribit.frontend.service.template"):
        src = Path(__file__).resolve().parents[1] / "config" / "systemd" / name
        (tmp_path / "config" / "systemd" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config" / "platform" / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{tmp_path}"',
                'python_bin = "python3"',
                'domain = "portfolio.test"',
                "next_frontend_port = 8800",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_spot_instrument_name() -> None:
    assert spot_instrument_name("btc", "usdc") == "BTC_USDC"
    assert spot_instrument_name("ETH", "USDT") == "ETH_USDT"


def test_resolve_spot_trade_side() -> None:
    from deribit_engine.wallet_ops import resolve_spot_trade_side

    assert resolve_spot_trade_side("BTC", "USDC") == ("sell", "BTC", "USDC")
    assert resolve_spot_trade_side("USDC", "BTC") == ("buy", "BTC", "USDC")
    with pytest.raises(ConfigurationError):
        resolve_spot_trade_side("USDC", "USDT")


def test_resolve_fee_subaccount_id_from_config() -> None:
    class FakeClient:
        def get_subaccounts(self, *, with_portfolio=False):
            raise AssertionError("should not call API when id configured")

    from deribit_engine.wallet_ops import FeeSubaccountConfig

    fee_id, label = resolve_fee_subaccount_id(
        FakeClient(),
        fee_config=FeeSubaccountConfig(subaccount_id=99, subaccount_name="fee_acc"),
    )
    assert fee_id == 99
    assert label == "id:99"


def test_resolve_fee_subaccount_id_by_name() -> None:
    class FakeClient:
        def get_subaccounts(self, *, with_portfolio=False):
            return [
                {"id": 1, "type": "main", "username": "main_user"},
                {"id": 42, "type": "subaccount", "username": "fee_acc", "system_name": "fee_acc"},
            ]

    from deribit_engine.wallet_ops import FeeSubaccountConfig

    fee_id, label = resolve_fee_subaccount_id(
        FakeClient(),
        fee_config=FeeSubaccountConfig(subaccount_id=None, subaccount_name="fee_acc"),
    )
    assert fee_id == 42
    assert label == "fee_acc"


def test_resolve_fee_subaccount_id_via_fee_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine.config import load_config
    from deribit_engine.wallet_ops import FeeSubaccountConfig, _resolve_fee_subaccount_id_via_fee_env

    repo = _bootstrap_repo(tmp_path)
    investor_init("eve", strategies=("naked",), repo_root=repo)
    investor_dir = repo / "config/investors/eve"
    fee_env = investor_dir / "accounts/.env.fee"
    fee_env.write_text(
        "\n".join(
            [
                "ACCOUNT_ROLE=fee",
                "DERIBIT_ENV=testnet",
                "DERIBIT_CLIENT_ID=fee_cid",
                "DERIBIT_CLIENT_SECRET=fee_sec",
                "ORDER_LABEL_PREFIX=eve_fee",
            ]
        ),
        encoding="utf-8",
    )
    strategy_env = investor_dir / "accounts/.env.naked"
    strategy_env.write_text(
        strategy_env.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=cid\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    strategy_client = DeribitClient(load_config(strategy_env, require_private=True))

    def _fake_get_subaccounts(self, *, with_portfolio=False):
        if self.config.client_id == "fee_cid":
            return [
                {"id": 1, "type": "main", "username": "main_user"},
                {"id": 99, "type": "subaccount", "username": "fee_acc", "system_name": "fee_acc"},
            ]
        return [{"id": 5, "type": "subaccount", "username": "naked_short"}]

    monkeypatch.setattr(DeribitClient, "get_subaccounts", _fake_get_subaccounts)

    fee_id, label = resolve_fee_subaccount_id(
        strategy_client,
        fee_config=FeeSubaccountConfig(subaccount_id=None, subaccount_name="fee_acc"),
        investor_dir=investor_dir,
    )
    assert fee_id == 99
    assert label == "fee_acc"

    via_fee = _resolve_fee_subaccount_id_via_fee_env(
        investor_dir,
        fee_config=FeeSubaccountConfig(subaccount_id=None, subaccount_name="fee_acc"),
    )
    assert via_fee == (99, "fee_acc")


def test_trade_spot_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine.config import load_config

    repo = _bootstrap_repo(tmp_path)
    investor_init("alice", strategies=("covered_call",), repo_root=repo)
    env = repo / "config/investors/alice/accounts/.env.covered_call"
    env.write_text(
        env.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=cid\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    config = load_config(env, require_private=True)
    client = DeribitClient(config)

    spot_row = {
        "instrument_name": "BTC_USDC",
        "base_currency": "BTC",
        "quote_currency": "USDC",
        "settlement_currency": "USDC",
        "instrument_type": "linear",
        "contract_size": 0.0001,
        "min_trade_amount": 0.0001,
        "tick_size": 0.5,
        "tick_size_steps": [],
    }

    monkeypatch.setattr(
        client, "get_instruments", lambda currency, kind="option", expired=False: [spot_row] if kind == "spot" else []
    )
    monkeypatch.setattr(
        client,
        "get_account_summaries",
        lambda *, extended=False: [
            {
                "currency": "BTC",
                "balance": 1,
                "equity": 1,
                "available_funds": 0.5,
                "available_withdrawal_funds": 0.5,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 50000,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            }
        ],
    )
    monkeypatch.setattr(
        client,
        "get_order_book",
        lambda instrument_name, depth=1: {
            "instrument_name": instrument_name,
            "best_bid_price": 95000,
            "best_bid_amount": 2,
            "best_ask_price": 95010,
            "best_ask_amount": 1.5,
            "mark_price": 95005,
            "index_price": 95000,
        },
    )

    out = trade_spot(
        config,
        client,
        from_currency="BTC",
        amount="0.05",
        to_currency="USDC",
        live=False,
    )
    assert out["action"] == "trade_spot_preview"
    assert out["instrument_name"] == "BTC_USDC"
    assert out["amount"] == "0.05"
    assert out["live"] is False
    assert out["trade_price"] == "95000"
    assert out["trade_price_source"] == "best_bid"
    assert out["estimated_quote_proceeds"] == "4750"
    assert out["best_bid_price"] == "95000"
    assert out["best_ask_price"] == "95010"


def test_trade_spot_limit_preview_uses_best_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine.config import load_config

    repo = _bootstrap_repo(tmp_path)
    investor_init("limit", strategies=("covered_call",), repo_root=repo)
    env = repo / "config/investors/limit/accounts/.env.covered_call"
    env.write_text(
        env.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=cid\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    config = load_config(env, require_private=True)
    client = DeribitClient(config)
    spot_row = {
        "instrument_name": "BTC_USDC",
        "base_currency": "BTC",
        "quote_currency": "USDC",
        "settlement_currency": "USDC",
        "instrument_type": "linear",
        "contract_size": 0.0001,
        "min_trade_amount": 0.0001,
        "tick_size": 0.5,
        "tick_size_steps": [],
    }
    monkeypatch.setattr(
        client, "get_instruments", lambda currency, kind="option", expired=False: [spot_row] if kind == "spot" else []
    )
    monkeypatch.setattr(
        client,
        "get_account_summaries",
        lambda *, extended=False: [
            {
                "currency": "BTC",
                "balance": 1,
                "equity": 1,
                "available_funds": 0.5,
                "available_withdrawal_funds": 0.5,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 50000,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            }
        ],
    )
    monkeypatch.setattr(
        client,
        "get_order_book",
        lambda instrument_name, depth=1: {
            "instrument_name": instrument_name,
            "best_bid_price": 74925,
            "best_bid_amount": 2,
            "best_ask_price": 74952,
            "best_ask_amount": 1.5,
            "mark_price": 74918.85,
            "index_price": 74900,
        },
    )

    out = trade_spot(
        config,
        client,
        from_currency="BTC",
        amount="0.01",
        to_currency="USDC",
        order_type="limit",
        live=False,
    )
    assert out["trade_price"] == "74952"
    assert out["trade_price_source"] == "best_ask"
    assert out["limit_price"] == "74952"
    assert out["estimated_quote_proceeds"] == "749.52"


def test_trade_spot_buy_limit_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine.config import load_config

    repo = _bootstrap_repo(tmp_path)
    investor_init("buyer", strategies=("bull_put",), repo_root=repo)
    env = repo / "config/investors/buyer/accounts/.env.bull_put"
    env.write_text(
        env.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=cid\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    config = load_config(env, require_private=True)
    client = DeribitClient(config)
    spot_row = {
        "instrument_name": "BTC_USDC",
        "base_currency": "BTC",
        "quote_currency": "USDC",
        "settlement_currency": "USDC",
        "instrument_type": "linear",
        "contract_size": 0.0001,
        "min_trade_amount": 0.0001,
        "tick_size": 0.5,
        "tick_size_steps": [],
    }
    monkeypatch.setattr(
        client, "get_instruments", lambda currency, kind="option", expired=False: [spot_row] if kind == "spot" else []
    )
    monkeypatch.setattr(
        client,
        "get_account_summaries",
        lambda *, extended=False: [
            {
                "currency": "USDC",
                "balance": 1000,
                "equity": 1000,
                "available_funds": 1000,
                "available_withdrawal_funds": 1000,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 1000,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            }
        ],
    )
    monkeypatch.setattr(
        client,
        "get_order_book",
        lambda instrument_name, depth=1: {
            "instrument_name": instrument_name,
            "best_bid_price": 74925,
            "best_bid_amount": 2,
            "best_ask_price": 74952,
            "best_ask_amount": 1.5,
            "mark_price": 74918.85,
            "index_price": 74900,
        },
    )

    out = trade_spot(
        config,
        client,
        from_currency="USDC",
        amount="50",
        to_currency="BTC",
        order_type="limit",
        live=False,
    )
    assert out["direction"] == "buy"
    assert out["from_amount"] == "50"
    assert out["amount"] == "0.0006"
    assert out["trade_price"] == "74925"
    assert out["trade_price_source"] == "best_bid"
    assert out["limit_price"] == "74925"
    assert out["estimated_to_amount"] == "0.0006"
    assert out["estimated_spend"] == "44.955"


def test_internal_transfer_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine.config import load_config

    repo = _bootstrap_repo(tmp_path)
    investor_init("bob", strategies=("naked",), repo_root=repo)
    investor_dir = repo / "config/investors/bob"
    (investor_dir / ".env.investor").write_text("FEE_SUBACCOUNT_ID=777\n", encoding="utf-8")
    env = investor_dir / "accounts/.env.naked"
    env.write_text(
        env.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=cid\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    config = load_config(env, require_private=True)
    client = DeribitClient(config)
    monkeypatch.setattr(
        client,
        "get_account_summaries",
        lambda *, extended=False: [
            {
                "currency": "USDC",
                "balance": 2000,
                "equity": 2000,
                "available_funds": 2000,
                "available_withdrawal_funds": 2000,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 2000,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            }
        ],
    )
    monkeypatch.setattr(
        client,
        "get_subaccounts",
        lambda *, with_portfolio=False: [{"id": 5, "type": "subaccount", "username": "naked_short"}],
    )

    out = internal_transfer(
        config,
        client,
        investor_dir=investor_dir,
        strategy_env=env,
        currency="USDC",
        amount="500",
        live=False,
    )
    assert out["action"] == "internal_transfer_preview"
    assert out["destination_subaccount_id"] == 777
    assert out["source_subaccount_id"] == 5
    assert out["transfer_via"] == "strategy_subaccount_api"
    assert "transfer_note" in out
    assert out["amount"] == "500"


def test_internal_transfer_live_calls_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine.config import load_config

    repo = _bootstrap_repo(tmp_path)
    investor_init("carol", strategies=("naked",), repo_root=repo)
    investor_dir = repo / "config/investors/carol"
    (investor_dir / ".env.investor").write_text("FEE_SUBACCOUNT_ID=888\n", encoding="utf-8")
    env = investor_dir / "accounts/.env.naked"
    env.write_text(
        env.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=cid\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    config = load_config(env, require_private=True)
    client = DeribitClient(config)
    monkeypatch.setattr(
        client,
        "get_account_summaries",
        lambda *, extended=False: [
            {
                "currency": "USDC",
                "balance": 1000,
                "equity": 1000,
                "available_funds": 1000,
                "available_withdrawal_funds": 1000,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 1000,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            }
        ],
    )
    monkeypatch.setattr(
        client,
        "get_subaccounts",
        lambda *, with_portfolio=False: [{"id": 5, "type": "subaccount", "username": "naked_short"}],
    )
    captured: dict[str, object] = {}

    def _fake_transfer(**kwargs):
        captured.update(kwargs)
        return {"id": 1, "state": "confirmed", "other_side": "fee_acc"}

    monkeypatch.setattr(client, "submit_transfer_between_subaccounts", _fake_transfer)

    out = internal_transfer(
        config,
        client,
        investor_dir=investor_dir,
        strategy_env=env,
        currency="USDC",
        amount="250",
        live=True,
        nonce="test-nonce-1",
    )
    assert out["action"] == "internal_transfer"
    assert out["transfer_state"] == "confirmed"
    assert captured["destination"] == 888
    assert captured["amount"] == Decimal("250")
    assert captured["nonce"] == "test-nonce-1"
    assert "source" not in captured


def test_internal_transfer_live_uses_main_account_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine.config import load_config

    repo = _bootstrap_repo(tmp_path)
    investor_init("mainxfer", strategies=("naked",), repo_root=repo)
    investor_dir = repo / "config/investors/mainxfer"
    (investor_dir / ".env.investor").write_text("FEE_SUBACCOUNT_ID=888\n", encoding="utf-8")
    env = investor_dir / "accounts/.env.naked"
    env.write_text(
        env.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=cid\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    main_env = investor_dir / "accounts/.env.main"
    main_env.write_text(
        "\n".join(
            [
                "ACCOUNT_ROLE=main",
                "DERIBIT_ENV=testnet",
                "DERIBIT_CLIENT_ID=main_cid",
                "DERIBIT_CLIENT_SECRET=main_sec",
                "ORDER_LABEL_PREFIX=mainxfer_main",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(env, require_private=True)
    client = DeribitClient(config)
    monkeypatch.setattr(
        client,
        "get_account_summaries",
        lambda *, extended=False: [
            {
                "currency": "USDC",
                "balance": 1000,
                "equity": 1000,
                "available_funds": 1000,
                "available_withdrawal_funds": 1000,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 1000,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            }
        ],
    )

    def _fake_get_subaccounts(self, *, with_portfolio=False):
        if self.config.client_id == "main_cid":
            return [
                {"id": 1, "type": "main", "username": "main_user"},
                {"id": 5, "type": "subaccount", "username": "naked_short"},
                {"id": 888, "type": "subaccount", "username": "fee_acc"},
            ]
        return [{"id": 5, "type": "subaccount", "username": "naked_short"}]

    captured: dict[str, object] = {}

    def _fake_transfer(self, **kwargs):
        captured.update(kwargs)
        return {"id": 1, "state": "confirmed", "other_side": "fee_acc"}

    monkeypatch.setattr(DeribitClient, "get_subaccounts", _fake_get_subaccounts)
    monkeypatch.setattr(DeribitClient, "submit_transfer_between_subaccounts", _fake_transfer)

    out = internal_transfer(
        config,
        client,
        investor_dir=investor_dir,
        strategy_env=env,
        currency="USDC",
        amount="250",
        live=True,
    )
    assert out["transfer_via"] == "main_account_api"
    assert captured["source"] == 5
    assert captured["destination"] == 888


def test_trade_spot_rejects_insufficient_balance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deribit_engine.config import load_config

    repo = _bootstrap_repo(tmp_path)
    investor_init("dave", strategies=("covered_call",), repo_root=repo)
    env = repo / "config/investors/dave/accounts/.env.covered_call"
    env.write_text(
        env.read_text(encoding="utf-8") + "\nDERIBIT_CLIENT_ID=cid\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    config = load_config(env, require_private=True)
    client = DeribitClient(config)
    spot_row = {
        "instrument_name": "BTC_USDC",
        "base_currency": "BTC",
        "quote_currency": "USDC",
        "settlement_currency": "USDC",
        "instrument_type": "linear",
        "contract_size": 0.0001,
        "min_trade_amount": 0.0001,
        "tick_size": 0.5,
        "tick_size_steps": [],
    }
    monkeypatch.setattr(
        client, "get_instruments", lambda currency, kind="option", expired=False: [spot_row] if kind == "spot" else []
    )
    monkeypatch.setattr(
        client,
        "get_account_summaries",
        lambda *, extended=False: [
            {
                "currency": "BTC",
                "balance": 0.01,
                "equity": 0.01,
                "available_funds": 0.01,
                "available_withdrawal_funds": 0.01,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 500,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            }
        ],
    )

    with pytest.raises(ConfigurationError, match="exceeds available"):
        trade_spot(config, client, from_currency="BTC", amount="1", live=False)


def _spot_instrument() -> OptionInstrument:
    return OptionInstrument.from_api(
        {
            "instrument_name": "BTC_USDT",
            "base_currency": "BTC",
            "quote_currency": "USDT",
            "settlement_currency": "USDT",
            "instrument_type": "spot",
            "tick_size": "0.5",
            "min_trade_amount": "0.0001",
            "contract_size": "0.0001",
        }
    )


def test_resolve_protected_spot_order_skips_when_bid_below_mark_floor() -> None:
    book = OrderBookSnapshot.from_api(
        {
            "instrument_name": "BTC_USDT",
            "best_bid_price": "69000",
            "best_ask_price": "70100",
            "mark_price": "70000",
            "index_price": "70000",
        }
    )
    effective, limit_px, mark, skip = resolve_protected_spot_order(
        direction="sell",
        book=book,
        instrument=_spot_instrument(),
        order_type="market",
        max_slippage_pct=Decimal("0.005"),
    )
    assert skip == "slippage_exceeded"
    assert mark == Decimal("70000")
    assert effective == "market"
    assert limit_px is None


def test_resolve_protected_spot_order_uses_limit_floor_when_bid_ok() -> None:
    book = OrderBookSnapshot.from_api(
        {
            "instrument_name": "BTC_USDT",
            "best_bid_price": "69900",
            "best_ask_price": "70100",
            "mark_price": "70000",
            "index_price": "70000",
        }
    )
    effective, limit_px, mark, skip = resolve_protected_spot_order(
        direction="sell",
        book=book,
        instrument=_spot_instrument(),
        order_type="market",
        max_slippage_pct=Decimal("0.005"),
    )
    assert skip is None
    assert effective == "limit"
    assert mark == Decimal("70000")
    assert limit_px == Decimal("69650")


def test_place_protected_spot_order_live_uses_limit_ioc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from conftest import make_config

    config = make_config(tmp_path, covered_call_spot_max_slippage_pct=Decimal("0.005"))
    client = DeribitClient(config)
    captured: dict[str, object] = {}

    def fake_get_order_book(instrument_name, *, depth=1):
        return {
            "instrument_name": instrument_name,
            "best_bid_price": "69900",
            "best_ask_price": "70100",
            "mark_price": "70000",
            "index_price": "70000",
        }

    def fake_sell(**kwargs):
        captured.update(kwargs)
        return {"order": {"order_id": "spot-1", "order_state": "filled", "average_price": "69900"}}

    monkeypatch.setattr(client, "get_order_book", fake_get_order_book)
    monkeypatch.setattr(client, "place_sell_order", fake_sell)

    out = place_protected_spot_order(
        client,
        instrument=_spot_instrument(),
        instrument_name="BTC_USDT",
        direction="sell",
        amount=Decimal("0.001"),
        label="trial-profit-sweep",
        order_type="market",
        max_slippage_pct=Decimal("0.005"),
        live=True,
    )
    assert out.get("skipped") is not True
    assert captured["order_type"] == "limit"
    assert captured["time_in_force"] == "immediate_or_cancel"
    assert captured["price"] == Decimal("69650")
