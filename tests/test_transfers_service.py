from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from deribit_engine.frontend_server.transfers_service import aggregate_transfers_for_accounts
from deribit_engine.frontend_server.types import DashboardAccount


class _FakeClient:
    def __init__(self, rows_by_currency: dict[str, list[dict]]):
        self.rows_by_currency = rows_by_currency

    def iter_transaction_log(self, *, currency: str, start_timestamp: int, end_timestamp: int, count: int = 100):
        del start_timestamp, end_timestamp, count
        yield from self.rows_by_currency.get(currency.upper(), [])


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


def test_aggregate_transfers_filters_transfer_rows_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    account = _account(tmp_path, name="cc", traded=("BTC", "USDC"))
    fake_rows = {
        "BTC": [
            {
                "id": 1,
                "timestamp": 1_700_000_000_000,
                "type": "transfer",
                "currency": "BTC",
                "change": "0.01",
                "info": "in",
            },
            {
                "id": 2,
                "timestamp": 1_700_000_100_000,
                "type": "deposit",
                "currency": "BTC",
                "change": "0.02",
                "info": "skip",
            },
            {
                "id": 3,
                "timestamp": 1_700_000_200_000,
                "type": "transfer",
                "currency": "BTC",
                "change": "-0.005",
                "info": "out",
            },
        ],
        "USDC": [
            {
                "id": 4,
                "timestamp": 1_700_000_300_000,
                "type": "transfer",
                "currency": "USDC",
                "change": "100",
                "info": "sweep",
            },
        ],
    }

    def _fake_client(_cfg):
        return _FakeClient(fake_rows)

    monkeypatch.setattr("deribit_engine.frontend_server.DeribitClient", _fake_client)
    monkeypatch.setattr(
        "deribit_engine.frontend_server.load_config",
        lambda _path, require_private=False: account.config,
    )

    payload = aggregate_transfers_for_accounts(
        [account],
        days=30,
        index_by_ccy={"BTC": Decimal("100000"), "ETH": Decimal("5000"), "USDC": Decimal("1")},
        limit_per_account=10,
    )

    assert payload["days_requested"] == 30
    assert len(payload["accounts"]) == 1
    row = payload["accounts"][0]
    assert row["name"] == "cc"
    assert row["transfer_count"] == 3
    assert [item["direction"] for item in row["transfers"]] == ["in", "out", "in"]
    assert row["transfers"][0]["book"] == "USDC"
    assert row["transfers"][1]["book"] == "BTC"
    assert row["transfers"][1]["amount_native"] == "-0.005"


def test_aggregate_transfers_dedupes_shared_api_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from conftest import make_config

    env_a = tmp_path / "a.env"
    env_b = tmp_path / "b.env"
    env_a.write_text("DERIBIT_ENV=mainnet\n", encoding="utf-8")
    env_b.write_text("DERIBIT_ENV=mainnet\n", encoding="utf-8")
    cfg = make_config(
        tmp_path,
        state_file=tmp_path / "a.json",
        client_id="shared-cid",
        client_secret="shared-sec",
        traded_collaterals=("USDC",),
    )
    account_a = DashboardAccount(
        name="a",
        env_file=env_a,
        config=cfg,
        state_path=Path(cfg.state_file),
        ledger_root=tmp_path / "ledger" / "a",
    )
    cfg_b = make_config(
        tmp_path,
        state_file=tmp_path / "b.json",
        client_id="shared-cid",
        client_secret="shared-sec",
        traded_collaterals=("USDC",),
    )
    account_b = DashboardAccount(
        name="b",
        env_file=env_b,
        config=cfg_b,
        state_path=Path(cfg_b.state_file),
        ledger_root=tmp_path / "ledger" / "b",
    )
    calls: list[str] = []

    class _CountingClient(_FakeClient):
        def iter_transaction_log(self, *, currency: str, start_timestamp: int, end_timestamp: int, count: int = 100):
            calls.append(currency.upper())
            return super().iter_transaction_log(
                currency=currency,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                count=count,
            )

    def _fake_client(_cfg):
        return _CountingClient(
            {
                "USDC": [
                    {
                        "id": 9,
                        "timestamp": 1_700_000_400_000,
                        "type": "transfer",
                        "currency": "USDC",
                        "change": "50",
                        "info": "once",
                    }
                ]
            }
        )

    monkeypatch.setattr("deribit_engine.frontend_server.DeribitClient", _fake_client)
    monkeypatch.setattr(
        "deribit_engine.frontend_server.load_config",
        lambda path, require_private=False: account_a.config if path == env_a else account_b.config,
    )

    payload = aggregate_transfers_for_accounts(
        [account_a, account_b],
        days=7,
        index_by_ccy={"USDC": Decimal("1")},
        limit_per_account=5,
    )

    assert calls == ["USDC"]
    assert len(payload["accounts"]) == 2
    assert payload["accounts"][0]["transfer_count"] == 1
    assert payload["accounts"][1]["transfer_count"] == 1
