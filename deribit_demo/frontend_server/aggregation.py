from __future__ import annotations

import copy
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..realized_summary import realized_summary_from_closed
from ..utils import utc_now
from .types import (
    DashboardAccount,
    _TtlCache,
)

LOGGER = logging.getLogger(__name__)

from .exchange import (
    _bot_for_account,
    _status_payload_for_account,
)
from .groups_service import _closed_groups_payload
from .helpers import (
    _daily_equity_by_utc_day,
    _dec,
    _dedupe_trade_group_rows,
    _equity_on_day,
    _has_private_creds,
    _live_api_identity,
    _ratio,
    _sum_decimal_dict,
    _sum_decimal_field,
    _tag_rows,
    _weighted_ratio_by_book,
    _worst_regime,
)


def _merge_decimal_dict_union_max(into: dict[str, Decimal], extra: dict[str, Any]) -> None:
    """Union-merge ``extra`` into ``into`` (upper-case keys); same-book values should match — keep max."""
    for raw_key, raw_val in (extra or {}).items():
        k = str(raw_key).upper()
        v = _dec(raw_val)
        if k not in into:
            into[k] = v
        else:
            into[k] = max(into[k], v)


def _merge_portfolio_for_same_api_identity(base: dict[str, Any], extra: dict[str, Any]) -> None:
    """Mutate ``base`` portfolio dict by layering per-book views from ``extra``.

    Same Deribit login may load multiple env files with different ``TRADED_COLLATERALS``.
    Each bot snapshot only lists books it manages; union-merge restores missing collateral rows
    for the dashboard without double-counting headline totals (those stay on the first snapshot).
    """
    if not extra:
        return

    for key in (
        "equity_by_book",
        "day_start_equity_by_book",
        "day_net_flow_usdc_by_book",
        "day_pnl_usdc_ex_flow_by_book",
        "day_pnl_usdc_ex_flow_ex_spot_by_book",
        "day_drawdown_pct_by_book",
    ):
        merged_map: dict[str, Decimal] = {}
        for raw_k, raw_v in (base.get(key) or {}).items():
            merged_map[str(raw_k).upper()] = _dec(raw_v)
        _merge_decimal_dict_union_max(merged_map, extra.get(key) or {})
        base[key] = merged_map

    base["halt_new_entries"] = bool(base.get("halt_new_entries")) or bool(extra.get("halt_new_entries"))
    base["hard_derisk"] = bool(base.get("hard_derisk")) or bool(extra.get("hard_derisk"))
    base["cooling_down"] = bool(base.get("cooling_down")) or bool(extra.get("cooling_down"))

    base_delta = dict(base.get("delta_totals_by_currency") or {})
    _merge_decimal_dict_union_max(base_delta, extra.get("delta_totals_by_currency") or {})
    base["delta_totals_by_currency"] = base_delta

    base_reg = dict(base.get("regime_by_currency") or {})
    for raw_k, raw_v in (extra.get("regime_by_currency") or {}).items():
        kk = str(raw_k).upper()
        base_reg[kk] = _worst_regime([str(base_reg.get(kk, "normal")), str(raw_v)])
    base["regime_by_currency"] = base_reg

    base_margin = dict(base.get("margin_ratios_by_currency") or {})
    for raw_k, ratios in (extra.get("margin_ratios_by_currency") or {}).items():
        kk = str(raw_k).upper()
        if kk not in base_margin:
            base_margin[kk] = ratios
    base["margin_ratios_by_currency"] = base_margin

    base_detail = {str(k).upper(): list(v or []) for k, v in (base.get("regime_detail_by_currency") or {}).items()}
    for raw_k, details in (extra.get("regime_detail_by_currency") or {}).items():
        kk = str(raw_k).upper()
        base_detail.setdefault(kk, []).extend(list(details or []))
    base["regime_detail_by_currency"] = base_detail

    base_halt_book = {str(k).upper(): list(v or []) for k, v in (base.get("halt_entry_reasons_by_book") or {}).items()}
    for raw_k, reasons in (extra.get("halt_entry_reasons_by_book") or {}).items():
        kk = str(raw_k).upper()
        base_halt_book.setdefault(kk, []).extend(list(reasons or []))
    base["halt_entry_reasons_by_book"] = base_halt_book

    base_cd_book = {str(k).upper(): v for k, v in (base.get("cooldown_until_ms_by_book") or {}).items()}
    for raw_k, ms in (extra.get("cooldown_until_ms_by_book") or {}).items():
        kk = str(raw_k).upper()
        choices = [base_cd_book.get(kk), ms]
        numeric = [int(x) for x in choices if x is not None]
        base_cd_book[kk] = max(numeric) if numeric else base_cd_book.get(kk)
    base["cooldown_until_ms_by_book"] = base_cd_book

    for key in ("cooling_down_by_book", "hard_derisk_by_book", "halt_entries_by_book"):
        sub = dict(base.get(key) or {})
        for raw_k, flag in (extra.get(key) or {}).items():
            kk = str(raw_k).upper()
            sub[kk] = bool(sub.get(kk)) or bool(flag)
        base[key] = sub


def _merge_status_group_for_equity(group: list[dict[str, Any]]) -> dict[str, Any]:
    """Deep-merge a list of status payloads that share one Deribit API identity."""
    merged = copy.deepcopy(group[0])
    portfolio = dict(merged.get("portfolio") or {})
    merged["portfolio"] = portfolio
    for other in group[1:]:
        _merge_portfolio_for_same_api_identity(portfolio, other.get("portfolio") or {})
    return merged


def _dedupe_statuses_for_equity_aggregate(
    accounts: list[DashboardAccount],
    statuses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One status per `_live_api_identity`, with per-book portfolio rows union-merged.

    Order follows first-seen account row (same as the legacy de-dupe).
    """
    order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for account, status in zip(accounts, statuses, strict=False):
        key = _live_api_identity(account)
        if key not in buckets:
            order.append(key)
        buckets[key].append(status)

    out: list[dict[str, Any]] = []
    for key in order:
        group = buckets[key]
        if len(group) == 1:
            out.append(group[0])
        else:
            out.append(_merge_status_group_for_equity(group))
    return out


def _resolve_apr_effective_capital_usdc(
    accounts: list[DashboardAccount],
    *,
    override: Decimal | None,
    status_payload: dict[str, Any] | None,
) -> Decimal:
    """Match engine ``_effective_capital``: live equity when available, else reference."""
    if override is not None and override > 0:
        return override
    if status_payload:
        equity = _dec((status_payload.get("portfolio") or {}).get("total_equity_usdc"))
        if equity > 0:
            return equity
    reference = sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))
    return reference if reference > 0 else Decimal("0")


def _aggregate_portfolios(
    accounts: list[DashboardAccount],
    statuses: list[dict[str, Any]],
    *,
    equity_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    all_portfolios = [status.get("portfolio") or {} for status in statuses]
    equity_src = equity_statuses if equity_statuses is not None else statuses
    equity_portfolios = [status.get("portfolio") or {} for status in equity_src]
    total_equity = _sum_decimal_field(equity_portfolios, "total_equity_usdc")
    day_start = _sum_decimal_field(equity_portfolios, "day_start_equity_usdc")
    day_net_flow = _sum_decimal_field(equity_portfolios, "day_net_flow_usdc")
    day_pnl = _sum_decimal_field(equity_portfolios, "day_pnl_usdc_ex_flow")
    day_pnl_ex_spot = _sum_decimal_field(equity_portfolios, "day_pnl_usdc_ex_flow_ex_spot")
    open_max_loss = _sum_decimal_field(equity_portfolios, "open_max_loss")
    run_rate = _sum_decimal_field(equity_portfolios, "projected_max_profit_run_rate_usdc")
    reference_capital = sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))
    target_apr_num = sum(
        (account.config.target_portfolio_apr * account.config.reference_capital_usdc for account in accounts),
        Decimal("0"),
    )
    target_apr = _ratio(target_apr_num, reference_capital)
    projected_apr = _ratio(run_rate, reference_capital)

    equity_by_book = _sum_decimal_dict(equity_portfolios, "equity_by_book")
    day_start_by_book = _sum_decimal_dict(equity_portfolios, "day_start_equity_by_book")
    day_net_flow_by_book = _sum_decimal_dict(equity_portfolios, "day_net_flow_usdc_by_book")
    day_pnl_by_book = _sum_decimal_dict(equity_portfolios, "day_pnl_usdc_ex_flow_by_book")
    day_pnl_ex_spot_by_book = _sum_decimal_dict(equity_portfolios, "day_pnl_usdc_ex_flow_ex_spot_by_book")
    drawdown_by_book: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for portfolio in equity_portfolios:
        for book, drawdown in (portfolio.get("day_drawdown_pct_by_book") or {}).items():
            key = str(book).upper()
            drawdown_by_book[key] = max(drawdown_by_book[key], _dec(drawdown))
    im_by_book = _weighted_ratio_by_book(equity_portfolios, ratio_key="im_ratio")
    mm_by_book = _weighted_ratio_by_book(equity_portfolios, ratio_key="mm_ratio")
    margin_ratios = {
        book: {
            "im_ratio": im_by_book.get(book, Decimal("0")),
            "mm_ratio": mm_by_book.get(book, Decimal("0")),
        }
        for book in sorted(set(im_by_book) | set(mm_by_book) | set(equity_by_book))
    }

    regime_by_currency: dict[str, str] = {}
    for portfolio in equity_portfolios:
        for book, regime in (portfolio.get("regime_by_currency") or {}).items():
            key = str(book).upper()
            regime_by_currency[key] = _worst_regime([regime_by_currency.get(key, "normal"), str(regime)])

    def _bool_by_book(key: str) -> dict[str, bool]:
        out: dict[str, bool] = defaultdict(bool)
        for portfolio in equity_portfolios:
            for book, value in (portfolio.get(key) or {}).items():
                out[str(book).upper()] = out[str(book).upper()] or bool(value)
        return dict(out)

    halt_reasons: list[str] = []
    halt_reasons_by_book: dict[str, list[str]] = defaultdict(list)
    regime_detail_by_currency: dict[str, list[str]] = defaultdict(list)
    for account, portfolio in zip(accounts, all_portfolios, strict=False):
        for reason in portfolio.get("halt_entry_reasons") or []:
            halt_reasons.append(f"{account.name}: {reason}")
        for book, reasons in (portfolio.get("halt_entry_reasons_by_book") or {}).items():
            halt_reasons_by_book[str(book).upper()].extend(f"{account.name}: {reason}" for reason in reasons)
        for book, details in (portfolio.get("regime_detail_by_currency") or {}).items():
            regime_detail_by_currency[str(book).upper()].extend(f"{account.name}: {detail}" for detail in details)

    return {
        "total_equity_usdc": total_equity,
        "day_start_equity_usdc": day_start,
        "day_net_flow_usdc": day_net_flow,
        "day_pnl_usdc_ex_flow": day_pnl,
        "day_pnl_usdc_ex_flow_ex_spot": day_pnl_ex_spot,
        "day_drawdown_pct": max((_dec(p.get("day_drawdown_pct")) for p in equity_portfolios), default=Decimal("0")),
        "open_max_loss": open_max_loss,
        "open_max_loss_pct": _ratio(open_max_loss, total_equity),
        "initial_margin_ratio": _ratio(
            sum(
                (_dec(p.get("initial_margin_ratio")) * _dec(p.get("total_equity_usdc")) for p in equity_portfolios),
                Decimal("0"),
            ),
            total_equity,
        ),
        "maintenance_margin_ratio": _ratio(
            sum(
                (_dec(p.get("maintenance_margin_ratio")) * _dec(p.get("total_equity_usdc")) for p in equity_portfolios),
                Decimal("0"),
            ),
            total_equity,
        ),
        "projected_max_profit_run_rate_usdc": run_rate,
        "projected_max_profit_apr": projected_apr,
        "target_progress_ratio": _ratio(projected_apr, target_apr),
        "regime": _worst_regime([str(p.get("regime") or "normal") for p in equity_portfolios]),
        "halt_new_entries": any(bool(p.get("halt_new_entries")) for p in equity_portfolios),
        "hard_derisk": any(bool(p.get("hard_derisk")) for p in equity_portfolios),
        "cooldown_until_ms": max((int(p.get("cooldown_until_ms") or 0) for p in equity_portfolios), default=0) or None,
        "cooling_down": any(bool(p.get("cooling_down")) for p in equity_portfolios),
        "delta_totals_by_currency": _sum_decimal_dict(equity_portfolios, "delta_totals_by_currency"),
        "regime_by_currency": regime_by_currency,
        "halt_entry_reasons": halt_reasons,
        "regime_detail_by_currency": dict(regime_detail_by_currency),
        "margin_ratios_by_currency": margin_ratios,
        "equity_by_book": equity_by_book,
        "day_start_equity_by_book": day_start_by_book,
        "day_net_flow_usdc_by_book": day_net_flow_by_book,
        "day_pnl_usdc_ex_flow_by_book": day_pnl_by_book,
        "day_pnl_usdc_ex_flow_ex_spot_by_book": day_pnl_ex_spot_by_book,
        "day_drawdown_pct_by_book": dict(drawdown_by_book),
        "cooldown_until_ms_by_book": {
            book: max(
                (int((p.get("cooldown_until_ms_by_book") or {}).get(book) or 0) for p in equity_portfolios),
                default=0,
            )
            or None
            for book in sorted(equity_by_book)
        },
        "cooling_down_by_book": _bool_by_book("cooling_down_by_book"),
        "hard_derisk_by_book": _bool_by_book("hard_derisk_by_book"),
        "halt_entries_by_book": _bool_by_book("halt_entries_by_book"),
        "halt_entry_reasons_by_book": dict(halt_reasons_by_book),
    }


def _aggregate_status(
    accounts: list[DashboardAccount],
    *,
    exchange_prefetch_cache: _TtlCache,
) -> dict[str, Any]:
    import deribit_demo.frontend_server as pkg

    try:
        prefetches = pkg._prefetch_all_accounts(accounts, cache=exchange_prefetch_cache)
    except Exception as exc:  # noqa: BLE001 — local state status should still load.
        LOGGER.warning("status exchange prefetch failed; continuing without live marks: %s", exc)
        prefetches = {}
        for account in accounts:
            if not _has_private_creds(account.config):
                continue
            key = _live_api_identity(account)
            if key not in prefetches:
                prefetches[key] = None
    work = [account for account in accounts if _has_private_creds(account.config)]

    def _fetch(account: DashboardAccount) -> tuple[DashboardAccount, dict[str, Any]]:
        return account, _status_payload_for_account(
            account,
            exchange_prefetch_cache=exchange_prefetch_cache,
            prefetches=prefetches,
        )

    if len(work) <= 1:
        pairs = [_fetch(account) for account in work]
    else:
        with ThreadPoolExecutor(max_workers=min(len(work), 4)) as pool:
            pairs = list(pool.map(_fetch, work))

    payload_by_name = {account.name: payload for account, payload in pairs}
    statuses: list[dict[str, Any]] = []
    trade_groups: list[dict[str, Any]] = []
    open_orders: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    account_summaries: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    underlying_index_usd: dict[str, str] = {}
    seen_balance_identity: set[str] = set()

    for account in accounts:
        payload = payload_by_name.get(account.name)
        if payload is None:
            continue
        statuses.append(payload)
        for key, value in (payload.get("underlying_index_usd") or {}).items():
            if _dec(value) > 0:
                underlying_index_usd[str(key).upper()] = str(value)
        trade_groups.extend(_tag_rows(payload.get("trade_groups") or [], account))
        open_orders.extend(_tag_rows(payload.get("open_orders") or [], account))
        positions.extend(_tag_rows(payload.get("positions") or [], account))
        balance_key = _live_api_identity(account)
        if balance_key not in seen_balance_identity:
            seen_balance_identity.add(balance_key)
            for book, row in (payload.get("accounts") or {}).items():
                book_key = str(book).upper()
                for field, value in row.items():
                    account_summaries[book_key][field] += _dec(value)

    active_accounts = [account for account in accounts if account.name in payload_by_name]
    equity_statuses = _dedupe_statuses_for_equity_aggregate(active_accounts, statuses)
    return {
        "env": "multi" if len(accounts) > 1 else accounts[0].config.env,
        "portfolio": _aggregate_portfolios(active_accounts, statuses, equity_statuses=equity_statuses),
        "underlying_index_usd": underlying_index_usd,
        "accounts": {book: dict(values) for book, values in sorted(account_summaries.items())},
        "trade_group_count": len(trade_groups),
        "trade_groups": trade_groups,
        "open_orders": open_orders,
        "positions": positions,
        "dashboard_accounts": [
            {
                "name": account.name,
                "env": account.config.env,
                "option_strategy": account.config.option_strategy,
                "state_file": str(account.state_path),
            }
            for account in accounts
        ],
        "account_statuses": [
            {
                "name": account.name,
                "env": payload.get("env"),
                "option_strategy": account.config.option_strategy,
                "portfolio": payload.get("portfolio"),
                "accounts": payload.get("accounts") or {},
                "trade_group_count": payload.get("trade_group_count"),
            }
            for account in accounts
            if (payload := payload_by_name.get(account.name)) is not None
        ],
    }


def _aggregate_realized_summary(
    accounts: list[DashboardAccount],
    *,
    days: int = 30,
    spot_index: dict[str, Decimal] | None = None,
    status_payload: dict[str, Any] | None = None,
    effective_capital_override: Decimal | None = None,
) -> dict[str, Any]:
    """Lightweight report summary from on-disk closed groups (no per-account bot.report())."""
    closed: list[dict[str, Any]] = []
    excluded = 0
    open_count = 0
    for account in accounts:
        payload = _closed_groups_payload(account.state_path, spot_index=spot_index)
        closed.extend(_tag_rows(payload.get("closed") or [], account))
        excluded += int(payload.get("performance_excluded_closed_group_count") or 0)
        open_count += len(payload.get("open") or [])
    closed = _dedupe_trade_group_rows(closed)
    reference_capital = sum((account.config.reference_capital_usdc for account in accounts), Decimal("0"))
    equity_by_day = _daily_equity_by_utc_day(accounts)
    fallback_capital = _resolve_apr_effective_capital_usdc(
        accounts,
        override=effective_capital_override,
        status_payload=status_payload,
    )
    today = datetime.now(tz=UTC).date()
    capital = _equity_on_day(equity_by_day, today, fallback_capital)
    target_num = sum(
        (account.config.target_portfolio_apr * account.config.reference_capital_usdc for account in accounts),
        Decimal("0"),
    )
    target_apr = _ratio(target_num, reference_capital)
    summary = realized_summary_from_closed(
        closed,
        effective_capital_usdc=capital,
        target_portfolio_apr=target_apr,
        window_days=days,
    )
    summary["open_group_count"] = str(open_count)
    summary["performance_excluded_closed_group_count"] = str(excluded)
    recent_closed = [row for row in closed if row.get("realized_pnl") is not None]
    recent_closed.sort(key=lambda row: int(row.get("closed_timestamp_ms") or 0), reverse=True)
    return {
        "action": "report",
        "generated_at": utc_now(),
        "note": "Summary from local state; trade journal stores API fills incrementally.",
        "summary": summary,
        "recent_closed_trades": recent_closed[:20],
        "open_trades": [],
    }


def _aggregate_report(accounts: list[DashboardAccount], *, days: int) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    recent_closed: list[dict[str, Any]] = []
    open_trades: list[dict[str, Any]] = []
    for account in accounts:
        payload = _bot_for_account(account, require_private=True).report(days=days)
        reports.append(payload)
        recent_closed.extend(_tag_rows(payload.get("recent_closed_trades") or [], account))
        open_trades.extend(_tag_rows(payload.get("open_trades") or [], account))

    summaries = [report.get("summary") or {} for report in reports]
    effective_capital = _sum_decimal_field(summaries, "effective_capital_usdc")
    realized_count = sum(int(summary.get("realized_closed_group_count") or 0) for summary in summaries)
    closed_count = sum(int(summary.get("closed_group_count") or 0) for summary in summaries)
    excluded_count = sum(int(summary.get("performance_excluded_closed_group_count") or 0) for summary in summaries)
    unresolved_count = sum(int(summary.get("unresolved_closed_group_count") or 0) for summary in summaries)
    total_realized = _sum_decimal_field(summaries, "realized_pnl_usdc")
    window_realized = _sum_decimal_field(summaries, "window_realized_pnl_usdc")
    total_holding_days = sum(
        (
            _dec(summary.get("avg_holding_days")) * Decimal(str(int(summary.get("realized_closed_group_count") or 0)))
            for summary in summaries
        ),
        Decimal("0"),
    )
    wins = sum(1 for row in recent_closed if _dec(row.get("realized_pnl")) > 0)
    lifetime_days = max((_dec(summary.get("lifetime_sample_days")) for summary in summaries), default=Decimal("0"))
    window_days_used = max((_dec(summary.get("window_days_used")) for summary in summaries), default=Decimal(str(days)))
    target_num = sum(
        (
            _dec(summary.get("target_portfolio_apr")) * _dec(summary.get("effective_capital_usdc"))
            for summary in summaries
        ),
        Decimal("0"),
    )

    recent_closed = _dedupe_trade_group_rows(recent_closed)
    recent_closed.sort(key=lambda row: int(row.get("closed_timestamp_ms") or 0), reverse=True)
    return {
        "action": "report",
        "generated_at": utc_now(),
        "note": "Aggregated multi-account realized report. Perpetual hedge PnL is not included.",
        "summary": {
            "effective_capital_usdc": effective_capital,
            "target_portfolio_apr": _ratio(target_num, effective_capital),
            "open_group_count": sum(int(summary.get("open_group_count") or 0) for summary in summaries),
            "closed_group_count": closed_count,
            "performance_excluded_closed_group_count": excluded_count,
            "realized_closed_group_count": realized_count,
            "unresolved_closed_group_count": unresolved_count,
            "open_max_loss_usdc": _sum_decimal_field(summaries, "open_max_loss_usdc"),
            "realized_pnl_usdc": total_realized,
            "avg_realized_pnl_usdc": _ratio(total_realized, Decimal(str(realized_count))),
            "realized_win_rate": _ratio(Decimal(str(wins)), Decimal(str(realized_count))),
            "avg_holding_days": _ratio(total_holding_days, Decimal(str(realized_count))),
            "lifetime_sample_days": lifetime_days,
            "lifetime_realized_apr": _ratio(total_realized * Decimal("365"), lifetime_days * effective_capital),
            "window_days_requested": days,
            "window_days_used": window_days_used,
            "window_realized_closed_group_count": sum(
                int(summary.get("window_realized_closed_group_count") or 0) for summary in summaries
            ),
            "window_realized_pnl_usdc": window_realized,
            "window_realized_apr": _ratio(window_realized * Decimal("365"), window_days_used * effective_capital),
        },
        "recent_closed_trades": recent_closed[:20],
        "open_trades": open_trades,
        "accounts": [
            {
                "name": account.name,
                "option_strategy": account.config.option_strategy,
                "summary": report.get("summary") or {},
            }
            for account, report in zip(accounts, reports, strict=False)
        ],
    }
