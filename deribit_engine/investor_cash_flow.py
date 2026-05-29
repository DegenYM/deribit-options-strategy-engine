"""Fetch cumulative investor net deposits from Deribit transaction logs.

Baseline / HWM net subscription (per investor)::

    sum(deposit + withdrawal + transfer)   # USDC-equivalent per book, then summed

across every **operational** sub-account in ``accounts.toml`` (unique API logins,
books BTC/ETH/USDC). Internal moves between configured subs appear as signed
``transfer`` rows on each API and **net to zero** in the aggregate. Capital that
lands on the main account first, then ``transfer`` into a configured sub, is
counted via that sub's inbound ``transfer`` (no main-account API required).
Cross-book transfers on one sub (e.g. USDC→BTC margin) net to ~zero in USDC terms.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .client import DeribitClient
from .config import BotConfig, load_config
from .env_layout import InvestorAccountSpec, InvestorManifest, load_investor_manifest
from .models import EXTERNAL_FLOW_TRANSACTION_TYPES, TransactionEntry
from .utils import to_decimal, utc_now_ms

LOGGER = logging.getLogger(__name__)


def sum_external_flow_native_in_window(
    client: DeribitClient,
    *,
    currency: str,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> Decimal:
    """Sum signed deposit / withdrawal / transfer in ``[start, end]`` (paginated).

    Uses ``iter_transaction_log`` so sub-account UI transfers are not dropped
    when the day log has many trade/settlement rows before the transfer line.
    """
    net = Decimal("0")
    ccy = currency.upper()
    if hasattr(client, "iter_transaction_log"):
        rows = client.iter_transaction_log(
            currency=ccy,
            start_timestamp=start_timestamp_ms,
            end_timestamp=end_timestamp_ms,
            count=100,
        )
    else:
        rows = client.get_transaction_log(
            currency=ccy,
            start_timestamp=start_timestamp_ms,
            end_timestamp=end_timestamp_ms,
            count=100,
        )
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        entry_type = str(payload.get("type") or "").lower()
        if entry_type not in EXTERNAL_FLOW_TRANSACTION_TYPES:
            continue
        amount_raw = payload.get("change")
        if amount_raw is None:
            amount_raw = payload.get("amount")
        net += to_decimal(amount_raw)
    return net


@dataclass(frozen=True)
class ApiIdentityFlow:
    """Net deposit/withdrawal for one Deribit API login."""

    label: str
    client_id: str
    subscription_native_by_book: dict[str, Decimal]
    transfer_native_by_book: dict[str, Decimal]
    deposit_count: int
    withdrawal_count: int
    transfer_count: int


@dataclass(frozen=True)
class SubscriptionFlowLine:
    """One external cash-flow row for investor fee reports."""

    identity_label: str
    client_id: str
    book: str
    timestamp_ms: int
    flow_type: str
    amount_native: Decimal
    usdc_equiv: Decimal
    included_in_subscription: bool


@dataclass(frozen=True)
class CumulativeNetFlow:
    cumulative_net_flow_usdc: Decimal
    net_flow_native_by_book: dict[str, Decimal]
    start_timestamp_ms: int
    end_timestamp_ms: int
    entry_count: int
    by_api_identity: tuple[ApiIdentityFlow, ...] = field(default_factory=tuple)
    transfer_native_by_book: dict[str, Decimal] = field(default_factory=dict)


DEFAULT_FEE_FLOW_START_DATE = "2026-01-01"


def default_fee_flow_start_ms() -> int:
    """UTC start of ``DEFAULT_FEE_FLOW_START_DATE`` for transaction-log scans."""
    dt = datetime.strptime(DEFAULT_FEE_FLOW_START_DATE, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def effective_fee_flow_start_ms(start_timestamp_ms: int) -> int:
    """Map legacy ``0`` (scan from epoch) to ``default_fee_flow_start_ms()``."""
    if start_timestamp_ms <= 0:
        return default_fee_flow_start_ms()
    return start_timestamp_ms


def parse_fee_flow_start_ms(values: dict[str, str | None]) -> int:
    """``FEE_FLOW_START_DATE=YYYY-MM-DD`` in ``.env.investor``; default 2026-01-01 UTC."""
    raw = values.get("FEE_FLOW_START_DATE")
    if raw is None or not str(raw).strip():
        return default_fee_flow_start_ms()
    text = str(raw).strip()
    try:
        dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"Invalid FEE_FLOW_START_DATE {text!r}; expected YYYY-MM-DD") from exc
    return int(dt.timestamp() * 1000)


def initial_hwm_from_net_flow(
    cumulative_net_flow_usdc: Decimal,
    collateral_spot_usdc: Decimal,
) -> Decimal:
    """Legacy helper: subtract a single USDC collateral deduction (e.g. config spot)."""
    return max(Decimal("0"), cumulative_net_flow_usdc - collateral_spot_usdc)


def initial_spot_deduction_usdc(
    net_flow_native_by_book: dict[str, Decimal],
    *,
    index_by_ccy: dict[str, Decimal],
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """BTC/ETH net subscription native amounts treated as initial spot inventory (USDC equiv.).

    Returns ``(btc_native, eth_native, spot_deduction_usdc, initial_hwm_nav_perf)``.
    """
    btc_native = net_flow_native_by_book.get("BTC", Decimal("0"))
    eth_native = net_flow_native_by_book.get("ETH", Decimal("0"))
    btc_usdc = native_book_amount_to_usdc(btc_native, "BTC", index_by_ccy)
    eth_usdc = native_book_amount_to_usdc(eth_native, "ETH", index_by_ccy)
    spot_usdc = btc_usdc + eth_usdc
    cumulative = _sum_books_to_usdc(
        net_flow_native_by_book,
        ordered_net_flow_books(net_flow_native_by_book),
        index_by_ccy,
    )
    initial_hwm = max(Decimal("0"), cumulative - spot_usdc)
    return btc_native, eth_native, spot_usdc, initial_hwm


def _api_identity_key(config: BotConfig) -> str:
    return f"{config.client_id.strip().lower()}\0{config.client_secret.strip()}"


_FEE_FLOW_BOOKS: tuple[str, ...] = ("BTC", "ETH", "USDC")


def _fee_flow_books() -> tuple[str, ...]:
    """Books scanned for investor fee baseline (independent of strategy traded_collaterals)."""
    return _FEE_FLOW_BOOKS


def _group_accounts_by_api_identity(
    manifest: InvestorManifest,
) -> dict[str, tuple[tuple[InvestorAccountSpec, BotConfig], ...]]:
    buckets: dict[str, list[tuple[InvestorAccountSpec, BotConfig]]] = defaultdict(list)
    for account in manifest.operational_accounts():
        cfg = load_config(account.env_path, require_private=False)
        buckets[_api_identity_key(cfg)].append((account, cfg))
    return {key: tuple(items) for key, items in buckets.items()}


def _counts_toward_subscription(entry: TransactionEntry) -> bool:
    """``deposit``, ``withdrawal``, and ``transfer`` (internal transfers cancel in the aggregate)."""
    return entry.type in {"deposit", "withdrawal", "transfer"}


def native_book_amount_to_usdc(
    amount_native: Decimal,
    book: str,
    index_by_ccy: dict[str, Decimal],
) -> Decimal:
    """Convert a per-book net-flow native balance to USDC equivalent."""
    if book == "USDC":
        return amount_native
    index = index_by_ccy.get(book, Decimal("0"))
    return amount_native * index


def _native_to_usdc(amount_native: Decimal, book: str, index_by_ccy: dict[str, Decimal]) -> Decimal:
    return native_book_amount_to_usdc(amount_native, book, index_by_ccy)


_FEE_REPORT_BOOK_ORDER: tuple[str, ...] = ("BTC", "ETH", "USDC")


def ordered_net_flow_books(native_by_book: dict[str, Decimal]) -> tuple[str, ...]:
    """Stable book order for fee reports (BTC, ETH, USDC, then any others)."""
    extra = tuple(sorted(book for book in native_by_book if book not in _FEE_REPORT_BOOK_ORDER))
    return tuple(book for book in _FEE_REPORT_BOOK_ORDER if book in native_by_book) + extra


def _sum_books_to_usdc(
    native_by_book: dict[str, Decimal],
    books: tuple[str, ...],
    index_by_ccy: dict[str, Decimal],
) -> Decimal:
    return sum(
        (_native_to_usdc(native_by_book.get(book, Decimal("0")), book, index_by_ccy) for book in books),
        Decimal("0"),
    )


def _flow_type_label(entry: TransactionEntry) -> str:
    if entry.type == "transfer":
        if entry.amount > 0:
            return "transfer_in"
        if entry.amount < 0:
            return "transfer_out"
        return "transfer"
    return entry.type


def _collect_identity_flow_lines(
    *,
    label: str,
    config: BotConfig,
    books: tuple[str, ...],
    start_ms: int,
    end_ms: int,
    index_by_ccy: dict[str, Decimal],
) -> list[SubscriptionFlowLine]:
    client = DeribitClient(config)
    lines: list[SubscriptionFlowLine] = []
    client_id = config.client_id.strip()

    for book in books:
        for payload in client.iter_transaction_log(
            currency=book,
            start_timestamp=start_ms,
            end_timestamp=end_ms,
            count=100,
        ):
            entry = TransactionEntry.from_api(payload)
            if entry.type not in {"deposit", "withdrawal", "transfer"}:
                continue
            included = _counts_toward_subscription(entry)
            lines.append(
                SubscriptionFlowLine(
                    identity_label=label,
                    client_id=client_id,
                    book=book,
                    timestamp_ms=entry.timestamp,
                    flow_type=_flow_type_label(entry),
                    amount_native=entry.amount,
                    usdc_equiv=_native_to_usdc(entry.amount, book, index_by_ccy),
                    included_in_subscription=included,
                )
            )
    return lines


def fetch_subscription_flow_lines(
    investor: str | Path,
    *,
    repo_root: Path,
    index_by_ccy: dict[str, Decimal],
    start_timestamp_ms: int | None = None,
    end_timestamp_ms: int | None = None,
) -> list[SubscriptionFlowLine]:
    """Return every deposit / withdrawal / transfer row (sorted by time)."""
    manifest = load_investor_manifest(investor, repo_root=repo_root)
    identity_groups = _group_accounts_by_api_identity(manifest)
    if not identity_groups:
        raise RuntimeError(f"No enabled accounts with API creds for investor {manifest.investor_id!r}")

    from dotenv import dotenv_values

    from .env_layout import resolve_investor_env_path

    investor_env = resolve_investor_env_path(manifest.root)
    env_values = dict(dotenv_values(investor_env)) if investor_env is not None else {}
    start_ms = start_timestamp_ms if start_timestamp_ms is not None else parse_fee_flow_start_ms(env_values)
    end_ms = end_timestamp_ms if end_timestamp_ms is not None else utc_now_ms()

    lines: list[SubscriptionFlowLine] = []
    for items in identity_groups.values():
        accounts = tuple(account for account, _cfg in items)
        books = _fee_flow_books()
        _account, cfg = items[0]
        label = ",".join(account.slug for account, _ in items)
        lines.extend(
            _collect_identity_flow_lines(
                label=label,
                config=cfg,
                books=books,
                start_ms=start_ms,
                end_ms=end_ms,
                index_by_ccy=index_by_ccy,
            )
        )
    return sorted(lines, key=lambda row: (row.timestamp_ms, row.identity_label, row.book, row.flow_type))


def _fetch_identity_flow(
    *,
    label: str,
    config: BotConfig,
    books: tuple[str, ...],
    start_ms: int,
    end_ms: int,
) -> ApiIdentityFlow:
    client = DeribitClient(config)
    subscription_native: dict[str, Decimal] = {book: Decimal("0") for book in books}
    transfer_native: dict[str, Decimal] = {book: Decimal("0") for book in books}
    deposit_count = 0
    withdrawal_count = 0
    transfer_count = 0

    for book in books:
        for payload in client.iter_transaction_log(
            currency=book,
            start_timestamp=start_ms,
            end_timestamp=end_ms,
            count=100,
        ):
            entry = TransactionEntry.from_api(payload)
            if _counts_toward_subscription(entry):
                subscription_native[book] += entry.amount
                if entry.type == "deposit":
                    deposit_count += 1
                elif entry.type == "withdrawal":
                    withdrawal_count += 1
                else:
                    transfer_count += 1
            elif entry.type == "transfer":
                transfer_native[book] += entry.amount

    return ApiIdentityFlow(
        label=label,
        client_id=config.client_id.strip(),
        subscription_native_by_book=subscription_native,
        transfer_native_by_book=transfer_native,
        deposit_count=deposit_count,
        withdrawal_count=withdrawal_count,
        transfer_count=transfer_count,
    )


def fetch_cumulative_net_flow_usdc(
    investor: str | Path,
    *,
    repo_root: Path,
    index_by_ccy: dict[str, Decimal],
    start_timestamp_ms: int | None = None,
    end_timestamp_ms: int | None = None,
) -> CumulativeNetFlow:
    """Sum ``deposit`` + ``withdrawal`` + ``transfer`` across all configured API logins.

    Inter-sub transfers cancel when aggregated; main→sub funding is captured via
    inbound ``transfer`` on the sub-account API.
    """
    manifest = load_investor_manifest(investor, repo_root=repo_root)
    identity_groups = _group_accounts_by_api_identity(manifest)
    if not identity_groups:
        raise RuntimeError(f"No enabled accounts with API creds for investor {manifest.investor_id!r}")

    from dotenv import dotenv_values

    from .env_layout import resolve_investor_env_path

    investor_env = resolve_investor_env_path(manifest.root)
    env_values = dict(dotenv_values(investor_env)) if investor_env is not None else {}
    start_ms = start_timestamp_ms if start_timestamp_ms is not None else parse_fee_flow_start_ms(env_values)
    end_ms = end_timestamp_ms if end_timestamp_ms is not None else utc_now_ms()

    identity_flows: list[ApiIdentityFlow] = []
    all_books: set[str] = set()
    for items in identity_groups.values():
        accounts = tuple(account for account, _cfg in items)
        books = _fee_flow_books()
        all_books.update(books)
        _account, cfg = items[0]
        label = ",".join(account.slug for account, _ in items)
        identity_flows.append(
            _fetch_identity_flow(
                label=label,
                config=cfg,
                books=books,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )

    books_sorted = tuple(sorted(all_books))
    net_native_by_book: dict[str, Decimal] = {book: Decimal("0") for book in books_sorted}
    transfer_native_by_book: dict[str, Decimal] = {book: Decimal("0") for book in books_sorted}
    entry_count = 0

    for identity in identity_flows:
        entry_count += identity.deposit_count + identity.withdrawal_count
        for book, amount in identity.subscription_native_by_book.items():
            net_native_by_book[book] = net_native_by_book.get(book, Decimal("0")) + amount
        for book, amount in identity.transfer_native_by_book.items():
            transfer_native_by_book[book] = transfer_native_by_book.get(book, Decimal("0")) + amount

    cumulative = _sum_books_to_usdc(net_native_by_book, books_sorted, index_by_ccy)
    LOGGER.info(
        "cumulative net flow investor=%s usdc=%s entries=%s identities=%s books=%s",
        manifest.investor_id,
        cumulative,
        entry_count,
        len(identity_flows),
        ",".join(books_sorted),
    )
    return CumulativeNetFlow(
        cumulative_net_flow_usdc=cumulative,
        net_flow_native_by_book=net_native_by_book,
        start_timestamp_ms=start_ms,
        end_timestamp_ms=end_ms,
        entry_count=entry_count,
        by_api_identity=tuple(identity_flows),
        transfer_native_by_book=transfer_native_by_book,
    )


def flow_report_dict(flow: CumulativeNetFlow, *, index_by_ccy: dict[str, Decimal]) -> dict[str, Any]:
    books = tuple(sorted(set(flow.net_flow_native_by_book) | set(flow.transfer_native_by_book)))
    return {
        "cumulative_net_flow_usdc": str(flow.cumulative_net_flow_usdc),
        "net_flow_native_by_book": {k: str(v) for k, v in flow.net_flow_native_by_book.items()},
        "transfer_native_by_book_excluded": {k: str(v) for k, v in flow.transfer_native_by_book.items()},
        "transfer_usdc_excluded": str(_sum_books_to_usdc(flow.transfer_native_by_book, books, index_by_ccy)),
        "entry_count": flow.entry_count,
        "start_timestamp_ms": flow.start_timestamp_ms,
        "end_timestamp_ms": flow.end_timestamp_ms,
        "by_api_identity": [
            {
                "label": item.label,
                "client_id": item.client_id,
                "subscription_native_by_book": {k: str(v) for k, v in item.subscription_native_by_book.items()},
                "transfer_native_by_book_excluded": {k: str(v) for k, v in item.transfer_native_by_book.items()},
                "deposit_count": item.deposit_count,
                "withdrawal_count": item.withdrawal_count,
                "transfer_count_excluded": item.transfer_count,
            }
            for item in flow.by_api_identity
        ],
        "note": (
            "net_flow_native_by_book sums deposit, withdrawal, and transfer across "
            "all configured sub-account APIs; internal sub transfers net to zero."
        ),
    }
