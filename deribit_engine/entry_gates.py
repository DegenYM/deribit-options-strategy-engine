"""Entry pacing gates shared by scanner, entry, and portfolio snapshot."""

from __future__ import annotations

from .models import NakedPutCandidate, PortfolioSnapshot, RiskRegime, TradeGroup


def last_entry_timestamp_ms_by_book(groups: list[TradeGroup]) -> dict[str, int]:
    """Most recent ``entry_timestamp_ms`` per collateral book across all groups."""
    out: dict[str, int] = {}
    for group in groups:
        ts = int(group.entry_timestamp_ms or 0)
        if ts <= 0:
            continue
        book = group.collateral_book()
        out[book] = max(out.get(book, 0), ts)
    return out


def entry_cooldown_active(
    *,
    book: str,
    last_entry_by_book: dict[str, int],
    now_ms: int,
    cooldown_minutes: int,
) -> bool:
    """Return True when ``book`` is still inside the post-entry cooldown window."""
    if cooldown_minutes <= 0:
        return False
    last_ms = last_entry_by_book.get(book.upper(), 0)
    if last_ms <= 0:
        return False
    return now_ms - last_ms < cooldown_minutes * 60 * 1000


def open_group_count_for_book(
    groups: list[TradeGroup],
    book: str,
    *,
    strategy: str | None = None,
) -> int:
    book_u = book.upper()
    count = 0
    for group in groups:
        if group.status != "open":
            continue
        if group.collateral_book() != book_u:
            continue
        if strategy is not None and (group.strategy or "").strip() != strategy:
            continue
        count += 1
    return count


def currency_entry_halt_reasons(
    *,
    currency: str,
    regime: RiskRegime,
    regime_detail: tuple[str, ...],
    crisis_open_group: bool,
    hard_derisk_on_crisis_open_group: bool,
) -> list[str]:
    """Reasons that block new entries for one managed underlying (BTC / ETH)."""
    ccy = currency.upper()
    reasons: list[str] = []
    if regime is not RiskRegime.NORMAL:
        reasons.append(f"{ccy}: regime={regime.value}")
    if any(note.startswith("data_unavailable") for note in regime_detail):
        reasons.append(f"{ccy}: regime data_unavailable")
    if hard_derisk_on_crisis_open_group and crisis_open_group:
        reasons.append(f"{ccy}: open_trade_group_in_crisis_regime")
    return reasons


def build_halt_new_entries_by_currency(
    *,
    managed_currencies: tuple[str, ...],
    regime_by_currency: dict[str, RiskRegime],
    regime_detail_by_currency: dict[str, tuple[str, ...]],
    crisis_currencies_with_open_groups: set[str],
    hard_derisk_on_crisis_open_group: bool,
    portfolio_blocks_all: bool,
) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for currency in managed_currencies:
        ccy = currency.upper()
        reasons = currency_entry_halt_reasons(
            currency=ccy,
            regime=regime_by_currency.get(ccy, RiskRegime.CRISIS),
            regime_detail=regime_detail_by_currency.get(ccy, ()),
            crisis_open_group=ccy in crisis_currencies_with_open_groups,
            hard_derisk_on_crisis_open_group=hard_derisk_on_crisis_open_group,
        )
        out[ccy] = portfolio_blocks_all or bool(reasons)
    return out


def candidate_book_entry_halted(snapshot: PortfolioSnapshot, candidate: NakedPutCandidate) -> bool:
    book = (candidate.collateral_currency or candidate.currency or "").upper()
    if not book:
        return False
    return bool(snapshot.halt_entries_by_book.get(book))


def underlying_entry_halted(snapshot: PortfolioSnapshot, underlying: str) -> bool:
    """True when new risk on this underlying (BTC / ETH) must not be opened.

    Linear ``ETH_USDC`` / ``BTC_USDC`` contracts use the underlying's regime,
    not the USDC collateral book's drawdown/IM flags alone.
    """
    ccy = underlying.upper()
    if not ccy:
        return True
    if snapshot.portfolio_wide_entry_halt:
        return True
    return bool(snapshot.halt_new_entries_by_currency.get(ccy, True))


def usdc_linear_underlyings(scan_underlyings: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(ccy for ccy in ("BTC", "ETH") if ccy in tuple(u.upper() for u in scan_underlyings))


def append_underlying_regime_halt_reasons_for_usdc_book(
    halt_reasons_by_book: dict[str, list[str]],
    *,
    scan_underlyings: tuple[str, ...],
    halt_new_entries_by_currency: dict[str, bool],
    regime_by_currency: dict[str, RiskRegime],
) -> None:
    """Surface per-underlying entry halts on the USDC book card (ETH-USDC / BTC-USDC)."""
    reasons = halt_reasons_by_book.setdefault("USDC", [])
    for ccy in usdc_linear_underlyings(scan_underlyings):
        if not halt_new_entries_by_currency.get(ccy, False):
            continue
        regime = regime_by_currency.get(ccy, RiskRegime.CRISIS)
        line = f"underlying {ccy}: entry halted (regime={regime.value})"
        if line not in reasons:
            reasons.append(line)


def candidate_entry_halted(snapshot: PortfolioSnapshot, candidate: NakedPutCandidate) -> bool:
    """True when this candidate's underlying or collateral book cannot open new risk."""
    currency = (candidate.currency or "").upper()
    if not currency:
        return True
    if underlying_entry_halted(snapshot, currency):
        return True
    return candidate_book_entry_halted(snapshot, candidate)
