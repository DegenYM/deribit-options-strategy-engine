"""Segregated margin book abstraction.

A ``Book`` represents one collateral pool (BTC, ETH, or USDC) and the set
of Deribit products it can trade under Segregated Standard Margin:

- BTC book  → ``BTC-<date>-<strike>-P/C`` (inverse, BTC-settled)
- ETH book  → ``ETH-<date>-<strike>-P/C`` (inverse, ETH-settled)
- USDC book → ``BTC_USDC-<date>-<strike>-P/C`` and
  ``ETH_USDC-<date>-<strike>-P/C`` (linear, USDC-settled)

Each book carries its own equity, IM / MM utilization, and per-leg / per-expiry
caps so that a drawdown in one collateral cannot silently consume capacity in
another. Helpers here centralise those lookups so engine/strategy code only
has to ask the book for a limit, not keep rebuilding ``btc_*`` / ``eth_*``
branches.

The class is intentionally small and immutable once built – ``BookRouter``
builds one ``Book`` per enabled collateral every scan/manage tick.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from .config import BotConfig
from .models import AccountSummary


@dataclass(frozen=True)
class Book:
    """One collateral pool under Segregated Standard Margin.

    ``underlyings`` is the set of base assets that may be traded out of this
    book. For inverse books it contains only the collateral currency. For the
    USDC book both ``BTC`` and ``ETH`` linears are eligible.
    """

    name: str
    collateral: str
    inverse: bool
    underlyings: tuple[str, ...]
    equity: Decimal
    initial_margin: Decimal = Decimal("0")
    maintenance_margin: Decimal = Decimal("0")

    # --- limits ---------------------------------------------------------
    per_leg_im_cap_put: Decimal = Decimal("0.15")
    per_leg_im_cap_call: Decimal = Decimal("0.12")
    expiry_im_cap: Decimal = Decimal("0.30")
    book_im_target: Decimal = Decimal("0.35")
    book_im_hard: Decimal = Decimal("0.45")
    book_mm_target: Decimal = Decimal("0.22")
    book_mm_hard: Decimal = Decimal("0.33")

    # --- liquidity gates ------------------------------------------------
    min_open_interest: Decimal = Decimal("20")
    max_spread_ratio: Decimal = Decimal("0.12")
    min_book_notional_usdc: Decimal = Decimal("3000")

    @property
    def enabled(self) -> bool:
        return self.equity > 0

    def per_leg_im_cap(self, option_type: str) -> Decimal:
        return self.per_leg_im_cap_call if option_type == "call" else self.per_leg_im_cap_put

    def im_utilization(self) -> Decimal:
        if self.equity <= 0:
            return Decimal("0")
        return self.initial_margin / self.equity

    def mm_utilization(self) -> Decimal:
        if self.equity <= 0:
            return Decimal("0")
        return self.maintenance_margin / self.equity

    # ``halt_new_entries`` is evaluated in engine using target/hard thresholds.
    def im_exceeds_target(self) -> bool:
        return self.im_utilization() >= self.book_im_target

    def im_exceeds_hard(self) -> bool:
        return self.im_utilization() >= self.book_im_hard

    def mm_exceeds_target(self) -> bool:
        return self.mm_utilization() >= self.book_mm_target

    def mm_exceeds_hard(self) -> bool:
        return self.mm_utilization() >= self.book_mm_hard

    def covers(self, underlying: str) -> bool:
        return underlying.upper() in self.underlyings

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "collateral": self.collateral,
            "inverse": self.inverse,
            "underlyings": list(self.underlyings),
            "equity": self.equity,
            "initial_margin": self.initial_margin,
            "maintenance_margin": self.maintenance_margin,
            "im_utilization": self.im_utilization(),
            "mm_utilization": self.mm_utilization(),
            "book_im_target": self.book_im_target,
            "book_im_hard": self.book_im_hard,
            "book_mm_target": self.book_mm_target,
            "book_mm_hard": self.book_mm_hard,
        }


@dataclass(frozen=True)
class BookRouter:
    """Build the set of enabled books from config + live account summaries.

    Two independent levers:

    * ``config.scan_underlyings``  – which coins the scanner looks at
      (BTC/ETH options). Legacy fallback: ``managed_currencies``.
    * ``config.traded_collaterals`` – which collateral pools the engine
      actually runs a book for. A pool omitted here is **not** constructed,
      so any dust equity it holds cannot pollute drawdown or IM gates.

    The USDC linear book is only built when ``USDC`` is in
    ``traded_collaterals`` AND at least one underlying is scanned.
    """

    config: BotConfig
    books: tuple[Book, ...] = field(default_factory=tuple)

    @classmethod
    def from_summaries(
        cls,
        config: BotConfig,
        summaries: dict[str, AccountSummary],
    ) -> BookRouter:
        books: list[Book] = []
        scan_underlyings = tuple(c.upper() for c in config.scan_underlyings)
        traded_collaterals = tuple(c.upper() for c in config.traded_collaterals)

        # Inverse books require both the underlying to be in the scan set and
        # the same coin to be whitelisted as a traded collateral.
        for currency in ("BTC", "ETH"):
            if currency not in scan_underlyings:
                continue
            if currency not in traded_collaterals:
                continue
            summary = summaries.get(currency)
            books.append(_build_inverse_book(config, currency, summary))

        # USDC linear book: built only when USDC collateral is whitelisted and
        # at least one underlying is in the scan set.
        if "USDC" in traded_collaterals:
            usdc_summary = summaries.get("USDC")
            usdc_underlyings = tuple(c for c in ("BTC", "ETH") if c in scan_underlyings)
            if usdc_underlyings:
                books.append(_build_usdc_book(config, usdc_summary, usdc_underlyings))

        return cls(config=config, books=tuple(books))

    def enabled_books(self) -> tuple[Book, ...]:
        return tuple(book for book in self.books if book.enabled)

    def for_collateral(self, collateral: str) -> Book | None:
        target = collateral.upper()
        for book in self.books:
            if book.collateral == target:
                return book
        return None

    def for_instrument(self, *, collateral: str, underlying: str) -> Book | None:
        """Find the book that would take a position in this instrument."""
        book = self.for_collateral(collateral)
        if book is None:
            return None
        if not book.covers(underlying):
            return None
        return book

    def to_dict(self) -> dict:
        return {"books": [book.to_dict() for book in self.books]}


def _build_inverse_book(config: BotConfig, currency: str, summary: AccountSummary | None) -> Book:
    equity = summary.equity if summary is not None else Decimal("0")
    initial_margin = summary.initial_margin if summary is not None else Decimal("0")
    maintenance_margin = summary.maintenance_margin if summary is not None else Decimal("0")
    return Book(
        name=f"{currency}_inverse",
        collateral=currency,
        inverse=True,
        underlyings=(currency,),
        equity=equity,
        initial_margin=initial_margin,
        maintenance_margin=maintenance_margin,
        per_leg_im_cap_put=config.per_leg_im_cap_put,
        per_leg_im_cap_call=config.per_leg_im_cap_call,
        expiry_im_cap=config.expiry_im_cap_per_book,
        book_im_target=config.book_im_target,
        book_im_hard=config.book_im_hard,
        book_mm_target=config.book_mm_target,
        book_mm_hard=config.book_mm_hard,
        min_open_interest=config.min_open_interest("reversed", currency),
        max_spread_ratio=config.inverse_max_spread_ratio,
        min_book_notional_usdc=config.inverse_min_book_notional_usdc,
    )


def _build_usdc_book(
    config: BotConfig,
    summary: AccountSummary | None,
    underlyings: tuple[str, ...],
) -> Book:
    equity = summary.equity if summary is not None else Decimal("0")
    initial_margin = summary.initial_margin if summary is not None else Decimal("0")
    maintenance_margin = summary.maintenance_margin if summary is not None else Decimal("0")
    return Book(
        name="USDC_linear",
        collateral="USDC",
        inverse=False,
        underlyings=underlyings,
        equity=equity,
        initial_margin=initial_margin,
        maintenance_margin=maintenance_margin,
        per_leg_im_cap_put=config.per_leg_im_cap_put,
        per_leg_im_cap_call=config.per_leg_im_cap_call,
        expiry_im_cap=config.expiry_im_cap_per_book,
        book_im_target=config.book_im_target,
        book_im_hard=config.book_im_hard,
        book_mm_target=config.book_mm_target,
        book_mm_hard=config.book_mm_hard,
        min_open_interest=config.min_open_interest("linear"),
        max_spread_ratio=config.linear_max_spread_ratio,
        min_book_notional_usdc=config.linear_min_book_notional_usdc,
    )


def inverse_collateral_for_currency(currency: str) -> str:
    """BTC options are BTC-settled; ETH options are ETH-settled."""
    return currency.upper()


def collateral_for_instrument(instrument_name: str) -> str:
    """Infer collateral from Deribit option symbol ("BTC_USDC-..." → USDC)."""
    name = (instrument_name or "").upper()
    if "_USDC-" in name:
        return "USDC"
    if name.startswith("BTC-"):
        return "BTC"
    if name.startswith("ETH-"):
        return "ETH"
    return ""


def summarize_books(books: Iterable[Book]) -> list[dict]:
    return [book.to_dict() for book in books]
