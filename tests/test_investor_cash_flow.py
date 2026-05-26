from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from deribit_demo.client import DeribitClient
from deribit_demo.investor_cash_flow import (
    default_fee_flow_start_ms,
    effective_fee_flow_start_ms,
    fetch_cumulative_net_flow_usdc,
    parse_fee_flow_start_ms,
)


def test_parse_fee_flow_start_ms() -> None:
    assert parse_fee_flow_start_ms({}) == default_fee_flow_start_ms()
    assert effective_fee_flow_start_ms(0) == default_fee_flow_start_ms()
    assert effective_fee_flow_start_ms(1_700_000_000_000) == 1_700_000_000_000
    ms = parse_fee_flow_start_ms({"FEE_FLOW_START_DATE": "2025-06-01"})
    assert ms == int(datetime(2025, 6, 1, tzinfo=UTC).timestamp() * 1000)


def test_fetch_cumulative_net_flow_usdc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    investor_dir = tmp_path / "config" / "investors" / "demo"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        """
[investor]
id = "Demo"
display_name = "Demo"

[[accounts]]
slug = "naked"
strategy = "naked_short"
enabled = true
""".strip(),
        encoding="utf-8",
    )
    env_path = accounts_dir / ".env.naked"
    env_path.write_text(
        "DERIBIT_ENV=testnet\nDERIBIT_CLIENT_ID=id\nDERIBIT_CLIENT_SECRET=sec\nTRADED_COLLATERALS=USDC\n",
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")

    logs = [
        {"timestamp": 1000, "type": "deposit", "currency": "USDC", "change": "100000"},
        {"timestamp": 2000, "type": "trade", "currency": "USDC", "change": "500"},
        {"timestamp": 3000, "type": "withdrawal", "currency": "USDC", "change": "-10000"},
        {"timestamp": 4000, "type": "transfer", "currency": "USDC", "change": "-50000"},
    ]

    def _iter_transaction_log(self, **kwargs):
        if kwargs["currency"] != "USDC":
            return
        yield from logs

    monkeypatch.setattr(DeribitClient, "iter_transaction_log", _iter_transaction_log)

    flow = fetch_cumulative_net_flow_usdc(
        investor_dir,
        repo_root=tmp_path,
        index_by_ccy={"USDC": Decimal("1")},
    )
    assert flow.cumulative_net_flow_usdc == Decimal("40000")
    assert flow.entry_count == 2
    assert flow.net_flow_native_by_book["USDC"] == Decimal("40000")
    assert flow.transfer_native_by_book["USDC"] == Decimal("0")


def test_unpaired_transfer_on_single_api_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    investor_dir = tmp_path / "config" / "investors" / "demo"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        """
[investor]
id = "Demo"
display_name = "Demo"

[[accounts]]
slug = "naked"
strategy = "naked_short"
enabled = true
""".strip(),
        encoding="utf-8",
    )
    (accounts_dir / ".env.naked").write_text(
        "DERIBIT_ENV=testnet\nDERIBIT_CLIENT_ID=id\nDERIBIT_CLIENT_SECRET=sec\nTRADED_COLLATERALS=USDC\n",
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")

    logs = [
        {"timestamp": 1000, "type": "transfer", "currency": "USDC", "change": "4450"},
        {"timestamp": 2000, "type": "transfer", "currency": "USDC", "change": "-190"},
    ]

    def _iter_transaction_log(self, **kwargs):
        yield from logs

    monkeypatch.setattr(DeribitClient, "iter_transaction_log", _iter_transaction_log)

    flow = fetch_cumulative_net_flow_usdc(
        investor_dir,
        repo_root=tmp_path,
        index_by_ccy={"USDC": Decimal("1")},
    )
    assert flow.cumulative_net_flow_usdc == Decimal("4260")
    assert flow.net_flow_native_by_book["USDC"] == Decimal("4260")
    assert flow.transfer_native_by_book["USDC"] == Decimal("0")
    assert flow.entry_count == 0


def test_main_to_sub_transfer_counts_without_main_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only sub API configured: inbound transfer from main is external funding."""
    investor_dir = tmp_path / "config" / "investors" / "demo"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        """
[investor]
id = "Demo"
display_name = "Demo"

[[accounts]]
slug = "naked"
strategy = "naked_short"
enabled = true
""".strip(),
        encoding="utf-8",
    )
    (accounts_dir / ".env.naked").write_text(
        "DERIBIT_ENV=testnet\nDERIBIT_CLIENT_ID=id\nDERIBIT_CLIENT_SECRET=sec\n",
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")

    logs = [
        {"timestamp": 1000, "type": "transfer", "currency": "USDC", "change": "10000"},
    ]

    def _iter_transaction_log(self, **kwargs):
        if kwargs["currency"] != "USDC":
            return
        yield from logs

    monkeypatch.setattr(DeribitClient, "iter_transaction_log", _iter_transaction_log)

    flow = fetch_cumulative_net_flow_usdc(
        investor_dir,
        repo_root=tmp_path,
        index_by_ccy={"USDC": Decimal("1")},
    )
    assert flow.cumulative_net_flow_usdc == Decimal("10000")
    assert flow.net_flow_native_by_book["USDC"] == Decimal("10000")


def test_inter_sub_transfer_does_not_change_net_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deposit on one sub + transfer to another sub counts the deposit once only."""
    investor_dir = tmp_path / "config" / "investors" / "demo"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        """
[investor]
id = "Demo"
display_name = "Demo"

[[accounts]]
slug = "covered_call"
strategy = "covered_call"
enabled = true

[[accounts]]
slug = "naked"
strategy = "naked_short"
enabled = true
""".strip(),
        encoding="utf-8",
    )
    (accounts_dir / ".env.covered_call").write_text(
        "DERIBIT_ENV=testnet\nDERIBIT_CLIENT_ID=aaa\nDERIBIT_CLIENT_SECRET=sec_a\n",
        encoding="utf-8",
    )
    (accounts_dir / ".env.naked").write_text(
        "DERIBIT_ENV=testnet\nDERIBIT_CLIENT_ID=bbb\nDERIBIT_CLIENT_SECRET=sec_b\n",
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")

    logs_by_client = {
        "aaa": [
            {"timestamp": 1000, "type": "deposit", "currency": "USDC", "change": "6299.2"},
            {"timestamp": 2000, "type": "transfer", "currency": "USDC", "change": "-3150"},
        ],
        "bbb": [
            {"timestamp": 2000, "type": "transfer", "currency": "USDC", "change": "3150"},
        ],
    }

    def _iter_transaction_log(self, **kwargs):
        if kwargs["currency"] != "USDC":
            return
        cid = self.config.client_id
        yield from logs_by_client.get(cid, [])

    monkeypatch.setattr(DeribitClient, "iter_transaction_log", _iter_transaction_log)

    flow = fetch_cumulative_net_flow_usdc(
        investor_dir,
        repo_root=tmp_path,
        index_by_ccy={"USDC": Decimal("1")},
    )
    assert flow.cumulative_net_flow_usdc == Decimal("6299.2")
    assert flow.net_flow_native_by_book["USDC"] == Decimal("6299.2")
    assert flow.entry_count == 1


def test_fetch_sums_all_api_identities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    investor_dir = tmp_path / "config" / "investors" / "demo"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        """
[investor]
id = "Demo"
display_name = "Demo"

[[accounts]]
slug = "covered_call"
strategy = "covered_call"
enabled = true

[[accounts]]
slug = "naked"
strategy = "naked_short"
enabled = true
""".strip(),
        encoding="utf-8",
    )
    (accounts_dir / ".env.covered_call").write_text(
        "DERIBIT_ENV=testnet\nDERIBIT_CLIENT_ID=aaa\nDERIBIT_CLIENT_SECRET=sec_a\nTRADED_COLLATERALS=USDC\n",
        encoding="utf-8",
    )
    (accounts_dir / ".env.naked").write_text(
        "DERIBIT_ENV=testnet\nDERIBIT_CLIENT_ID=bbb\nDERIBIT_CLIENT_SECRET=sec_b\nTRADED_COLLATERALS=USDC\n",
        encoding="utf-8",
    )
    (tmp_path / "deribit_demo").mkdir()
    (tmp_path / ".env.example").write_text("", encoding="utf-8")

    logs_by_client = {
        "aaa": [{"timestamp": 1000, "type": "deposit", "currency": "USDC", "change": "10000"}],
        "bbb": [{"timestamp": 1000, "type": "deposit", "currency": "USDC", "change": "5000"}],
    }

    def _iter_transaction_log(self, **kwargs):
        cid = self.config.client_id
        yield from logs_by_client.get(cid, [])

    monkeypatch.setattr(DeribitClient, "iter_transaction_log", _iter_transaction_log)

    flow = fetch_cumulative_net_flow_usdc(
        investor_dir,
        repo_root=tmp_path,
        index_by_ccy={"USDC": Decimal("1")},
    )
    assert flow.cumulative_net_flow_usdc == Decimal("15000")
    assert len(flow.by_api_identity) == 2
