from __future__ import annotations

from pathlib import Path

import pytest

from deribit_engine.client import DeribitClient
from deribit_engine.exceptions import ConfigurationError, ExchangeError
from deribit_engine.fee_account import fetch_fee_account_balance
from deribit_engine.investor_ops import investor_init


def _bootstrap_repo(tmp_path: Path) -> Path:
    (tmp_path / "deribit_engine").mkdir()
    example = Path(__file__).resolve().parents[1] / "config" / "investors" / "_example"
    (tmp_path / "config" / "investors" / "_example").mkdir(parents=True)
    for rel in (
        "accounts.toml",
        ".env.investor.example",
        "accounts/.env.naked.example",
        "accounts/.env.bull_put.example",
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


def test_fetch_fee_account_balance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _bootstrap_repo(tmp_path)
    investor_init("alice", strategies=("naked",), repo_root=repo)
    fee_env = repo / "config/investors/alice/accounts/.env.fee"
    fee_env.write_text(
        "\n".join(
            [
                "ACCOUNT_ROLE=fee",
                "DERIBIT_ENV=testnet",
                "DERIBIT_CLIENT_ID=cid",
                "DERIBIT_CLIENT_SECRET=sec",
                "ORDER_LABEL_PREFIX=alice_fee",
            ]
        ),
        encoding="utf-8",
    )

    def _fake_summaries(self, *, extended=False):
        return [
            {
                "currency": "USDC",
                "balance": 1500.5,
                "equity": 1500.5,
                "available_funds": 1500.5,
                "available_withdrawal_funds": 1500.5,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 1500.5,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            },
            {
                "currency": "BTC",
                "balance": 0,
                "equity": 0,
                "available_funds": 0,
                "available_withdrawal_funds": 0,
                "initial_margin": 0,
                "maintenance_margin": 0,
                "delta_total": 0,
                "options_delta": 0,
                "options_gamma": 0,
                "options_theta": 0,
                "total_equity_usd": 0,
                "total_initial_margin_usd": 0,
                "total_maintenance_margin_usd": 0,
            },
        ]

    monkeypatch.setattr(DeribitClient, "get_account_summaries", _fake_summaries)

    result = fetch_fee_account_balance("alice", repo_root=repo)
    assert result["investor_id"] == "alice"
    assert result["env"] == "testnet"
    assert result["total_equity_usdc"] == "1500.5"
    assert result["books"]["USDC"]["balance"] == "1500.5"
    assert result["books"]["USDC"]["available_withdrawal_funds"] == "1500.5"
    assert result["books"]["BTC"]["equity"] == "0"


def test_fetch_fee_account_balance_missing_account_read_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _bootstrap_repo(tmp_path)
    investor_init("dave", strategies=("naked",), repo_root=repo)
    fee_env = repo / "config/investors/dave/accounts/.env.fee"
    fee_env.write_text(
        "\n".join(
            [
                "ACCOUNT_ROLE=fee",
                "DERIBIT_ENV=testnet",
                "DERIBIT_CLIENT_ID=cid",
                "DERIBIT_CLIENT_SECRET=sec",
                "ORDER_LABEL_PREFIX=dave_fee",
            ]
        ),
        encoding="utf-8",
    )

    def _forbidden(self, *, extended=False):
        raise ExchangeError(
            "private/get_account_summaries failed: code=13021 message=forbidden "
            "data={'reason': 'required scope account:read'}"
        )

    monkeypatch.setattr(DeribitClient, "get_account_summaries", _forbidden)

    with pytest.raises(ConfigurationError, match="Account=read scope"):
        fetch_fee_account_balance("dave", repo_root=repo)


def test_fetch_fee_account_balance_missing_creds(tmp_path: Path) -> None:
    repo = _bootstrap_repo(tmp_path)
    investor_init("bob", strategies=("naked",), repo_root=repo)
    with pytest.raises(ConfigurationError, match="missing DERIBIT_CLIENT_ID/SECRET"):
        fetch_fee_account_balance("bob", repo_root=repo)


def test_fetch_fee_account_balance_missing_env(tmp_path: Path) -> None:
    repo = _bootstrap_repo(tmp_path)
    investor_init("carol", strategies=("naked",), repo_root=repo)
    (repo / "config/investors/carol/accounts/.env.fee").unlink()
    with pytest.raises(ConfigurationError, match="Fee account env not found"):
        fetch_fee_account_balance("carol", repo_root=repo)
