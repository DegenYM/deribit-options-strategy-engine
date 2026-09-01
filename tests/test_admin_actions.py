from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from deribit_engine.admin_server.actions import (
    LIVE_CONFIRM,
    list_investor_targets,
    run_close_position,
    run_panic_close,
    run_spot_restore,
)
from deribit_engine.admin_server.app import create_admin_app
from deribit_engine.env_layout import InvestorAccountSpec, InvestorManifest
from deribit_engine.exceptions import ConfigurationError
from deribit_engine.models import StrategyState, TradeGroup


def _account(tmp_path: Path, slug: str = "covered_call") -> InvestorAccountSpec:
    return InvestorAccountSpec(
        slug=slug,
        strategy="covered_call",
        env_path=tmp_path / f".env.{slug}",
        enabled=True,
        display_name=slug,
    )


def _manifest(tmp_path: Path, *accounts: InvestorAccountSpec) -> InvestorManifest:
    return InvestorManifest(
        investor_id="alice",
        display_name="Alice",
        root=tmp_path,
        accounts=accounts,
    )


def _restore_group() -> TradeGroup:
    return TradeGroup.from_dict(
        {
            "group_id": "0043",
            "currency": "BTC",
            "short_instrument_name": "BTC-28AUG26-78000-C",
            "short_label": "cc-btc-0043",
            "status": "closed",
            "strategy": "covered_call",
            "option_type": "call",
            "collateral_currency": "BTC",
            "quantity": "0.1",
            "covered_underlying_quantity": "0.1",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "closed_timestamp_ms": 1_746_000_000_000,
            "short_strike": "78000",
            "entry_credit": "30",
            "original_entry_credit": "30",
            "max_loss": "1000",
            "regime_at_entry": "normal",
            "spot_exit_status": "filled",
            "spot_exit_amount": "0.1",
            "spot_exit_quote_proceeds": "7800",
            "spot_exit_quote_proceeds_lifetime": "7800",
            "spot_restore_status": "submitted",
            "spot_restore_order_id": "parked-1",
        }
    )


def _open_group() -> TradeGroup:
    return TradeGroup.from_dict(
        {
            "group_id": "0010",
            "currency": "BTC",
            "short_instrument_name": "BTC-26SEP26-90000-C",
            "short_label": "cc-btc-0010",
            "status": "open",
            "strategy": "covered_call",
            "option_type": "call",
            "collateral_currency": "BTC",
            "quantity": "0.1",
            "covered_underlying_quantity": "0.1",
            "entry_timestamp_ms": 1,
            "expiration_timestamp_ms": 2,
            "short_strike": "90000",
            "entry_credit": "20",
            "original_entry_credit": "20",
            "max_loss": "1000",
            "regime_at_entry": "normal",
        }
    )


def test_live_actions_require_confirm(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="confirm"):
        run_close_position(
            "alice",
            repo_root=tmp_path,
            account="covered_call",
            group_id="0010",
            live=True,
            confirm="yes",
        )
    with pytest.raises(ConfigurationError, match="confirm"):
        run_panic_close("alice", repo_root=tmp_path, account=None, live=True, confirm=None)
    with pytest.raises(ConfigurationError, match="confirm"):
        run_spot_restore(
            "alice",
            repo_root=tmp_path,
            account="covered_call",
            group_id="0043",
            live=True,
            confirm="",
        )


def test_list_investor_targets_from_state(tmp_path: Path, monkeypatch) -> None:
    account = _account(tmp_path)
    state = StrategyState(groups=[_open_group(), _restore_group()])
    monkeypatch.setattr(
        "deribit_engine.admin_server.actions.load_manifest",
        lambda *_a, **_k: _manifest(tmp_path, account),
    )
    monkeypatch.setattr("deribit_engine.admin_server.actions._load_state", lambda *_a, **_k: state)
    monkeypatch.setattr("deribit_engine.admin_server.actions.has_private_creds_for_env", lambda *_a, **_k: True)
    monkeypatch.setattr("deribit_engine.config.has_private_creds_for_env", lambda *_a, **_k: True)

    payload = list_investor_targets("alice", repo_root=tmp_path)
    assert [row["group_id"] for row in payload["open_groups"]] == ["0010"]
    assert [row["group_id"] for row in payload["restore_candidates"]] == ["0043"]
    assert payload["restore_candidates"][0]["account"] == "covered_call"


def test_spot_restore_cancels_parked_then_markets(tmp_path: Path, monkeypatch) -> None:
    account = _account(tmp_path)
    group = _restore_group()
    state = StrategyState(groups=[group])
    cancelled: list[str] = []
    restores: list[dict] = []

    class _Client:
        def cancel_order(self, order_id: str) -> dict:
            cancelled.append(order_id)
            return {"cancelled": order_id}

    class _Bot:
        client = _Client()
        state_store = SimpleNamespace(save=lambda _state: None)

        def _load_runtime(self, live=False):
            del live
            return SimpleNamespace(state=state)

        def _persist_trade_journal_actions(self, actions):
            del actions

    monkeypatch.setattr(
        "deribit_engine.admin_server.actions.load_manifest",
        lambda *_a, **_k: _manifest(tmp_path, account),
    )
    monkeypatch.setattr("deribit_engine.config.has_private_creds_for_env", lambda *_a, **_k: True)
    monkeypatch.setattr("deribit_engine.admin_server.actions._load_state", lambda *_a, **_k: state)
    monkeypatch.setattr("deribit_engine.admin_server.actions._build_bot", lambda *_a, **_k: _Bot())
    monkeypatch.setattr(
        "deribit_engine.admin_server.actions._spot_restore_order_is_open",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "deribit_engine.admin_server.actions.execute_spot_restore_for_group",
        lambda bot, target, **kwargs: restores.append({"group": target.group_id, **kwargs})
        or {
            "action": "spot_restore",
            "order_type": kwargs.get("order_type"),
        },
    )

    result = run_spot_restore(
        "alice",
        repo_root=tmp_path,
        account="covered_call",
        group_id="43",
        live=True,
        confirm=LIVE_CONFIRM,
    )
    assert cancelled == ["parked-1"]
    assert restores[0]["order_type"] == "market"
    assert restores[0]["restore_reason"] == "emergency_spot_restore"
    assert result["cancelled_resting"]["cancelled_order_id"] == "parked-1"
    assert group.spot_restore_status == "skipped"
    assert "operator_cancelled" in group.spot_restore_reason


def test_preview_does_not_build_bot(tmp_path: Path, monkeypatch) -> None:
    account = _account(tmp_path)
    state = StrategyState(groups=[_open_group(), _restore_group()])
    built = []
    monkeypatch.setattr(
        "deribit_engine.admin_server.actions.load_manifest",
        lambda *_a, **_k: _manifest(tmp_path, account),
    )
    monkeypatch.setattr("deribit_engine.config.has_private_creds_for_env", lambda *_a, **_k: True)
    monkeypatch.setattr("deribit_engine.admin_server.actions._load_state", lambda *_a, **_k: state)
    monkeypatch.setattr(
        "deribit_engine.admin_server.actions._build_bot",
        lambda *_a, **_k: built.append(True),
    )

    close = run_close_position(
        "alice",
        repo_root=tmp_path,
        account="covered_call",
        group_id="0010",
        live=False,
        confirm=None,
    )
    restore = run_spot_restore(
        "alice",
        repo_root=tmp_path,
        account="covered_call",
        group_id="0043",
        live=False,
        confirm=None,
    )
    panic = run_panic_close("alice", repo_root=tmp_path, account=None, live=False, confirm=None)
    assert built == []
    assert close["plan"]["order_type"] == "market"
    assert close["plan"]["est_pnl_usdc"] == "20"
    assert close["plan"]["est_close_fee_usdc"] == "0"
    assert restore["plan"]["will_cancel_resting_order_id"] == "parked-1"
    assert restore["plan"]["breakeven_price"] == "78000"
    assert panic["plan"]["accounts"][0]["open_groups"][0]["group_id"] == "0010"
    assert panic["plan"]["accounts"][0]["open_groups"][0]["est_pnl_usdc"] == "20"


def test_admin_trade_routes(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config" / "platform").mkdir(parents=True)
    (tmp_path / "config" / "investors").mkdir(parents=True)
    (tmp_path / "config" / "platform" / "registry.toml").write_text(
        "\n".join(
            [
                "[platform]",
                f'repo_root = "{tmp_path}"',
                "next_frontend_port = 8800",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "deribit_engine.admin_server.actions.list_investor_targets",
        lambda *_a, **_k: {"investor_id": "alice", "open_groups": [], "restore_candidates": [], "accounts": []},
    )
    monkeypatch.setattr(
        "deribit_engine.admin_server.actions.run_close_position",
        lambda *_a, **kwargs: {"ok": True, "live": kwargs["live"], "kind": "close_position"},
    )
    client = TestClient(create_admin_app(repo_root=tmp_path))
    listed = client.get("/api/admin/investors/alice/targets")
    assert listed.status_code == 200
    preview = client.post(
        "/api/admin/investors/alice/close-position",
        json={"account": "covered_call", "group_id": "0010"},
    )
    assert preview.status_code == 200
    assert preview.json()["live"] is False
