from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..client import DeribitClient
from ..investor_cash_flow import cash_flow_scan_currencies, native_book_amount_to_usdc
from ..models import TransactionEntry
from ..utils import utc_now_ms
from .helpers import _has_private_creds, _live_api_identity
from .types import DashboardAccount


def _transfer_direction(entry: TransactionEntry) -> str:
    if entry.amount > 0:
        return "in"
    if entry.amount < 0:
        return "out"
    return "flat"


def _entry_to_row(entry: TransactionEntry, book: str, index_by_ccy: dict[str, Decimal]) -> dict[str, Any]:
    return {
        "id": entry.id,
        "timestamp_ms": entry.timestamp,
        "book": book,
        "direction": _transfer_direction(entry),
        "amount_native": str(entry.amount),
        "usdc_equiv": str(native_book_amount_to_usdc(entry.amount, book, index_by_ccy)),
        "info": entry.info,
        "balance_after": str(entry.balance) if entry.balance is not None else None,
    }


def fetch_transfer_rows_for_currency(
    client: DeribitClient,
    *,
    currency: str,
    start_ms: int,
    end_ms: int,
) -> list[TransactionEntry]:
    rows: list[TransactionEntry] = []
    for payload in client.iter_transaction_log(
        currency=currency.upper(),
        start_timestamp=start_ms,
        end_timestamp=end_ms,
        count=100,
    ):
        entry = TransactionEntry.from_api(payload)
        if entry.type == "transfer":
            rows.append(entry)
    return rows


def aggregate_transfers_for_accounts(
    accounts: list[DashboardAccount],
    *,
    days: int,
    index_by_ccy: dict[str, Decimal],
    limit_per_account: int = 100,
) -> dict[str, Any]:
    """Return transfer rows per dashboard account for its tracked collateral books."""
    import deribit_engine.frontend_server as pkg

    end_ms = utc_now_ms()
    start_ms = end_ms - days * 86400 * 1000
    identity_clients: dict[str, DeribitClient] = {}
    rows_by_identity_book: dict[tuple[str, str], list[TransactionEntry]] = {}
    account_payloads: list[dict[str, Any]] = []

    for account in accounts:
        if not _has_private_creds(account.config):
            continue
        identity = _live_api_identity(account)
        if identity not in identity_clients:
            cfg = pkg.load_config(account.env_file, require_private=True)
            identity_clients[identity] = pkg.DeribitClient(cfg)
        client = identity_clients[identity]
        books = cash_flow_scan_currencies(account.config.traded_collaterals)
        all_rows: list[tuple[TransactionEntry, str]] = []
        for book in books:
            cache_key = (identity, book)
            if cache_key not in rows_by_identity_book:
                rows_by_identity_book[cache_key] = fetch_transfer_rows_for_currency(
                    client,
                    currency=book,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            for entry in rows_by_identity_book[cache_key]:
                all_rows.append((entry, book))
        all_rows.sort(key=lambda item: item[0].timestamp, reverse=True)
        limited = all_rows[: max(limit_per_account, 0)]
        account_payloads.append(
            {
                "name": account.name,
                "env": account.config.env,
                "option_strategy": account.config.option_strategy,
                "traded_collaterals": list(account.config.traded_collaterals),
                "books_scanned": list(books),
                "transfer_count": len(all_rows),
                "transfers": [_entry_to_row(entry, book, index_by_ccy) for entry, book in limited],
            }
        )

    return {
        "days_requested": days,
        "start_timestamp_ms": start_ms,
        "end_timestamp_ms": end_ms,
        "accounts": account_payloads,
    }
