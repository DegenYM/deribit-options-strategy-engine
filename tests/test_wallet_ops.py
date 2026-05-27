from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from deribit_engine.client import DeribitClient
from deribit_engine.exceptions import ConfigurationError
from deribit_engine.investor_ops import investor_init
from deribit_engine.wallet_ops import (
    internal_transfer,
    resolve_fee_subaccount_id,
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
        "accounts/.env.fee.example",
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

    out = internal_transfer(
        config,
        client,
        investor_dir=investor_dir,
        currency="USDC",
        amount="500",
        live=False,
    )
    assert out["action"] == "internal_transfer_preview"
    assert out["destination_subaccount_id"] == 777
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
    captured: dict[str, object] = {}

    def _fake_transfer(**kwargs):
        captured.update(kwargs)
        return {"id": 1, "state": "confirmed", "other_side": "fee_acc"}

    monkeypatch.setattr(client, "submit_transfer_between_subaccounts", _fake_transfer)

    out = internal_transfer(
        config,
        client,
        investor_dir=investor_dir,
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
