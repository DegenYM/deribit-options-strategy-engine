from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from deribit_engine.frontend_server.transfers_service import (
    aggregate_transfers_for_accounts,
    build_transfers_payload_from_store,
    sync_transfer_rows_for_currency,
)
from deribit_engine.frontend_server.types import DashboardAccount
from deribit_engine.models import TransactionEntry
from deribit_engine.transfer_store import TransferStore


@pytest.fixture(autouse=True)
def _fixed_transfer_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now = 1_700_100_000_000
    monkeypatch.setattr(
        "deribit_engine.frontend_server.transfers_service.utc_now_ms",
        lambda: fixed_now,
    )


class _FakeClient:
    def __init__(self, rows_by_currency: dict[str, list[dict]]):
        self.rows_by_currency = rows_by_currency
        self.calls: list[tuple[str, int, int]] = []

    def iter_transaction_log(self, *, currency: str, start_timestamp: int, end_timestamp: int, count: int = 100):
        self.calls.append((currency.upper(), int(start_timestamp), int(end_timestamp)))
        del count
        for row in self.rows_by_currency.get(currency.upper(), []):
            ts = int(row["timestamp"])
            if ts < start_timestamp or ts > end_timestamp:
                continue
            yield row


def _account(tmp_path: Path, *, name: str, traded: tuple[str, ...]) -> DashboardAccount:
    from conftest import make_config

    env_file = tmp_path / f"{name}.env"
    env_file.write_text("DERIBIT_ENV=mainnet\n", encoding="utf-8")
    cfg = make_config(
        tmp_path,
        state_file=tmp_path / f"{name}.json",
        client_id=f"cid-{name}",
        client_secret=f"sec-{name}",
        traded_collaterals=traded,
    )
    return DashboardAccount(
        name=name,
        env_file=env_file,
        config=cfg,
        state_path=Path(cfg.state_file),
        ledger_root=tmp_path / "ledger" / name,
    )


def test_transfer_store_incremental_sync_uses_last_timestamp(tmp_path: Path) -> None:
    store = TransferStore(tmp_path / "transfers.db")
    scope = "scope-a"
    client = _FakeClient(
        {
            "USDC": [
                {
                    "id": 1,
                    "timestamp": 1_700_000_000_000,
                    "type": "transfer",
                    "currency": "USDC",
                    "change": "10",
                    "info": "first",
                },
                {
                    "id": 2,
                    "timestamp": 1_700_010_000_000,
                    "type": "transfer",
                    "currency": "USDC",
                    "change": "20",
                    "info": "second",
                },
            ]
        }
    )

    inserted_first = sync_transfer_rows_for_currency(
        store,
        client,
        scope_key=scope,
        currency="USDC",
        start_ms=1_699_900_000_000,
        end_ms=1_700_020_000_000,
        overlap_ms=0,
    )
    assert inserted_first == 2
    assert store.row_count(scope, "USDC") == 2

    client.rows_by_currency["USDC"].append(
        {
            "id": 3,
            "timestamp": 1_700_020_000_000,
            "type": "transfer",
            "currency": "USDC",
            "change": "5",
            "info": "third",
        }
    )

    inserted_second = sync_transfer_rows_for_currency(
        store,
        client,
        scope_key=scope,
        currency="USDC",
        start_ms=1_699_900_000_000,
        end_ms=1_700_030_000_000,
        overlap_ms=0,
    )
    assert inserted_second == 1
    assert store.row_count(scope, "USDC") == 3
    assert len(client.calls) == 2
    assert client.calls[1][1] == 1_700_010_000_000


def test_build_transfers_payload_from_store_without_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    account = _account(tmp_path, name="cc", traded=("USDC",))
    store = TransferStore(tmp_path / "ledger" / "transfers.db")
    scope = "cid-cc\0sec-cc"
    store.upsert_row(
        scope,
        "USDC",
        TransactionEntry(
            id=42,
            timestamp=1_700_050_000_000,
            type="transfer",
            currency="USDC",
            amount=Decimal("25"),
            balance=None,
            info="cached",
        ),
    )

    payload = build_transfers_payload_from_store(
        [account],
        days=30,
        index_by_ccy={"USDC": Decimal("1")},
        limit_per_account=10,
        store=store,
    )
    assert payload is not None
    assert payload["source"] == "store"
    assert payload["accounts"][0]["transfers"][0]["id"] == 42


def test_aggregate_transfers_reuses_store_on_second_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    account = _account(tmp_path, name="cc", traded=("USDC",))
    fake_rows = {
        "USDC": [
            {
                "id": 10,
                "timestamp": 1_700_000_000_000,
                "type": "transfer",
                "currency": "USDC",
                "change": "100",
                "info": "in",
            },
        ],
    }
    client = _FakeClient(fake_rows)

    monkeypatch.setattr("deribit_engine.frontend_server.DeribitClient", lambda _cfg: client)
    monkeypatch.setattr(
        "deribit_engine.frontend_server.load_config",
        lambda _path, require_private=False: account.config,
    )

    store = TransferStore(tmp_path / "ledger" / "transfers.db")
    index = {"BTC": Decimal("100000"), "ETH": Decimal("5000"), "USDC": Decimal("1")}

    payload1 = aggregate_transfers_for_accounts(
        [account],
        days=30,
        index_by_ccy=index,
        limit_per_account=10,
        store=store,
    )
    assert len(payload1["accounts"][0]["transfers"]) == 1
    assert len(client.calls) == 1

    payload2 = aggregate_transfers_for_accounts(
        [account],
        days=30,
        index_by_ccy=index,
        limit_per_account=10,
        store=store,
    )
    assert len(payload2["accounts"][0]["transfers"]) == 1
    assert len(client.calls) == 2
    assert payload2["accounts"][0]["transfers"][0]["id"] == 10
    full_window_start = 1_700_100_000_000 - 30 * 86400 * 1000
    assert client.calls[1][1] > full_window_start
