from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..client import DeribitClient
from ..investor_cash_flow import cash_flow_scan_currencies, native_book_amount_to_usdc
from ..models import TransactionEntry
from ..transfer_store import (
    DEFAULT_TRANSFER_SYNC_OVERLAP_MS,
    TransferStore,
    transfers_db_path_for_accounts,
)
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


def _window_bounds(*, days: int, end_ms: int | None = None) -> tuple[int, int]:
    end = int(end_ms if end_ms is not None else utc_now_ms())
    start = end - days * 86400 * 1000
    return start, end


def transfer_store_has_data(accounts: list[DashboardAccount], store: TransferStore) -> bool:
    for account in accounts:
        if not _has_private_creds(account.config):
            continue
        identity = _live_api_identity(account)
        for book in cash_flow_scan_currencies(account.config.traded_collaterals):
            if store.row_count(identity, book) > 0:
                return True
    return False


def _account_transfer_rows(
    account: DashboardAccount,
    *,
    store: TransferStore,
    start_ms: int,
    end_ms: int,
) -> tuple[list[tuple[TransactionEntry, str]], list[str]]:
    identity = _live_api_identity(account)
    books = cash_flow_scan_currencies(account.config.traded_collaterals)
    all_rows: list[tuple[TransactionEntry, str]] = []
    for book in books:
        for entry in store.list_rows(identity, book, since_ms=start_ms, until_ms=end_ms):
            all_rows.append((entry, book))
    return all_rows, list(books)


def build_transfers_payload_from_store(
    accounts: list[DashboardAccount],
    *,
    days: int,
    index_by_ccy: dict[str, Decimal],
    limit_per_account: int = 100,
    store: TransferStore | None = None,
    end_ms: int | None = None,
) -> dict[str, Any] | None:
    """Read transfer rows from sqlite only — no Deribit sync."""
    transfer_store = store or TransferStore(transfers_db_path_for_accounts(accounts))
    if not transfer_store_has_data(accounts, transfer_store):
        return None
    start_ms, resolved_end_ms = _window_bounds(days=days, end_ms=end_ms)
    account_payloads: list[dict[str, Any]] = []
    for account in accounts:
        if not _has_private_creds(account.config):
            continue
        all_rows, books = _account_transfer_rows(
            account,
            store=transfer_store,
            start_ms=start_ms,
            end_ms=resolved_end_ms,
        )
        all_rows.sort(key=lambda item: item[0].timestamp, reverse=True)
        limited = all_rows[: max(limit_per_account, 0)]
        account_payloads.append(
            {
                "name": account.name,
                "env": account.config.env,
                "option_strategy": account.config.option_strategy,
                "traded_collaterals": list(account.config.traded_collaterals),
                "books_scanned": books,
                "transfer_count": len(all_rows),
                "transfers": [_entry_to_row(entry, book, index_by_ccy) for entry, book in limited],
            }
        )
    return {
        "days_requested": days,
        "start_timestamp_ms": start_ms,
        "end_timestamp_ms": resolved_end_ms,
        "store_path": str(transfer_store.path),
        "source": "store",
        "accounts": account_payloads,
    }


def fetch_transfer_rows_for_currency(
    client: DeribitClient,
    *,
    currency: str,
    start_ms: int,
    end_ms: int,
    max_transfers: int | None = None,
) -> list[TransactionEntry]:
    """Collect transfer rows newest-first; stop once ``max_transfers`` reached."""
    rows: list[TransactionEntry] = []
    for payload in client.iter_transaction_log(
        currency=currency.upper(),
        start_timestamp=start_ms,
        end_timestamp=end_ms,
        count=100,
    ):
        entry = TransactionEntry.from_api(payload)
        if entry.type != "transfer":
            continue
        rows.append(entry)
        if max_transfers is not None and len(rows) >= max_transfers:
            break
    return rows


def sync_transfer_rows_for_currency(
    store: TransferStore,
    client: DeribitClient,
    *,
    scope_key: str,
    currency: str,
    start_ms: int,
    end_ms: int,
    overlap_ms: int = DEFAULT_TRANSFER_SYNC_OVERLAP_MS,
) -> int:
    """Fetch only transfer rows newer than the latest row already stored."""
    book = currency.upper()
    last_ts = store.max_timestamp_ms(scope_key, book)
    fetch_start = max(start_ms, last_ts - overlap_ms) if last_ts is not None else start_ms
    inserted = 0
    for payload in client.iter_transaction_log(
        currency=book,
        start_timestamp=fetch_start,
        end_timestamp=end_ms,
        count=100,
    ):
        entry = TransactionEntry.from_api(payload)
        if entry.type != "transfer":
            continue
        if store.upsert_row(scope_key, book, entry):
            inserted += 1
    store.touch_sync_meta(scope_key, book, synced_through_ms=end_ms)
    return inserted


def aggregate_transfers_for_accounts(
    accounts: list[DashboardAccount],
    *,
    days: int,
    index_by_ccy: dict[str, Decimal],
    limit_per_account: int = 100,
    store: TransferStore | None = None,
    sync_deribit: bool = True,
) -> dict[str, Any]:
    """Return transfer rows per dashboard account for its tracked collateral books."""
    import deribit_engine.frontend_server as pkg

    start_ms, end_ms = _window_bounds(days=days)
    transfer_store = store or TransferStore(transfers_db_path_for_accounts(accounts))
    identity_clients: dict[str, DeribitClient] = {}
    synced_identity_books: set[tuple[str, str]] = set()
    account_payloads: list[dict[str, Any]] = []

    for account in accounts:
        if not _has_private_creds(account.config):
            continue
        identity = _live_api_identity(account)
        books = cash_flow_scan_currencies(account.config.traded_collaterals)
        if sync_deribit:
            if identity not in identity_clients:
                cfg = pkg.load_config(account.env_file, require_private=True)
                identity_clients[identity] = pkg.DeribitClient(cfg)
            client = identity_clients[identity]
            for book in books:
                cache_key = (identity, book)
                if cache_key not in synced_identity_books:
                    sync_transfer_rows_for_currency(
                        transfer_store,
                        client,
                        scope_key=identity,
                        currency=book,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    )
                    synced_identity_books.add(cache_key)
        all_rows, scanned_books = _account_transfer_rows(
            account,
            store=transfer_store,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        all_rows.sort(key=lambda item: item[0].timestamp, reverse=True)
        limited = all_rows[: max(limit_per_account, 0)]
        account_payloads.append(
            {
                "name": account.name,
                "env": account.config.env,
                "option_strategy": account.config.option_strategy,
                "traded_collaterals": list(account.config.traded_collaterals),
                "books_scanned": scanned_books,
                "transfer_count": len(all_rows),
                "transfers": [_entry_to_row(entry, book, index_by_ccy) for entry, book in limited],
            }
        )

    return {
        "days_requested": days,
        "start_timestamp_ms": start_ms,
        "end_timestamp_ms": end_ms,
        "store_path": str(transfer_store.path),
        "source": "live" if sync_deribit else "store",
        "accounts": account_payloads,
    }
