from __future__ import annotations

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from .client import DeribitClient
from .config import BotConfig
from .exceptions import AuthenticationError, ExchangeError
from .fees import option_trade_fee_usdc, premium_value_usdc
from .margin import (
    linear_usdc_short_call_initial_per_contract_usdc,
    linear_usdc_short_put_initial_per_contract_usdc,
    short_call_initial_unit,
    short_put_initial_unit,
)
from .models import (
    EXTERNAL_FLOW_TRANSACTION_TYPES,
    AccountSummary,
    HedgePlan,
    NakedPutCandidate,
    OpenOrder,
    OptionInstrument,
    OrderBookSnapshot,
    PortfolioSnapshot,
    Position,
    RiskRegime,
    SpreadLeg,
    TradeGroup,
    StrategyState,
    TransactionEntry,
    normalize_strategy_name,
)
from .state import StrategyStateStore, load_performance_exclusion_group_ids
from .strategy import StrategySelector
from .utils import (
    align_option_order_amount,
    format_decimal,
    ms_to_datetime,
    parse_option_name,
    safe_div,
    to_decimal,
    utc_now,
    utc_now_ms,
)

LOGGER = logging.getLogger(__name__)
LOG_REASON_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
# When logging scan blockers, avoid megabytes of text per cycle.
_MAX_SCAN_BLOCKER_LOG_LINES = 36
# Append at most this many `example_messages` from scan rejection detail per side.
_MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES = 10


@dataclass
class RuntimeContext:
    state: StrategyState
    summaries: dict[str, AccountSummary]
    open_orders: list[OpenOrder]
    positions: list[Position]
    option_positions: list[Position]
    future_positions: list[Position]
    future_markets_by_name: dict[str, OptionInstrument]
    markets_by_currency: dict[str, list[OptionInstrument]]
    orderbook_cache: dict[str, OrderBookSnapshot]
    regime_by_currency: dict[str, RiskRegime]
    snapshot: PortfolioSnapshot


class DeribitOptionTrialBot:
    def __init__(
        self,
        config: BotConfig,
        client: DeribitClient,
        *,
        state_store: StrategyStateStore | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.config = config
        self.client = client
        self.strategy = StrategySelector(config)
        self.state_store = state_store or StrategyStateStore(config.state_file)
        self.sleep_fn = sleep_fn or time.sleep
        # Cache per-currency regime decisions keyed by currency → (regime, ts_ms).
        # Used when index/DVOL feeds are temporarily unavailable so we fall back
        # to the last known regime rather than mis-classifying the market as crisis.
        self._last_regime_cache: dict[str, tuple[RiskRegime, int]] = {}
        self._instrument_metadata_cache: dict[str, OptionInstrument] = {}

    def _linear_usdc_mode(self) -> bool:
        return self.config.option_markets_profile == "linear_usdc"

    def _group_im_in_collateral(
        self,
        group: TradeGroup,
        *,
        orderbook_cache: dict[str, OrderBookSnapshot] | None,
    ) -> Decimal:
        """Return a group's initial margin expressed in **its own collateral
        currency's native unit** (BTC / ETH / USDC).

        Post-fix groups carry this value on ``estimated_im_collateral``. Legacy
        state files only stored ``max_loss`` (always USDC-scale), so we fall
        back to a best-effort conversion: USDC-collateral groups can use
        ``max_loss`` directly; coin-collateral groups divide by the current
        index price for the underlying so the figure is on the same scale as
        the scanner's ``summary_equity`` (BTC or ETH).
        """
        if group.estimated_im_collateral > 0:
            return group.estimated_im_collateral
        collateral = self._group_collateral_currency(group).upper()
        if collateral == "USDC":
            return group.max_loss
        if orderbook_cache is None:
            return Decimal("0")
        idx = self._currency_index_price(group.currency, orderbook_cache)
        if idx <= 0:
            return Decimal("0")
        return group.max_loss / idx

    def _realized_return_on_im_collateral_native(
        self,
        group: TradeGroup,
        realized_pnl_usdc: Decimal,
        *,
        index_price_usd: Decimal,
        orderbook_cache: dict[str, OrderBookSnapshot] | None = None,
    ) -> Decimal:
        """Realized PnL ÷ position initial margin, in comparable (collateral) units.

        ``realized_pnl_usdc`` follows existing ledger semantics (USDC equivalent).
        USDC-collateral: divide USDC PnL by USDC IM. Coin-collateral: convert PnL
        to coin via ``index_price_usd`` then divide by IM in coin.
        """
        im = self._group_im_in_collateral(group, orderbook_cache=orderbook_cache)
        if im <= 0:
            return Decimal("0")
        if self._group_collateral_currency(group).upper() == "USDC":
            return safe_div(realized_pnl_usdc, im)
        if index_price_usd <= 0:
            return Decimal("0")
        pnl_native = realized_pnl_usdc / index_price_usd
        return safe_div(pnl_native, im)

    def _realized_annualized_return_on_im_native(
        self,
        group: TradeGroup,
        realized_pnl_usdc: Decimal,
        *,
        index_price_usd: Decimal,
        closed_timestamp_ms: int,
        orderbook_cache: dict[str, OrderBookSnapshot] | None = None,
    ) -> Decimal:
        holding_days = Decimal(str(max(closed_timestamp_ms - group.entry_timestamp_ms, 0))) / Decimal("86400000")
        if holding_days <= 0:
            return Decimal("0")
        r = self._realized_return_on_im_collateral_native(
            group,
            realized_pnl_usdc,
            index_price_usd=index_price_usd,
            orderbook_cache=orderbook_cache,
        )
        return r * (Decimal("365") / holding_days)

    def _naked_im_by_expiry(
        self,
        state: StrategyState,
        collateral_currency: str,
        *,
        orderbook_cache: dict[str, OrderBookSnapshot] | None = None,
    ) -> dict[int, Decimal]:
        """Aggregate open-group IM per expiration, in the collateral
        currency's unit (BTC / ETH / USDC).

        ``collateral_currency`` picks the book — "BTC" returns totals for the
        BTC-settled inverse book, "ETH" for the ETH-settled inverse book, and
        "USDC" for the linear-USDC book (which may mix BTC_USDC and ETH_USDC
        underlyings under the same per-expiry cap). Callers pass the collateral
        of the candidate they are sizing so ``existing_im_for_expiry`` lives
        in the same unit as ``summary_equity * expiry_im_cap``.
        """
        target = (collateral_currency or "").upper()
        totals: dict[int, Decimal] = {}
        for group in self._open_groups(state):
            if self._group_collateral_currency(group).upper() != target:
                continue
            exp = group.expiration_timestamp_ms
            totals[exp] = totals.get(exp, Decimal("0")) + self._group_im_in_collateral(
                group, orderbook_cache=orderbook_cache
            )
        return totals

    def _effective_capital(self, total_equity_usdc: Decimal) -> Decimal:
        if total_equity_usdc > 0:
            return total_equity_usdc
        return self.config.reference_capital_usdc

    def ping(self) -> dict[str, Any]:
        payload = self.client.ping()
        return {"env": self.config.env, "ok": True, "result": payload}

    def status(self) -> dict[str, Any]:
        context = self._load_runtime()
        self.state_store.save(context.state)
        return self._status_payload(context)

    def report(self, *, days: int = 30) -> dict[str, Any]:
        state = self.state_store.load()
        open_groups = self._open_groups(state)
        excluded_group_ids = load_performance_exclusion_group_ids(self.state_store.path)
        all_closed_groups = [group for group in state.groups if group.status == "closed"]
        closed_groups = [
            group for group in all_closed_groups if group.group_id not in excluded_group_ids
        ]
        realized_groups = [
            group
            for group in closed_groups
            if group.realized_pnl is not None and group.closed_timestamp_ms is not None
        ]
        unresolved_closed_groups = [group for group in closed_groups if group not in realized_groups]
        total_realized_pnl = sum((group.realized_pnl or Decimal("0") for group in realized_groups), Decimal("0"))
        total_holding_days = sum((group.holding_days for group in realized_groups), Decimal("0"))
        win_count = len([group for group in realized_groups if (group.realized_pnl or Decimal("0")) > 0])
        realized_count = Decimal(str(len(realized_groups)))
        lifetime_sample_days = self._realized_sample_days(realized_groups)
        window_groups, window_days = self._window_realized_groups(realized_groups, days)
        window_realized_pnl = sum((group.realized_pnl or Decimal("0") for group in window_groups), Decimal("0"))

        # One Deribit-heavy pass when we need live marks for open groups (avoid a second
        # ``_load_runtime`` inside ``_open_trades_for_report``).
        if self.config.has_private_credentials and open_groups:
            runtime = self._load_runtime()
            summaries = runtime.summaries
            open_trades = self._trade_groups_payload(
                open_groups, runtime.option_positions, runtime.orderbook_cache
            )
        elif self.config.has_private_credentials:
            summaries = self._account_summaries_by_currency()
            open_trades = self._trade_groups_payload(open_groups, None, None)
        else:
            summaries = {}
            open_trades = self._trade_groups_payload(open_groups, None, None)

        report_equity = self._total_equity_usdc(summaries, {})
        effective_capital = self._effective_capital(report_equity)

        return {
            "action": "report",
            "generated_at": utc_now(),
            "note": "Naked short option realized report. Perpetual hedge PnL is not included.",
            "summary": {
                "effective_capital_usdc": effective_capital,
                "target_portfolio_apr": self.config.target_portfolio_apr,
                "open_group_count": len(open_groups),
                "closed_group_count": len(closed_groups),
                "performance_excluded_closed_group_count": len(all_closed_groups) - len(closed_groups),
                "realized_closed_group_count": len(realized_groups),
                "unresolved_closed_group_count": len(unresolved_closed_groups),
                "open_max_loss_usdc": self._open_max_loss(state),
                "realized_pnl_usdc": total_realized_pnl,
                "avg_realized_pnl_usdc": safe_div(total_realized_pnl, realized_count),
                "realized_win_rate": safe_div(Decimal(str(win_count)), realized_count),
                "avg_holding_days": safe_div(total_holding_days, realized_count),
                "lifetime_sample_days": lifetime_sample_days,
                "lifetime_realized_apr": self._annualize_apr(
                    total_realized_pnl,
                    lifetime_sample_days,
                    effective_capital,
                ),
                "window_days_requested": days,
                "window_days_used": window_days,
                "window_realized_closed_group_count": len(window_groups),
                "window_realized_pnl_usdc": window_realized_pnl,
                "window_realized_apr": self._annualize_apr(
                    window_realized_pnl,
                    window_days,
                    effective_capital,
                ),
            },
            "recent_closed_trades": [
                self._report_group_payload(group)
                for group in sorted(closed_groups, key=lambda item: item.closed_timestamp_ms or 0, reverse=True)[:20]
            ],
            "open_trades": open_trades,
        }

    def scan(
        self,
        *,
        currencies: tuple[str, ...] | None = None,
        top_n: int | None = None,
    ) -> dict[str, Any]:
        context = self._load_runtime()
        candidates = self._scan_candidates(context, currencies=currencies, top_n=top_n)
        self.state_store.save(context.state)
        return self._scan_payload(context, candidates, scan_currencies=currencies)

    def enter_best(
        self,
        *,
        currencies: tuple[str, ...] | None = None,
        live: bool = False,
    ) -> dict[str, Any]:
        context = self._load_runtime()
        candidates = self._scan_candidates(context, currencies=currencies, top_n=1)
        result = self._enter_best_from_candidates(context, candidates=candidates, live=live)
        if result.get("group") is not None:
            context.state.groups.append(result["group"])
        self.state_store.save(context.state)
        return result

    def manage(self, *, live: bool = False) -> dict[str, Any]:
        context = self._load_runtime()
        actions: list[dict[str, Any]] = []

        actions.extend(self._pending_covered_call_spot_exit_actions(context, live=live))

        if context.snapshot.hard_derisk:
            cooldown_until = utc_now_ms() + (self.config.cooldown_hours * 3600 * 1000)
            # Route the cooldown to the specific book(s) that triggered the hard
            # derisk so the other books keep trading. Fall back to a portfolio-
            # wide cooldown for global triggers (crisis regime on an open group,
            # hard-defense delta / stop-loss hits) that aren't book-scoped.
            hard_books = [
                book for book, flag in context.snapshot.hard_derisk_by_book.items() if flag
            ]
            global_trigger = context.snapshot.hard_derisk and not hard_books
            if live:
                for book in hard_books:
                    context.state.cooldown_until_ms_by_book[book] = cooldown_until
                if global_trigger:
                    context.state.cooldown_until_ms = cooldown_until
            actions.append(
                {
                    "action": "cooldown_started" if live else "cooldown_recommended",
                    "cooldown_until_ms": cooldown_until,
                    "reason": "hard_derisk",
                    "books": hard_books or ["portfolio"],
                }
            )

        for group in sorted(self._open_groups(context.state), key=lambda item: item.max_loss, reverse=True):
            group_actions = self._manage_group(context, group, live=live)
            actions.extend(group_actions)

        if self.config.enable_perp_hedge and context.snapshot.regime is RiskRegime.NORMAL:
            for currency in self.config.managed_currencies:
                unwind = self._maybe_unwind_hedge(context, currency=currency, live=live)
                if unwind is not None:
                    actions.append(unwind)

        self.state_store.save(context.state)
        return {
            "action": "manage",
            "live": live,
            "portfolio": context.snapshot.to_dict(),
            "trade_groups": self._trade_groups_payload(
                self._open_groups(context.state),
                context.option_positions,
                context.orderbook_cache,
            ),
            "actions": actions,
        }

    def run(
        self,
        *,
        live: bool = False,
        cycles: int = 1,
        currencies: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        iteration = 0
        cycle_results: list[dict[str, Any]] = []
        retain_results = cycles > 0
        last_log_signature: tuple[Any, ...] | None = None
        while cycles <= 0 or iteration < cycles:
            cycle_no = iteration + 1
            manage_result = self.manage(live=live)
            cycle_result: dict[str, Any] = {"manage": manage_result}

            context = self._load_runtime()
            status_after_manage = self._status_payload(context)
            cycle_result["status"] = status_after_manage
            candidates = self._scan_candidates(context, currencies=currencies, top_n=self.config.top_n)
            cycle_result["scan"] = self._scan_payload(context, candidates, scan_currencies=currencies)
            portfolio = status_after_manage["portfolio"]
            can_enter = not portfolio["halt_new_entries"]
            if can_enter:
                cycle_result["entry"] = self._enter_best_from_candidates(context, candidates=candidates[:1], live=live)
                if cycle_result["entry"].get("group") is not None:
                    context.state.groups.append(cycle_result["entry"]["group"])
            else:
                reason = (
                    "hard_derisk" if portfolio["hard_derisk"]
                    else "cooling_down" if portfolio["cooling_down"]
                    else "halt_limit_reached"
                )
                cycle_result["entry"] = {
                    "action": "entry_skipped",
                    "reason": reason,
                }

            entry_action = cycle_result["entry"].get("action", "")
            risk_blocked = portfolio["hard_derisk"] or portfolio["cooling_down"]
            if not candidates and not risk_blocked and entry_action != "naked_put_entered":
                topup_actions = self._topup_existing_naked_groups(context, live=live)
                if topup_actions:
                    cycle_result["topup"] = topup_actions
            self.state_store.save(context.state)
            log_signature = self._cycle_log_signature(cycle_result)
            if log_signature != last_log_signature:
                self._log_cycle_update(cycle_no, cycle_result, live=live)
                last_log_signature = log_signature
            if retain_results:
                cycle_results.append(cycle_result)
            iteration += 1
            if cycles > 0 and iteration >= cycles:
                break
            sleep_seconds = self.config.poll_seconds_stress if portfolio["regime"] != RiskRegime.NORMAL.value else self.config.poll_seconds_normal
            self.sleep_fn(sleep_seconds)
        return {"action": "run", "cycles": iteration, "results": cycle_results}

    def close_positions(
        self,
        *,
        instruments: list[str] | None = None,
        list_only: bool = False,
        live: bool = False,
        order_type: str = "market",
        amount: Decimal | None = None,
    ) -> dict[str, Any]:
        """Close specific exchange positions (options or perps), without portfolio-wide panic logic."""
        normalized_order_type = str(order_type or "market").strip().lower()
        if normalized_order_type not in {"market", "limit"}:
            raise ExchangeError(f"close-position: unsupported order_type {order_type!r}")

        if not self.config.has_private_credentials:
            raise AuthenticationError("close-position requires private API credentials")

        positions = [
            Position.from_api(row) for row in self.client.get_positions(currency="any", kind="any")
        ]
        open_positions = [item for item in positions if abs(item.size) > 0]

        if list_only:
            return {
                "action": "close-position",
                "live": live,
                "list_only": True,
                "positions": [self._position_close_row(item) for item in open_positions],
                "targets": [],
                "skipped": [],
            }

        requested = [str(name).strip() for name in (instruments or []) if str(name).strip()]
        if not requested:
            raise ExchangeError("close-position: pass --instrument NAME or use --list")

        by_name = {item.instrument_name: item for item in open_positions}
        targets: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        context = self._load_runtime() if live else None

        for instrument_name in requested:
            position = by_name.get(instrument_name)
            if position is None:
                skipped.append({"instrument_name": instrument_name, "reason": "no_open_position"})
                continue

            close_qty = abs(position.size)
            if amount is not None and amount > 0:
                close_qty = min(close_qty, amount)
            close_side = "buy" if position.direction == "sell" else "sell"
            is_option = position.kind == "option"
            is_perp = position.kind in {"future", "future_combo"} or "PERPETUAL" in position.instrument_name

            if not live:
                targets.append(
                    self._close_position_preview(
                        position,
                        close_qty=close_qty,
                        close_side=close_side,
                        order_type=normalized_order_type,
                        is_option=is_option,
                        is_perp=is_perp,
                    )
                )
                continue

            assert context is not None
            label = f"{self.config.order_label_prefix}-manual-close"
            if is_option:
                targets.append(
                    self._close_option_position_live(
                        context,
                        position=position,
                        close_qty=close_qty,
                        close_side=close_side,
                        label=label,
                        order_type=normalized_order_type,
                    )
                )
            elif is_perp:
                action = self._close_perp_position(position, live=True)
                if action is None:
                    skipped.append({"instrument_name": instrument_name, "reason": "zero_size"})
                else:
                    targets.append({**action, "status": "submitted"})
            else:
                response = self.client.close_position(
                    instrument_name,
                    order_type=normalized_order_type,
                )
                targets.append(
                    {
                        "instrument_name": instrument_name,
                        "kind": position.kind,
                        "close_side": close_side,
                        "amount": format_decimal(close_qty, 8),
                        "order_type": normalized_order_type,
                        "status": "submitted",
                        "method": "close_position",
                        "response": response,
                    }
                )

        return {
            "action": "close-position",
            "live": live,
            "list_only": False,
            "order_type": normalized_order_type,
            "positions": [self._position_close_row(item) for item in open_positions],
            "targets": targets,
            "skipped": skipped,
        }

    def _position_close_row(self, position: Position) -> dict[str, Any]:
        return {
            "instrument_name": position.instrument_name,
            "kind": position.kind,
            "direction": position.direction,
            "size": format_decimal(abs(position.size), 8),
            "mark_price": format_decimal(position.mark_price, 8),
            "floating_profit_loss": format_decimal(position.floating_profit_loss, 8),
        }

    def _close_position_preview(
        self,
        position: Position,
        *,
        close_qty: Decimal,
        close_side: str,
        order_type: str,
        is_option: bool,
        is_perp: bool,
    ) -> dict[str, Any]:
        if is_option:
            method = "reduce_only_limit_ioc" if order_type == "limit" else "reduce_only_market"
        elif is_perp:
            method = "close_position"
        else:
            method = "close_position"
        return {
            "instrument_name": position.instrument_name,
            "kind": position.kind,
            "direction": position.direction,
            "size": format_decimal(abs(position.size), 8),
            "close_side": close_side,
            "amount": format_decimal(close_qty, 8),
            "order_type": order_type,
            "status": "preview",
            "method": method,
        }

    def _close_option_position_live(
        self,
        context: RuntimeContext,
        *,
        position: Position,
        close_qty: Decimal,
        close_side: str,
        label: str,
        order_type: str,
    ) -> dict[str, Any]:
        instrument_name = position.instrument_name
        if order_type == "limit":
            instrument = self._find_instrument(context, instrument_name)
            book = self._get_orderbook(instrument_name, context.orderbook_cache)
            initial_price = (
                self.strategy.close_buy_price(instrument, book)
                if close_side == "buy"
                else self.strategy.close_sell_price(instrument, book)
            )
            result = self._close_leg_with_retry(
                context,
                instrument_name=instrument_name,
                quantity=close_qty,
                direction=close_side,
                label=label,
                initial_price=initial_price,
            )
            if result["unfilled"] <= 0:
                status = "filled"
            elif result["filled"] > 0:
                status = "partial"
            else:
                status = "failed"
            return {
                "instrument_name": instrument_name,
                "kind": position.kind,
                "close_side": close_side,
                "amount": format_decimal(close_qty, 8),
                "order_type": order_type,
                "status": status,
                "method": "reduce_only_limit_ioc",
                "filled": format_decimal(result["filled"], 8),
                "unfilled": format_decimal(result["unfilled"], 8),
                "responses": result["responses"],
            }

        instrument = self._find_instrument(context, instrument_name)
        requested = align_option_order_amount(close_qty, instrument.contract_size, instrument.min_trade_amount)
        capacity = self._option_reduce_only_capacity(
            instrument_name,
            close_side,
            option_positions=context.option_positions,
        )
        order_amount = align_option_order_amount(
            min(requested, capacity),
            instrument.contract_size,
            instrument.min_trade_amount,
        )
        if order_amount <= 0:
            return {
                "instrument_name": instrument_name,
                "kind": position.kind,
                "close_side": close_side,
                "amount": format_decimal(close_qty, 8),
                "order_type": order_type,
                "status": "failed",
                "method": "reduce_only_market",
                "reason": "zero_reduce_only_capacity",
            }
        place_fn = self.client.place_buy_order if close_side == "buy" else self.client.place_sell_order
        response = place_fn(
            instrument_name=instrument_name,
            amount=order_amount,
            label=label,
            order_type="market",
            reduce_only=True,
        )
        filled = self._response_filled_amount(response)
        if filled >= order_amount:
            status = "filled"
        elif filled > 0:
            status = "partial"
        else:
            status = "failed"
        return {
            "instrument_name": instrument_name,
            "kind": position.kind,
            "close_side": close_side,
            "amount": format_decimal(order_amount, 8),
            "order_type": order_type,
            "status": status,
            "method": "reduce_only_market",
            "response": response,
        }

    def panic_close(self, *, live: bool = False) -> dict[str, Any]:
        context = self._load_runtime()
        actions: list[dict[str, Any]] = []
        for order in context.open_orders:
            if live:
                response = self.client.cancel_order(order.order_id)
                actions.append({"action": "cancel_order", "order_id": order.order_id, "response": response})
            else:
                actions.append({"action": "cancel_order_preview", "order_id": order.order_id})

        for group in list(self._open_groups(context.state)):
            actions.extend(self._close_group(context, group, reason="panic_close", live=live))

        for position in context.future_positions:
            if "PERPETUAL" not in position.instrument_name:
                continue
            preview = self._close_perp_position(position, live=live)
            if preview is not None:
                actions.append(preview)

        if live:
            context.state.groups = [group for group in context.state.groups if group.status == "closed"]
            cooldown_until = utc_now_ms() + (self.config.cooldown_hours * 3600 * 1000)
            context.state.cooldown_until_ms = cooldown_until
            # Panic-close is a portfolio-wide halt: stamp every enabled book's
            # cooldown so post-panic re-entries are blocked evenly across books.
            for book in context.snapshot.equity_by_book:
                context.state.cooldown_until_ms_by_book[book] = cooldown_until
        self.state_store.save(context.state)
        return {"action": "panic_close", "live": live, "actions": actions}

    def cancel(self, order_id: str) -> dict[str, Any]:
        response = self.client.cancel_order(order_id)
        return {"action": "cancelled", "order_id": order_id, "response": response}

    def _load_runtime(self) -> RuntimeContext:
        state = self.state_store.load()
        summaries = self._account_summaries_by_currency()
        open_orders = [OpenOrder.from_api(row) for row in self.client.get_open_orders(kind="any")] if self.config.has_private_credentials else []
        positions = [Position.from_api(row) for row in self.client.get_positions(currency="any", kind="any")] if self.config.has_private_credentials else []
        option_positions = [item for item in positions if item.kind == "option"]
        future_positions = [item for item in positions if item.kind in {"future", "future_combo"} or "PERPETUAL" in item.instrument_name]
        future_markets_by_name = self._load_perpetual_markets()
        markets_by_currency = self._load_supported_option_markets()
        orderbook_cache: dict[str, OrderBookSnapshot] = {}
        state = self._reset_daily_state(state, summaries)
        # Refresh external cash-flow (deposit / withdrawal / transfer) tallies
        # from Deribit's transaction log so drawdown is measured against
        # trading P&L only, not user-initiated balance changes.
        self._refresh_cash_flows_by_book(state, orderbook_cache)
        state = self._reconcile_state(
            state,
            option_positions=option_positions,
            orderbook_cache=orderbook_cache,
            markets_by_currency=markets_by_currency,
        )
        regime_by_currency: dict[str, RiskRegime] = {}
        regime_detail_by_currency: dict[str, tuple[str, ...]] = {}
        for currency in self.config.managed_currencies:
            regime, detail = self._determine_regime_with_detail(
                currency,
                markets=markets_by_currency[currency],
                orderbook_cache=orderbook_cache,
            )
            regime_by_currency[currency] = regime
            regime_detail_by_currency[currency] = tuple(detail)
        self._update_recovery_counts(state, regime_by_currency)
        for group in self._open_groups(state):
            self._refresh_group(context_markets=markets_by_currency, group=group, orderbook_cache=orderbook_cache)
        if self._is_covered_call_strategy():
            self._clear_covered_call_book_cooldowns(state, summaries)
        snapshot = self._build_portfolio_snapshot(
            state=state,
            summaries=summaries,
            regime_by_currency=regime_by_currency,
            regime_detail_by_currency=regime_detail_by_currency,
            future_positions=future_positions,
            orderbook_cache=orderbook_cache,
        )
        return RuntimeContext(
            state=state,
            summaries=summaries,
            open_orders=open_orders,
            positions=positions,
            option_positions=option_positions,
            future_positions=future_positions,
            future_markets_by_name=future_markets_by_name,
            markets_by_currency=markets_by_currency,
            orderbook_cache=orderbook_cache,
            regime_by_currency=regime_by_currency,
            snapshot=snapshot,
        )

    def _status_payload(self, context: RuntimeContext) -> dict[str, Any]:
        underlying_index_usd: dict[str, str] = {}
        for sym in ("BTC", "ETH"):
            idx = self._currency_index_price(sym, context.orderbook_cache)
            underlying_index_usd[sym] = format_decimal(idx, 4) if idx > 0 else "0"
        return {
            "env": self.config.env,
            "portfolio": context.snapshot.to_dict(),
            "underlying_index_usd": underlying_index_usd,
            "accounts": {
                currency: {
                    "balance": format_decimal(summary.balance, 8),
                    "equity": format_decimal(summary.equity, 8),
                    "available_funds": format_decimal(summary.available_funds, 8),
                    "initial_margin": format_decimal(summary.initial_margin, 8),
                    "maintenance_margin": format_decimal(summary.maintenance_margin, 8),
                    "delta_total": format_decimal(summary.delta_total, 8),
                }
                for currency, summary in sorted(context.summaries.items())
            },
            "trade_group_count": len(self._open_groups(context.state)),
            "trade_groups": self._trade_groups_payload(
                self._open_groups(context.state),
                context.option_positions,
                context.orderbook_cache,
            ),
            "open_orders": [self._order_payload(order) for order in context.open_orders],
            "positions": [self._position_payload(position) for position in context.positions],
        }

    def _scan_payload(
        self,
        context: RuntimeContext,
        candidates: list[NakedPutCandidate],
        *,
        scan_currencies: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        selected = scan_currencies or self.config.managed_currencies
        option_counts = Counter((c.option_type or "put") for c in candidates)
        is_covered_call = self.config.option_strategy == "covered_call"
        note_zh = None
        if (
            not is_covered_call
            and self.config.enable_short_call
            and self.config.short_call_fallback_only
        ):
            note_zh = (
                "short_call_fallback_only=true：本輪只要有 put 通過 min_net_apr，就不會掃描 short call；"
                "要看 call 請設 SHORT_OPTION_SIDE=both 或 SHORT_CALL_FALLBACK_ONLY=false。"
            )
        return {
            "env": self.config.env,
            "candidate_count": len(candidates),
            "regime": context.snapshot.regime.value,
            "portfolio": context.snapshot.to_dict(),
            "candidates": [c.to_dict() for c in candidates],
            "strategy_mode": self.config.option_strategy,
            "candidate_option_type_counts": dict(option_counts),
            "scan_policy": {
                "enable_short_put": self.config.enable_short_put,
                "enable_short_call": self.config.enable_short_call,
                "short_call_fallback_only": self.config.short_call_fallback_only,
                "put_and_call_compete_in_scan": self.config.naked_scan_put_and_call_compete,
                "note_zh": note_zh,
            },
            "entry_blockers": self._scan_entry_blockers(context, candidates, selected_currencies=selected),
            "scan_rejections": self._covered_call_scan_rejections_payload(context, tuple(selected))
            if is_covered_call
            else self._naked_scan_rejections_payload(context, tuple(selected)),
            "scan_rejections_short_call": None
            if is_covered_call
            else self._naked_scan_rejections_short_call_payload(context, tuple(selected)),
        }

    def _naked_scan_rejections_payload(
        self,
        context: RuntimeContext,
        selected_currencies: tuple[str, ...],
    ) -> dict[str, Any]:
        loader = lambda instrument_name: self._get_orderbook(instrument_name, context.orderbook_cache)
        out: dict[str, Any] = {}
        for currency in selected_currencies:
            markets_by_collateral: dict[str, list[OptionInstrument]] = {}
            for market in context.markets_by_currency.get(currency, []):
                coll = "USDC" if self._linear_usdc_mode() else (market.settlement_currency or currency)
                markets_by_collateral.setdefault(coll, []).append(market)
            for collateral_ccy, collateral_markets in sorted(markets_by_collateral.items()):
                collateral_summary = context.summaries.get(collateral_ccy)
                equity = collateral_summary.equity if collateral_summary is not None else Decimal("0")
                mm = collateral_summary.maintenance_margin if collateral_summary is not None else Decimal("0")
                im_by_exp = self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
                regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
                key = f"{currency}/{collateral_ccy}" if collateral_ccy != currency else currency
                out[key] = self.strategy.naked_put_scan_rejection_detail(
                    currency,
                    collateral_markets,
                    loader,
                    regime=regime,
                    summary_equity=equity,
                    summary_maintenance_margin=mm,
                    collateral_currency=collateral_ccy,
                    existing_im_by_expiry=im_by_exp,
                )
        return out

    def _naked_scan_rejections_short_call_payload(
        self,
        context: RuntimeContext,
        selected_currencies: tuple[str, ...],
    ) -> dict[str, Any] | None:
        """Per-book short-call rejection stats (mirrors ``scan_rejections`` for puts)."""
        if not self.config.enable_short_call:
            return None
        loader = lambda instrument_name: self._get_orderbook(instrument_name, context.orderbook_cache)
        out: dict[str, Any] = {}
        for currency in selected_currencies:
            markets_by_collateral: dict[str, list[OptionInstrument]] = {}
            for market in context.markets_by_currency.get(currency, []):
                coll = "USDC" if self._linear_usdc_mode() else (market.settlement_currency or currency)
                markets_by_collateral.setdefault(coll, []).append(market)
            for collateral_ccy, collateral_markets in sorted(markets_by_collateral.items()):
                collateral_summary = context.summaries.get(collateral_ccy)
                equity = collateral_summary.equity if collateral_summary is not None else Decimal("0")
                mm = collateral_summary.maintenance_margin if collateral_summary is not None else Decimal("0")
                im_by_exp = self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
                regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
                key = f"{currency}/{collateral_ccy}" if collateral_ccy != currency else currency
                out[key] = self.strategy.naked_call_scan_rejection_detail(
                    currency,
                    collateral_markets,
                    loader,
                    regime=regime,
                    summary_equity=equity,
                    summary_maintenance_margin=mm,
                    collateral_currency=collateral_ccy,
                    existing_im_by_expiry=im_by_exp,
                )
        return out

    def _covered_call_scan_rejections_payload(
        self,
        context: RuntimeContext,
        selected_currencies: tuple[str, ...],
    ) -> dict[str, Any]:
        loader = lambda instrument_name: self._get_orderbook(instrument_name, context.orderbook_cache)
        out: dict[str, Any] = {}
        for currency in selected_currencies:
            ccy = currency.upper()
            summary = context.summaries.get(ccy)
            equity = summary.equity if summary is not None else Decimal("0")
            regime = context.regime_by_currency.get(ccy, RiskRegime.CRISIS)
            available_cover = self._available_covered_call_quantity(context, ccy)
            collateral_markets = [
                market
                for market in context.markets_by_currency.get(ccy, [])
                if (market.settlement_currency or ccy).upper() == ccy
                and market.option_type == "call"
                and market.instrument_type != "linear"
            ]
            out[ccy] = self.strategy.covered_call_scan_rejection_detail(
                ccy,
                collateral_markets,
                loader,
                regime=regime,
                collateral_currency=ccy,
                available_cover_quantity=available_cover,
                summary_equity=equity,
            )
        return out

    def _scan_entry_blockers(
        self,
        context: RuntimeContext,
        candidates: list[NakedPutCandidate],
        *,
        selected_currencies: tuple[str, ...],
    ) -> list[str]:
        blockers: list[str] = []
        snap = context.snapshot
        if snap.halt_new_entries:
            blockers.extend(list(snap.halt_entry_reasons))
            return blockers
        active_strategies = self._active_scan_strategy_keys()
        if (
            self.config.max_concurrent_groups > 0
            and active_strategies
            and all(
                self._open_group_count_for_strategy(context.state, strategy) >= self.config.max_concurrent_groups
                for strategy in active_strategies
            )
        ):
            detail = ", ".join(
                f"{strategy} open_count={self._open_group_count_for_strategy(context.state, strategy)}"
                for strategy in active_strategies
            )
            blockers.append(
                f"max_concurrent_groups: all enabled strategies at limit "
                f"({detail}; limit={self.config.max_concurrent_groups})"
            )
            return blockers
        if candidates:
            return []
        if self.config.option_strategy == "covered_call":
            return self._covered_call_scan_entry_blockers(
                context,
                selected_currencies=selected_currencies,
            )
        threshold = self.config.min_net_apr
        orderbook_cache = context.orderbook_cache
        for currency in selected_currencies:
            if self.config.option_strategy == "bull_put_spread":
                open_for_currency = self._open_group_count_for_currency(
                    context.state,
                    currency,
                    strategy="bull_put_spread",
                )
                if self.config.max_groups_per_currency > 0 and open_for_currency >= self.config.max_groups_per_currency:
                    blockers.append(
                        f"{currency} [bull_put_spread]: max_groups_per_currency "
                        f"(open_for_strategy_currency={open_for_currency} >= {self.config.max_groups_per_currency})"
                    )
                    continue
            regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                detail = snap.regime_detail_by_currency.get(currency, ())
                blockers.append(f"{currency}: regime=crisis — {'; '.join(detail)}")
                continue
            loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
            naked_markets_by_collateral: dict[str, list[OptionInstrument]] = {}
            for market in context.markets_by_currency.get(currency, []):
                coll = "USDC" if self._linear_usdc_mode() else (market.settlement_currency or currency)
                naked_markets_by_collateral.setdefault(coll, []).append(market)
            for collateral_ccy, collateral_markets in sorted(naked_markets_by_collateral.items()):
                collateral_summary = context.summaries.get(collateral_ccy)
                if collateral_summary is None or collateral_summary.equity <= 0:
                    blockers.append(
                        f"{currency}/{collateral_ccy}: book equity<=0 or missing account summary"
                    )
                    continue
                ccy_ratios = snap.margin_ratios_by_currency.get(collateral_ccy)
                if ccy_ratios:
                    ccy_im, ccy_mm = ccy_ratios
                    if ccy_im >= self.config.book_im_target:
                        blockers.append(
                            f"{currency}/{collateral_ccy}: book im_ratio >= book_im_target "
                            f"({format_decimal(ccy_im, 8)} >= {format_decimal(self.config.book_im_target, 6)})"
                        )
                        continue
                    if ccy_mm >= self.config.book_mm_target:
                        blockers.append(
                            f"{currency}/{collateral_ccy}: book mm_ratio >= book_mm_target "
                            f"({format_decimal(ccy_mm, 8)} >= {format_decimal(self.config.book_mm_target, 6)})"
                        )
                        continue
                im_by_exp = self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
                builder_map: list[tuple[str, Callable[..., list[NakedPutCandidate]]]] = []
                if self.config.enable_short_put:
                    builder_map.append(("put", self.strategy.build_naked_short_put_candidates))
                if self.config.enable_short_call:
                    builder_map.append(("call", self.strategy.build_naked_short_call_candidates))
                if not builder_map:
                    blockers.append(f"{currency}/{collateral_ccy}: no option sides enabled (enable_short_put/enable_short_call both disabled)")
                    continue
                for side, builder in builder_map:
                    strategy_key = "naked_short"
                    open_for_strategy = self._open_group_count_for_strategy(context.state, strategy_key)
                    if (
                        self.config.max_concurrent_groups > 0
                        and open_for_strategy >= self.config.max_concurrent_groups
                    ):
                        blockers.append(
                            f"{currency}/{collateral_ccy} [{strategy_key}]: max_concurrent_groups "
                            f"(open_for_strategy={open_for_strategy} >= {self.config.max_concurrent_groups})"
                        )
                        continue
                    open_for_strategy_currency = self._open_group_count_for_currency(
                        context.state,
                        currency,
                        strategy=strategy_key,
                    )
                    if (
                        self.config.max_groups_per_currency > 0
                        and open_for_strategy_currency >= self.config.max_groups_per_currency
                    ):
                        blockers.append(
                            f"{currency}/{collateral_ccy} [{strategy_key}]: max_groups_per_currency "
                            f"(open_for_strategy_currency={open_for_strategy_currency} >= "
                            f"{self.config.max_groups_per_currency})"
                        )
                        continue
                    raw_naked = builder(
                        collateral_markets,
                        loader,
                        regime=regime,
                        summary_equity=collateral_summary.equity,
                        summary_maintenance_margin=collateral_summary.maintenance_margin,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        existing_im_by_expiry=im_by_exp,
                    )
                    if not raw_naked:
                        if side == "put":
                            detail = self.strategy.naked_put_scan_rejection_detail(
                                currency,
                                collateral_markets,
                                loader,
                                regime=regime,
                                summary_equity=collateral_summary.equity,
                                summary_maintenance_margin=collateral_summary.maintenance_margin,
                                collateral_currency=collateral_ccy,
                                existing_im_by_expiry=im_by_exp,
                            )
                            liq = detail.get("liquidity_rejections") or {}
                            post = detail.get("after_liquidity_rejections") or {}
                            prefix = f"{currency}/{collateral_ccy} [{side}]"
                            liq_line = self._format_scan_rejection_counts_inline("liquidity_rej", liq)
                            post_line = self._format_scan_rejection_counts_inline("post_liquidity_rej", post)
                            if liq_line:
                                blockers.append(f"{prefix}: {liq_line}")
                            if post_line:
                                blockers.append(f"{prefix}: {post_line}")
                            post_ex = self._post_only_scan_example_messages(detail.get("example_messages"))
                            for ex in post_ex[:_MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES]:
                                blockers.append(f"{prefix}: {ex}")
                            if not liq_line and not post_line:
                                blockers.append(
                                    f"{prefix}: puts_in_dte_window={detail.get('puts_in_dte_window', 0)}"
                                )
                        else:
                            detail = self.strategy.naked_call_scan_rejection_detail(
                                currency,
                                collateral_markets,
                                loader,
                                regime=regime,
                                summary_equity=collateral_summary.equity,
                                summary_maintenance_margin=collateral_summary.maintenance_margin,
                                collateral_currency=collateral_ccy,
                                existing_im_by_expiry=im_by_exp,
                            )
                            liq = detail.get("liquidity_rejections") or {}
                            post = detail.get("after_liquidity_rejections") or {}
                            prefix = f"{currency}/{collateral_ccy} [{side}]"
                            liq_line = self._format_scan_rejection_counts_inline("liquidity_rej", liq)
                            post_line = self._format_scan_rejection_counts_inline("post_liquidity_rej", post)
                            if liq_line:
                                blockers.append(f"{prefix}: {liq_line}")
                            if post_line:
                                blockers.append(f"{prefix}: {post_line}")
                            post_ex = self._post_only_scan_example_messages(detail.get("example_messages"))
                            for ex in post_ex[:_MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES]:
                                blockers.append(f"{prefix}: {ex}")
                            if not liq_line and not post_line:
                                blockers.append(
                                    f"{prefix}: calls_in_dte_window={detail.get('calls_in_dte_window', 0)}"
                                )
                        continue
                    deduped_naked = [c for c in raw_naked if not self._naked_candidate_matches_open_group(context.state, c)]
                    if not deduped_naked:
                        blockers.append(f"{currency}/{collateral_ccy} [{side}]: all naked candidates already open")
                        continue
                    best_net = max(c.net_apr for c in deduped_naked)
                    below_naked = [c for c in deduped_naked if c.net_apr < threshold]
                    if len(below_naked) == len(deduped_naked):
                        blockers.append(
                            f"{currency}/{collateral_ccy} [{side}]: {len(deduped_naked)} naked candidate(s) all below min_net_apr "
                            f"({format_decimal(threshold, 4)}); best net_apr={format_decimal(best_net, 8)}"
                        )
        if not blockers:
            blockers.append("no_candidates: empty selection or all currencies skipped before diagnostics")
        return blockers

    def _covered_call_scan_entry_blockers(
        self,
        context: RuntimeContext,
        *,
        selected_currencies: tuple[str, ...],
    ) -> list[str]:
        blockers: list[str] = []
        snap = context.snapshot
        threshold = self.config.min_net_apr
        orderbook_cache = context.orderbook_cache
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
        for currency in selected_currencies:
            ccy = currency.upper()
            open_for_strategy = self._open_group_count_for_strategy(context.state, "covered_call")
            if (
                self.config.max_concurrent_groups > 0
                and open_for_strategy >= self.config.max_concurrent_groups
            ):
                blockers.append(
                    f"{ccy} [covered_call]: max_concurrent_groups "
                    f"(open_for_strategy={open_for_strategy} >= {self.config.max_concurrent_groups})"
                )
                continue
            open_for_strategy_currency = self._open_group_count_for_currency(
                context.state,
                ccy,
                strategy="covered_call",
            )
            if (
                self.config.max_groups_per_currency > 0
                and open_for_strategy_currency >= self.config.max_groups_per_currency
            ):
                blockers.append(
                    f"{ccy} [covered_call]: max_groups_per_currency "
                    f"(open_for_strategy_currency={open_for_strategy_currency} >= "
                    f"{self.config.max_groups_per_currency})"
                )
                continue
            regime = context.regime_by_currency.get(ccy, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                detail = snap.regime_detail_by_currency.get(ccy, ())
                blockers.append(f"{ccy}: regime=crisis — {'; '.join(detail)}")
                continue
            summary = context.summaries.get(ccy)
            if summary is None or summary.equity <= 0:
                blockers.append(f"{ccy}/{ccy} [covered_call]: book equity<=0 or missing account summary")
                continue
            available_cover = self._available_covered_call_quantity(context, ccy)
            if available_cover <= 0:
                blockers.append(
                    f"{ccy}/{ccy} [covered_call]: no available {ccy} cover after existing covered_call reservations"
                )
                continue
            if not self._covered_call_book_im_mm_shielded(
                context.state,
                context.summaries,
                ccy,
                available_cover=available_cover,
            ):
                ccy_ratios = snap.margin_ratios_by_currency.get(ccy)
                if ccy_ratios:
                    ccy_im, ccy_mm = ccy_ratios
                    if ccy_im >= self.config.book_im_target:
                        blockers.append(
                            f"{ccy}/{ccy} [covered_call]: book im_ratio >= book_im_target "
                            f"({format_decimal(ccy_im, 8)} >= {format_decimal(self.config.book_im_target, 6)})"
                        )
                        continue
                    if ccy_mm >= self.config.book_mm_target:
                        blockers.append(
                            f"{ccy}/{ccy} [covered_call]: book mm_ratio >= book_mm_target "
                            f"({format_decimal(ccy_mm, 8)} >= {format_decimal(self.config.book_mm_target, 6)})"
                        )
                        continue
            collateral_markets = [
                market
                for market in context.markets_by_currency.get(ccy, [])
                if (market.settlement_currency or ccy).upper() == ccy
                and market.option_type == "call"
                and market.instrument_type != "linear"
                and self.config.entry_dte_min <= market.dte_days() <= self.config.entry_dte_max
            ]
            if not collateral_markets:
                blockers.append(f"{ccy}/{ccy} [covered_call]: no inverse call markets in entry DTE window")
                continue
            raw_candidates = self.strategy.build_covered_call_candidates(
                collateral_markets,
                loader,
                regime=regime,
                collateral_currency=ccy,
                currency=ccy,
                available_cover_quantity=available_cover,
                summary_equity=summary.equity,
            )
            if not raw_candidates:
                detail = self.strategy.covered_call_scan_rejection_detail(
                    ccy,
                    collateral_markets,
                    loader,
                    regime=regime,
                    collateral_currency=ccy,
                    available_cover_quantity=available_cover,
                    summary_equity=summary.equity,
                )
                prefix = f"{ccy}/{ccy} [covered_call]"
                liq = detail.get("liquidity_rejections") or {}
                post = detail.get("after_liquidity_rejections") or {}
                liq_line = self._format_scan_rejection_counts_inline("liquidity_rej", liq)
                post_line = self._format_scan_rejection_counts_inline("post_liquidity_rej", post)
                if liq_line:
                    blockers.append(f"{prefix}: {liq_line}")
                if post_line:
                    blockers.append(f"{prefix}: {post_line}")
                post_ex = self._post_only_scan_example_messages(detail.get("example_messages"))
                for ex in post_ex[:_MAX_SCAN_REJECTION_EXAMPLE_LOG_LINES]:
                    blockers.append(f"{prefix}: {ex}")
                if not liq_line and not post_line:
                    blockers.append(
                        f"{prefix}: calls_in_dte_window={detail.get('calls_in_dte_window', 0)}"
                    )
                continue
            deduped = [
                candidate
                for candidate in raw_candidates
                if not self._naked_candidate_matches_open_group(context.state, candidate)
            ]
            if not deduped:
                blockers.append(f"{ccy}/{ccy} [covered_call]: all covered_call candidates already open")
                continue
            best_net = max(candidate.net_apr for candidate in deduped)
            below_threshold = [candidate for candidate in deduped if candidate.net_apr < threshold]
            if len(below_threshold) == len(deduped):
                blockers.append(
                    f"{ccy}/{ccy} [covered_call]: {len(deduped)} candidate(s) all below min_net_apr "
                    f"({format_decimal(threshold, 4)}); best net_apr={format_decimal(best_net, 8)}"
                )
        if not blockers:
            blockers.append("no_candidates: empty selection or all currencies skipped before covered_call diagnostics")
        return blockers

    @staticmethod
    def _post_only_scan_example_messages(messages: list[str] | None) -> list[str]:
        """Strip scan `example_messages` to post-liquidity phase only (excludes [liquidity] / [build])."""
        if not messages:
            return []
        return [ex for ex in messages if " [post] " in ex]

    @staticmethod
    def _format_scan_rejection_counts_inline(title: str, counts: dict[str, Any]) -> str | None:
        """Single segment like `liquidity_rej: a=1, b=2` (sorted by count desc); None if empty."""
        if not counts:
            return None
        pairs: list[tuple[str, int]] = []
        for key, raw in counts.items():
            try:
                pairs.append((str(key), int(raw)))
            except (TypeError, ValueError):
                pairs.append((str(key), 0))
        pairs.sort(key=lambda kv: (-kv[1], kv[0]))
        max_show = 10
        head = pairs[:max_show]
        body = ", ".join(f"{k}={v}" for k, v in head)
        if len(pairs) > max_show:
            body += f", ...+{len(pairs) - max_show} more_reasons"
        return f"{title}: {body}"

    def _log_cycle_candidates(self, cycle_no: int, candidates: list[dict[str, Any]]) -> None:
        LOGGER.info("run cycle=%s candidate_count=%s", cycle_no, len(candidates))
        for rank, candidate in enumerate(candidates[:3], start=1):
            currency = candidate["currency"]
            short_instrument_name = candidate["short_instrument_name"]
            quantity = candidate["quantity"]
            max_profit_apr = candidate.get("max_profit_apr") or candidate.get("net_apr") or Decimal("0")
            net_credit = candidate.get("net_credit") or Decimal("0")
            max_loss = candidate.get("max_loss") or Decimal("0")
            LOGGER.info(
                "run cycle=%s candidate_rank=%s currency=%s short=%s qty=%s apr=%s net_credit=%s max_loss=%s",
                cycle_no,
                rank,
                currency,
                short_instrument_name,
                format_decimal(quantity, 8),
                format_decimal(max_profit_apr, 8),
                format_decimal(net_credit, 8),
                format_decimal(max_loss, 8),
            )

    def _log_cycle_update(self, cycle_no: int, cycle_result: dict[str, Any], *, live: bool) -> None:
        status = cycle_result["status"]
        portfolio = status["portfolio"]
        manage_actions = cycle_result["manage"].get("actions", [])
        entry = cycle_result["entry"]
        LOGGER.info(
            "run cycle=%s live=%s regime=%s open_groups=%s manage_actions=%s entry_action=%s",
            cycle_no,
            live,
            portfolio["regime"],
            len(status.get("trade_groups", [])),
            len(manage_actions),
            entry["action"],
        )
        if manage_actions:
            LOGGER.info(
                "run cycle=%s manage_action_types=%s",
                cycle_no,
                ",".join(action["action"] for action in manage_actions),
            )
        if entry.get("reason"):
            LOGGER.info("run cycle=%s entry_reason=%s", cycle_no, entry["reason"])
        regime_by_currency = portfolio.get("regime_by_currency") or {}
        regime_detail_by_currency = portfolio.get("regime_detail_by_currency") or {}
        for currency in sorted(regime_by_currency):
            regime_value = regime_by_currency[currency]
            if regime_value == RiskRegime.NORMAL.value:
                continue
            detail = regime_detail_by_currency.get(currency) or ()
            detail_text = "; ".join(detail) if detail else "(no detail)"
            LOGGER.info(
                "run cycle=%s [regime] %s=%s — %s",
                cycle_no,
                currency,
                regime_value,
                detail_text,
            )
        blockers = cycle_result["scan"].get("entry_blockers", [])
        if blockers and not cycle_result["scan"].get("candidates"):
            for line in blockers[:_MAX_SCAN_BLOCKER_LOG_LINES]:
                LOGGER.info("run cycle=%s [scan] %s", cycle_no, line)
            if len(blockers) > _MAX_SCAN_BLOCKER_LOG_LINES:
                LOGGER.info(
                    "run cycle=%s [scan] ... %s more blocker lines omitted",
                    cycle_no,
                    len(blockers) - _MAX_SCAN_BLOCKER_LOG_LINES,
                )
        self._log_cycle_candidates(cycle_no, cycle_result["scan"]["candidates"])
        topup_actions = cycle_result.get("topup", [])
        if topup_actions:
            LOGGER.info(
                "run cycle=%s topup_actions=%s",
                cycle_no,
                ",".join(a["action"] for a in topup_actions),
            )

    def _cycle_log_signature(self, cycle_result: dict[str, Any]) -> tuple[Any, ...]:
        status = cycle_result["status"]
        portfolio = status["portfolio"]
        scan = cycle_result["scan"]
        entry = cycle_result["entry"]
        return (
            portfolio["regime"],
            portfolio["halt_new_entries"],
            portfolio["hard_derisk"],
            portfolio["cooling_down"],
            self._normalized_log_reasons(portfolio.get("halt_entry_reasons", [])),
            tuple(
                sorted(
                    (
                        group["group_id"],
                        group["currency"],
                        group["short_instrument_name"],
                        str(group["quantity"]),
                        group["status"],
                    )
                    for group in status.get("trade_groups", [])
                )
            ),
            tuple(self._cycle_action_signature(action) for action in cycle_result["manage"].get("actions", [])),
            scan.get("candidate_count", 0),
            tuple(
                (
                    candidate["currency"],
                    candidate["short_instrument_name"],
                )
                for candidate in scan.get("candidates", [])[:3]
            ),
            self._normalized_log_reasons(scan.get("entry_blockers", [])),
            tuple(
                (
                    currency,
                    portfolio.get("regime_by_currency", {}).get(currency),
                    self._normalized_log_reasons(detail),
                )
                for currency, detail in sorted(
                    (portfolio.get("regime_detail_by_currency") or {}).items()
                )
            ),
            (
                entry.get("action"),
                entry.get("reason"),
                self._cycle_entry_signature(entry),
            ),
            tuple(a.get("action") for a in cycle_result.get("topup", [])),
        )

    def _normalized_log_reasons(self, reasons: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self._normalize_log_reason(reason) for reason in reasons)

    @staticmethod
    def _normalize_log_reason(reason: str) -> str:
        normalized = reason.strip()
        if " (" in normalized:
            normalized = normalized.split(" (", 1)[0]
        if "; " in normalized:
            normalized = normalized.split("; ", 1)[0]
        normalized = LOG_REASON_NUMBER_RE.sub("#", normalized)
        return " ".join(normalized.split())

    def _cycle_action_signature(self, action: dict[str, Any]) -> tuple[Any, ...]:
        return (
            action.get("action"),
            action.get("group_id"),
            action.get("reason"),
            action.get("currency"),
            action.get("instrument_name"),
            action.get("short_instrument_name"),
        )

    def _cycle_entry_signature(self, entry: dict[str, Any]) -> tuple[Any, ...] | None:
        candidate = entry.get("candidate")
        if not isinstance(candidate, dict):
            return None
        return (
            candidate.get("currency"),
            candidate.get("short_instrument_name"),
        )

    def _enter_best_from_candidates(
        self,
        context: RuntimeContext,
        *,
        candidates: list[NakedPutCandidate],
        live: bool,
    ) -> dict[str, Any]:
        if not candidates:
            return {
                "action": "no_candidate",
                "regime": context.snapshot.regime.value,
                "portfolio": context.snapshot.to_dict(),
            }

        candidate = candidates[0]
        if self._naked_candidate_matches_open_group(context.state, candidate):
            return {
                "action": "entry_skipped",
                "reason": "duplicate_open_group",
                "candidate": candidate.to_dict(),
            }
        group_id = self._next_group_id(context.state)
        labels = self._spread_labels(candidate.currency, group_id)
        if not live:
            strategy = candidate.strategy or "naked_short"
            dry_action = f"dry_run_enter_{strategy}"
            if strategy == "naked_short":
                dry_action = f"dry_run_enter_naked_{candidate.option_type}"
            requests: dict[str, Any] = {
                "short_leg": self._entry_naked_short_request(candidate, labels["short"], quantity=candidate.quantity, aggressive=False),
            }
            execution_mode = "naked_short_post_only"
            if strategy == "bull_put_spread" and candidate.long_leg is not None:
                requests = {
                    "long_leg": self._entry_long_buy_request(candidate, labels["long"], quantity=candidate.quantity),
                    "short_leg": requests["short_leg"],
                }
                execution_mode = "bull_put_spread_buy_protection_first"
            return {
                "action": dry_action,
                "candidate": candidate.to_dict(),
                "group_id": group_id,
                "requests": requests,
                "execution_mode": execution_mode,
            }
        if (candidate.strategy or "") == "bull_put_spread":
            return self._execute_bull_put_spread_entry(context, candidate, group_id)
        return self._execute_naked_put_entry(context, candidate, group_id)

    def _scan_candidates(
        self,
        context: RuntimeContext,
        *,
        currencies: tuple[str, ...] | None,
        top_n: int | None,
    ) -> list[NakedPutCandidate]:
        snapshot = context.snapshot
        # Portfolio-wide kill switches (data_unavailable, open_max_loss_pct, or
        # every enabled book halted) still short-circuit the scan. Per-book
        # halts are handled inside the per-currency loop so one halted book
        # can't silently sink the others.
        global_blockers = (
            bool(snapshot.cooldown_until_ms and snapshot.cooldown_until_ms > utc_now_ms())
            or snapshot.open_max_loss_pct >= self.config.halt_open_max_loss_pct
            or any(
                any(note.startswith("data_unavailable") for note in notes)
                for notes in snapshot.regime_detail_by_currency.values()
            )
        )
        if global_blockers:
            return []
        if snapshot.halt_entries_by_book and all(snapshot.halt_entries_by_book.values()):
            return []
        selected = currencies or self.config.managed_currencies
        active_strategies = self._active_scan_strategy_keys()
        if (
            self.config.max_concurrent_groups > 0
            and active_strategies
            and all(
                self._open_group_count_for_strategy(context.state, strategy) >= self.config.max_concurrent_groups
                for strategy in active_strategies
            )
        ):
            return []

        limit = top_n or self.config.top_n
        orderbook_cache = context.orderbook_cache
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)

        candidates_n: list[NakedPutCandidate] = []
        threshold = self.config.min_net_apr
        for currency in selected:
            if self.config.option_strategy == "bull_put_spread":
                if self._strategy_at_currency_limit(context.state, "bull_put_spread", currency):
                    continue
            elif self.config.option_strategy == "covered_call":
                if self._strategy_at_currency_limit(context.state, "covered_call", currency):
                    continue
            regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                continue
            markets_by_collateral: dict[str, list[OptionInstrument]] = {}
            for market in context.markets_by_currency.get(currency, []):
                coll = "USDC" if self._linear_usdc_mode() else (market.settlement_currency or currency)
                markets_by_collateral.setdefault(coll, []).append(market)
            for collateral_ccy, collateral_markets in sorted(markets_by_collateral.items()):
                # Skip candidates routed to a book that is currently halted
                # (drawdown, cooldown, or hard IM/MM breach) while still
                # evaluating other books for the same underlying.
                if snapshot.halt_entries_by_book.get(collateral_ccy):
                    continue
                collateral_summary = context.summaries.get(collateral_ccy)
                if collateral_summary is None or collateral_summary.equity <= 0:
                    continue
                skip_book_im_mm = False
                if self.config.option_strategy == "covered_call":
                    available_cover = self._available_covered_call_quantity(context, currency)
                    skip_book_im_mm = self._covered_call_book_im_mm_shielded(
                        context.state,
                        context.summaries,
                        collateral_ccy,
                        available_cover=available_cover,
                    )
                ccy_ratios = context.snapshot.margin_ratios_by_currency.get(collateral_ccy)
                if ccy_ratios and not skip_book_im_mm:
                    ccy_im, ccy_mm = ccy_ratios
                    if ccy_im >= self.config.book_im_target or ccy_mm >= self.config.book_mm_target:
                        continue
                im_by_exp = self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
                if self.config.option_strategy == "bull_put_spread":
                    if self._strategy_at_concurrent_limit(context.state, "bull_put_spread"):
                        continue
                    for candidate in self.strategy.build_bull_put_spread_candidates(
                        collateral_markets,
                        loader,
                        regime=regime,
                        summary_equity=collateral_summary.equity,
                        summary_maintenance_margin=collateral_summary.maintenance_margin,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        existing_im_by_expiry=im_by_exp,
                    ):
                        if self._naked_candidate_matches_open_group(context.state, candidate):
                            continue
                        if candidate.net_apr < threshold:
                            continue
                        candidates_n.append(candidate)
                    continue
                if self.config.option_strategy == "covered_call":
                    if self._strategy_at_concurrent_limit(context.state, "covered_call"):
                        continue
                    available_cover = self._available_covered_call_quantity(context, currency)
                    for candidate in self.strategy.build_covered_call_candidates(
                        collateral_markets,
                        loader,
                        regime=regime,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        available_cover_quantity=available_cover,
                        summary_equity=collateral_summary.equity,
                    ):
                        if self._naked_candidate_matches_open_group(context.state, candidate):
                            continue
                        if candidate.net_apr < threshold:
                            continue
                        candidates_n.append(candidate)
                    continue
                put_candidates: list[NakedPutCandidate] = []
                if (
                    self.config.enable_short_put
                    and not self._strategy_at_concurrent_limit(context.state, "naked_short")
                    and not self._strategy_at_currency_limit(context.state, "naked_short", currency)
                ):
                    for candidate in self.strategy.build_naked_short_put_candidates(
                        collateral_markets,
                        loader,
                        regime=regime,
                        summary_equity=collateral_summary.equity,
                        summary_maintenance_margin=collateral_summary.maintenance_margin,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        existing_im_by_expiry=im_by_exp,
                    ):
                        if self._naked_candidate_matches_open_group(context.state, candidate):
                            continue
                        if candidate.net_apr < threshold:
                            continue
                        put_candidates.append(candidate)
                candidates_n.extend(put_candidates)

                scan_calls = self.config.enable_short_call and (
                    not self.config.short_call_fallback_only or not put_candidates
                )
                if (
                    scan_calls
                    and not self._strategy_at_concurrent_limit(context.state, "naked_short")
                    and not self._strategy_at_currency_limit(context.state, "naked_short", currency)
                ):
                    for candidate in self.strategy.build_naked_short_call_candidates(
                        collateral_markets,
                        loader,
                        regime=regime,
                        summary_equity=collateral_summary.equity,
                        summary_maintenance_margin=collateral_summary.maintenance_margin,
                        collateral_currency=collateral_ccy,
                        currency=currency,
                        existing_im_by_expiry=im_by_exp,
                    ):
                        if self._naked_candidate_matches_open_group(context.state, candidate):
                            continue
                        if candidate.net_apr < threshold:
                            continue
                        candidates_n.append(candidate)
        return self.strategy.take_top_scan_candidates(
            candidates_n,
            limit=limit,
        )

    def _manage_group(self, context: RuntimeContext, group: TradeGroup, *, live: bool) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        is_covered_call = self._is_covered_call_group(group)
        if is_covered_call:
            robust_exit_actions = self._maybe_covered_call_robust_spot_exit(context, group, live=live)
            if robust_exit_actions is not None:
                return robust_exit_actions
            return actions
        soft_delta, hard_delta = self._defense_delta_thresholds(group)
        hard_trigger = not is_covered_call and (
            group.short_delta >= hard_delta
            or group.loss_pct_of_max_loss >= self.config.hard_stop_loss_pct
        )
        soft_trigger = not is_covered_call and (
            group.short_delta >= soft_delta
            or group.loss_pct_of_max_loss >= self.config.soft_defense_loss_pct
        )
        if hard_trigger:
            if self.config.enable_perp_hedge:
                hedge_plan = self._build_hedge_plan(context, group.currency, mode="hard")
                if hedge_plan is not None:
                    actions.append(self._execute_hedge_plan(context, hedge_plan, live=live))
            actions.extend(self._close_group(context, group, reason="hard_stop", live=live))
            return actions
        if group.profit_capture >= self.config.tp_capture_pct:
            actions.extend(self._close_group(context, group, reason="take_profit", live=live))
            return actions
        early_exit_reason = self._maybe_early_exit_reason(context, group)
        if early_exit_reason is not None:
            actions.extend(self._close_group(context, group, reason=early_exit_reason, live=live))
            return actions
        robust_exit_actions = self._maybe_covered_call_robust_spot_exit(context, group, live=live)
        if robust_exit_actions is not None:
            return robust_exit_actions
        if group.dte_days <= self.config.time_exit_dte:
            actions.extend(self._close_group(context, group, reason="time_exit", live=live))
            return actions
        if soft_trigger:
            if self.config.enable_perp_hedge:
                hedge_plan = self._build_hedge_plan(context, group.currency, mode="soft")
                if hedge_plan is not None:
                    actions.append(self._execute_hedge_plan(context, hedge_plan, live=live))
                else:
                    actions.extend(self._close_group(context, group, reason="soft_stop_no_hedge", live=live))
            else:
                actions.extend(self._close_group(context, group, reason="soft_stop", live=live))
        return actions

    def _defense_delta_thresholds(self, group: TradeGroup) -> tuple[Decimal, Decimal]:
        if (group.option_type or "").lower() == "call":
            return self.config.soft_defense_delta_call, self.config.hard_defense_delta_call
        return self.config.soft_defense_delta, self.config.hard_defense_delta

    def _maybe_covered_call_robust_spot_exit(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> list[dict[str, Any]] | None:
        if not self.config.covered_call_spot_exit_enabled:
            return None
        if not self.config.covered_call_robust_exit_enabled:
            return None
        if not self._is_covered_call_group(group):
            return None
        if group.dte_days > self.config.covered_call_robust_exit_dte:
            return None
        if not self._covered_call_itm(group, context):
            return None

        actions = self._close_group(context, group, reason="covered_call_robust_exit", live=live)
        if not live:
            actions.append(
                self._execute_covered_call_spot_exit(
                    context,
                    group,
                    reason="covered_call_robust_exit_preview",
                    live=False,
                )
            )
            return actions
        if group.status != "closed":
            actions.append(
                {
                    "action": "covered_call_spot_exit_skipped",
                    "group_id": group.group_id,
                    "reason": "option_close_incomplete",
                }
            )
            return actions
        actions.append(
            self._execute_covered_call_spot_exit(
                context,
                group,
                reason="covered_call_robust_exit",
                live=True,
            )
        )
        return actions

    def _pending_covered_call_spot_exit_actions(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        if not self.config.covered_call_spot_exit_enabled:
            return []
        actions: list[dict[str, Any]] = []
        for group in context.state.groups:
            if (
                group.status == "closed"
                and self._is_covered_call_group(group)
                and group.spot_exit_status == "pending"
            ):
                actions.append(
                    self._execute_covered_call_spot_exit(
                        context,
                        group,
                        reason=group.spot_exit_reason or "covered_call_settlement_exit",
                        live=live,
                    )
                )
        return actions

    def _is_covered_call_strategy(self) -> bool:
        return self.config.option_strategy == "covered_call"

    def _available_covered_call_quantity_from_summaries(
        self,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        currency: str,
    ) -> Decimal:
        ccy = currency.upper()
        summary = summaries.get(ccy)
        if summary is None:
            return Decimal("0")
        reserved = self._reserved_covered_call_quantity(state, ccy)
        return max(summary.equity - reserved, Decimal("0"))

    def _covered_call_book_im_mm_shielded(
        self,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        currency: str,
        *,
        available_cover: Decimal | None = None,
    ) -> bool:
        """Skip book IM/MM gates when covered_call still has native spot backing."""
        if not self._is_covered_call_strategy():
            return False
        ccy = currency.upper()
        if available_cover is None:
            available_cover = self._available_covered_call_quantity_from_summaries(state, summaries, ccy)
        if available_cover > 0:
            return True
        return self._covered_call_book_fully_collateralized(state, summaries, ccy)

    def _covered_call_book_fully_collateralized(
        self,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        currency: str,
    ) -> bool:
        """True when this collateral book still holds enough native equity for open covered calls."""
        if not self._is_covered_call_strategy():
            return False
        ccy = currency.upper()
        summary = summaries.get(ccy)
        if summary is None or summary.equity <= 0:
            return False
        reserved = self._reserved_covered_call_quantity(state, ccy)
        if reserved <= 0:
            return False
        return summary.equity >= reserved

    def _clear_covered_call_book_cooldowns(
        self,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
    ) -> None:
        """Drop stale cooldowns once native book equity still covers open short calls."""
        for ccy in summaries:
            if not self._covered_call_book_fully_collateralized(state, summaries, ccy):
                continue
            state.cooldown_until_ms_by_book.pop(ccy.upper(), None)
        if not any(not self._is_covered_call_group(group) for group in self._open_groups(state)):
            state.cooldown_until_ms = None

    @staticmethod
    def _is_covered_call_group(group: TradeGroup) -> bool:
        return group.option_type == "call" and (
            (group.strategy or "") == "covered_call"
            or group.covered_underlying_quantity > 0
            or group.short_label.startswith("covered_call-")
        )

    def _covered_call_itm(self, group: TradeGroup, context: RuntimeContext) -> bool:
        index_price = self._currency_index_price(group.currency, context.orderbook_cache)
        if index_price <= 0:
            try:
                index_price = self._get_orderbook(group.short_instrument_name, context.orderbook_cache).index_price
            except Exception:
                index_price = Decimal("0")
        if index_price <= 0 or group.short_strike <= 0:
            return False
        trigger = group.short_strike * (Decimal("1") + self.config.covered_call_itm_buffer_pct)
        return index_price > trigger

    def _covered_call_itm_from_cache(
        self,
        group: TradeGroup,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> bool:
        index_price = self._currency_index_price(group.currency, orderbook_cache)
        if index_price <= 0 or group.short_strike <= 0:
            return False
        trigger = group.short_strike * (Decimal("1") + self.config.covered_call_itm_buffer_pct)
        return index_price > trigger

    @staticmethod
    def _covered_call_spot_instrument(currency: str) -> str:
        return f"{currency.upper()}_USDC"

    def _spot_min_trade_amount(self, instrument_name: str, currency: str) -> tuple[Decimal, Decimal]:
        for lookup_currency in ("USDC", currency.upper()):
            try:
                rows = self.client.get_instruments(lookup_currency, kind="spot", expired=False)
            except Exception:
                continue
            for row in rows:
                instrument = OptionInstrument.from_api(row)
                if instrument.instrument_name == instrument_name:
                    return instrument.contract_size, instrument.min_trade_amount
        return Decimal("0"), Decimal("0")

    def _covered_call_spot_exit_amount(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        live: bool,
    ) -> Decimal:
        target = group.covered_underlying_quantity if group.covered_underlying_quantity > 0 else group.quantity
        if target <= 0:
            return Decimal("0")

        summary = context.summaries.get(group.currency)
        if live:
            summary = self._account_summaries_by_currency().get(group.currency, summary)
        if summary is not None:
            available = max(summary.available_funds, summary.available_withdrawal_funds, summary.balance)
            if available <= 0:
                return Decimal("0")
            target = min(target, available)

        instrument_name = self._covered_call_spot_instrument(group.currency)
        contract_size, min_trade_amount = self._spot_min_trade_amount(instrument_name, group.currency)
        aligned = align_option_order_amount(target, contract_size, min_trade_amount)
        if contract_size > 0 or min_trade_amount > 0:
            return aligned
        return target

    def _execute_covered_call_spot_exit(
        self,
        context: RuntimeContext,
        group: TradeGroup,
        *,
        reason: str,
        live: bool,
    ) -> dict[str, Any]:
        if group.spot_exit_status in {"submitted", "filled"}:
            return {
                "action": "covered_call_spot_exit_skipped",
                "group_id": group.group_id,
                "reason": f"already_{group.spot_exit_status}",
                "spot_exit_status": group.spot_exit_status,
                "spot_exit_order_id": group.spot_exit_order_id or None,
            }

        instrument_name = self._covered_call_spot_instrument(group.currency)
        amount = self._covered_call_spot_exit_amount(context, group, live=live)
        if amount <= 0:
            if live:
                group.spot_exit_status = "skipped"
                group.spot_exit_reason = "spot_amount_below_min_or_unavailable"
            return {
                "action": "covered_call_spot_exit_skipped",
                "group_id": group.group_id,
                "reason": "spot_amount_below_min_or_unavailable",
                "instrument_name": instrument_name,
                "amount": format_decimal(amount, 8),
                "live": live,
            }

        payload = {
            "action": "covered_call_spot_exit" if live else "covered_call_spot_exit_preview",
            "group_id": group.group_id,
            "reason": reason,
            "instrument_name": instrument_name,
            "amount": format_decimal(amount, 8),
            "order_type": self.config.covered_call_spot_order_type,
            "live": live,
        }
        if not live:
            return payload

        group.spot_exit_status = "submitted"
        group.spot_exit_amount = amount
        group.spot_exit_instrument_name = instrument_name
        group.spot_exit_reason = reason
        label = f"{group.short_label or self._spread_labels(group.currency, group.group_id)['short']}-spot-exit"
        try:
            response = self.client.place_sell_order(
                instrument_name=instrument_name,
                amount=amount,
                label=label,
                order_type=self.config.covered_call_spot_order_type,
            )
        except Exception as exc:  # Do not blindly retry non-idempotent spot sells.
            group.spot_exit_reason = f"{reason}: submission_uncertain: {exc}"
            payload["spot_exit_status"] = group.spot_exit_status
            payload["error"] = str(exc)
            return payload

        order = self._response_order(response)
        order_state = str(order.get("order_state") or "").lower()
        filled = self._response_filled_amount(response)
        group.spot_exit_order_id = str(order.get("order_id") or "")
        if order_state == "filled" or filled >= amount:
            group.spot_exit_status = "filled"
        elif order_state in {"cancelled", "rejected"}:
            group.spot_exit_status = "failed"
        else:
            group.spot_exit_status = "submitted"
        payload["spot_exit_status"] = group.spot_exit_status
        payload["spot_exit_order_id"] = group.spot_exit_order_id or None
        payload["filled_amount"] = format_decimal(filled, 8)
        payload["response"] = response
        return payload

    def _maybe_early_exit_reason(self, context: RuntimeContext, group: TradeGroup) -> str | None:
        """Decide whether to close a short option leg early.

        Triggers ``early_exit_low_apr`` when:
          * ``enable_early_exit`` is on, and
          * the short leg's book spread is tight enough that crossing the ask
            is cheap (maker/taker fees on Deribit options are equal, so taker
            execution has no structural disadvantage), and
          * at least ``early_exit_min_profit_capture`` of the entry credit has
            been realized (we don't bail out while still in the red), and
          * the *remaining* annualized yield of holding the position to expiry
            has decayed below ``early_exit_remaining_apr`` — typically because
            the underlying moved away from the strike and the residual premium
            is no longer worth the margin lock-up.
        """
        if not self.config.enable_early_exit:
            return None
        if group.dte_days <= 0 or group.max_loss <= 0:
            return None
        try:
            short_book = self._get_orderbook(group.short_instrument_name, context.orderbook_cache)
        except Exception:
            # Orderbook fetch can fail transiently (rate-limit, network blip);
            # early exit is optional so surface the failure in the log and skip
            # this group rather than swallowing the traceback silently.
            LOGGER.exception(
                "early_exit: failed to load orderbook for %s, skipping",
                group.short_instrument_name,
            )
            return None
        if short_book.best_ask_price <= 0 or short_book.best_bid_price <= 0:
            return None
        if short_book.spread_ratio > self.config.early_exit_max_spread_ratio:
            return None
        if group.profit_capture < self.config.early_exit_min_profit_capture:
            return None
        remaining_credit = max(group.current_debit - group.current_close_fee, Decimal("0"))
        if remaining_credit <= 0:
            return "early_exit_low_apr"
        remaining_apr = (remaining_credit / group.max_loss) * (Decimal("365") / group.dte_days)
        if remaining_apr < self.config.early_exit_remaining_apr:
            return "early_exit_low_apr"
        return None

    def _maybe_unwind_hedge(self, context: RuntimeContext, *, currency: str, live: bool) -> dict[str, Any] | None:
        recovery_count = context.state.normal_recovery_counts.get(currency, 0)
        if recovery_count < self.config.recovery_normal_cycles:
            return None
        current_perp_base = self._current_hedge_base(context.future_positions, currency)
        if current_perp_base >= 0:
            return None
        unwind_base = abs(current_perp_base) / Decimal("2")
        if unwind_base <= 0:
            return None
        index_price = self._currency_index_price(currency, context.orderbook_cache)
        amount = self._align_future_order_amount(
            context,
            instrument_name=self._perp_instrument(currency),
            amount=unwind_base * index_price,
        )
        if amount <= 0:
            return None
        unwind_base = amount / index_price
        action = {
            "action": "hedge_unwind",
            "currency": currency,
            "instrument_name": self._perp_instrument(currency),
            "amount": format_decimal(amount, 8),
            "base_amount": format_decimal(unwind_base, 8),
            "live": live,
        }
        if live:
            response = self.client.place_buy_order(
                instrument_name=self._perp_instrument(currency),
                amount=amount,
                label=self._hedge_label(currency, "recovery"),
                order_type="market",
                reduce_only=True,
            )
            action["response"] = response
        return action

    def _close_group(self, context: RuntimeContext, group: TradeGroup, *, reason: str, live: bool) -> list[dict[str, Any]]:
        short_book = self._get_orderbook(group.short_instrument_name, context.orderbook_cache)
        close_short = {
            "instrument_name": group.short_instrument_name,
            "amount": format_decimal(group.quantity, 8),
            "price": format_decimal(
                self.strategy.close_buy_price(self._find_instrument(context, group.short_instrument_name), short_book),
                8,
            ),
            "label": f"{group.short_label or self._spread_labels(group.currency, group.group_id)['short']}-close",
            "direction": "buy",
        }
        if not live:
            requests: dict[str, Any] = {"short_leg": close_short}
            if group.long_instrument_name:
                long_book = self._get_orderbook(group.long_instrument_name, context.orderbook_cache)
                long_inst = self._find_instrument(context, group.long_instrument_name)
                requests["long_leg"] = {
                    "instrument_name": group.long_instrument_name,
                    "amount": format_decimal(group.quantity, 8),
                    "price": format_decimal(self.strategy.close_sell_price(long_inst, long_book), 8),
                    "label": f"{group.long_label or self._spread_labels(group.currency, group.group_id)['long']}-close",
                    "direction": "sell",
                }
            return [
                {
                    "action": "close_group_preview",
                    "reason": reason,
                    "group_id": group.group_id,
                    "requests": requests,
                }
            ]
        short_result = self._close_leg_with_retry(
            context,
            instrument_name=group.short_instrument_name,
            quantity=group.quantity,
            direction="buy",
            label=close_short["label"],
            initial_price=to_decimal(close_short["price"]),
        )
        short_response = short_result["last_response"]
        if short_result["unfilled"] > 0:
            LOGGER.warning("close_group %s: short leg unfilled=%s", group.group_id, short_result["unfilled"])
            group.last_action = f"{reason}_incomplete"
            return [
                {
                    "action": "close_group_incomplete",
                    "reason": reason,
                    "group_id": group.group_id,
                    "short_filled": short_result["filled"],
                    "short_unfilled": short_result["unfilled"],
                    "responses": {"short_leg": short_response, "short_attempts": short_result["responses"]},
                }
            ]
        closed_timestamp_ms = utc_now_ms()
        short_close_price = short_result["average_price"]
        short_instrument = self._find_instrument(context, group.short_instrument_name)
        realized_close_debit = self._premium_value_usdc(
            premium=short_close_price,
            quantity=group.quantity,
            index_price=short_book.index_price,
            instrument=short_instrument,
        )
        realized_close_fee = self._option_fee_usdc(
            premium=short_close_price,
            quantity=group.quantity,
            index_price=short_book.index_price,
            base_currency=short_instrument.base_currency,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        realized_close_debit += realized_close_fee
        long_response = None
        if group.long_instrument_name:
            long_result = self._close_leg_with_retry(
                context,
                instrument_name=group.long_instrument_name,
                quantity=group.quantity,
                direction="sell",
                label=f"{group.long_label or self._spread_labels(group.currency, group.group_id)['long']}-close",
                initial_price=self.strategy.close_sell_price(
                    self._find_instrument(context, group.long_instrument_name),
                    self._get_orderbook(group.long_instrument_name, context.orderbook_cache),
                ),
            )
            long_response = long_result["last_response"]
            if long_result["unfilled"] > 0:
                LOGGER.warning("close_group %s: long leg unfilled=%s", group.group_id, long_result["unfilled"])
            long_close_price = long_result["average_price"]
            long_instrument = self._find_instrument(context, group.long_instrument_name)
            long_book = self._get_orderbook(group.long_instrument_name, context.orderbook_cache)
            long_credit = self._premium_value_usdc(
                premium=long_close_price,
                quantity=long_result["filled"],
                index_price=long_book.index_price,
                instrument=long_instrument,
            )
            long_fee = self._option_fee_usdc(
                premium=long_close_price,
                quantity=long_result["filled"],
                index_price=long_book.index_price,
                base_currency=long_instrument.base_currency,
                quote_currency=long_instrument.quote_currency,
                settlement_currency=long_instrument.settlement_currency,
            )
            realized_close_debit -= max(long_credit - long_fee, Decimal("0"))
            realized_close_fee += long_fee
        realized_pnl = group.entry_credit - realized_close_debit
        realized_return_on_max_loss = safe_div(realized_pnl, group.max_loss)
        realized_annualized_return = self._realized_annualized_return_on_im_native(
            group,
            realized_pnl,
            index_price_usd=short_book.index_price,
            closed_timestamp_ms=closed_timestamp_ms,
            orderbook_cache=context.orderbook_cache,
        )
        self._mark_group_closed(
            group,
            reason=reason,
            closed_timestamp_ms=closed_timestamp_ms,
            realized_close_debit=realized_close_debit,
            realized_close_fee=realized_close_fee,
            realized_pnl=realized_pnl,
            realized_return_on_max_loss=realized_return_on_max_loss,
            realized_annualized_return=realized_annualized_return,
        )
        return [
            {
                "action": "close_group",
                "reason": reason,
                "group_id": group.group_id,
                "realized_close_debit": realized_close_debit,
                "realized_close_fee": realized_close_fee,
                "realized_pnl": realized_pnl,
                "realized_return_on_max_loss": realized_return_on_max_loss,
                "realized_annualized_return": realized_annualized_return,
                "responses": {
                    "short_leg": short_response,
                    "short_attempts": short_result["responses"],
                    "long_leg": long_response,
                },
            }
        ]

    def _close_leg_with_retry(
        self,
        context: RuntimeContext,
        *,
        instrument_name: str,
        quantity: Decimal,
        direction: str,
        label: str,
        initial_price: Decimal,
    ) -> dict[str, Any]:
        responses: list[dict[str, Any]] = []
        total_filled = Decimal("0")
        weighted_price = Decimal("0")
        place_fn = self.client.place_buy_order if direction == "buy" else self.client.place_sell_order
        inst = self._find_instrument(context, instrument_name)
        requested_total = align_option_order_amount(quantity, inst.contract_size, inst.min_trade_amount)
        if requested_total <= 0:
            raise ExchangeError(
                f"close order amount aligned to zero for {instrument_name}; "
                f"requested quantity is below exchange minimum/step"
            )
        initial_price = self._positive_option_limit_price(inst, initial_price)

        capacity = self._option_reduce_only_capacity(
            instrument_name, direction, option_positions=context.option_positions
        )
        first_amount = align_option_order_amount(
            min(requested_total, capacity), inst.contract_size, inst.min_trade_amount
        )
        if first_amount <= 0:
            LOGGER.warning(
                "close_leg_with_retry: skip reduce_only %s on %s (label=%s requested=%s exchange_capacity=%s)",
                direction,
                instrument_name,
                label,
                requested_total,
                capacity,
            )
            noop = self._noop_option_order_response()
            return {
                "responses": [noop],
                "last_response": noop,
                "average_price": Decimal("0"),
                "filled": Decimal("0"),
                "unfilled": requested_total,
            }

        response = place_fn(
            instrument_name=instrument_name,
            amount=first_amount,
            label=label,
            order_type="limit",
            price=initial_price,
            time_in_force="immediate_or_cancel",
            reduce_only=True,
        )
        responses.append(response)
        filled = self._response_filled_amount(response)
        avg = self._response_average_price(response)
        if filled > 0:
            weighted_price += avg * filled
            total_filled += filled

        remaining = requested_total - total_filled
        if remaining > 0:
            context.orderbook_cache.pop(instrument_name, None)
            retry_book = self._get_orderbook(instrument_name, context.orderbook_cache)
            instrument = self._find_instrument(context, instrument_name)
            remaining = align_option_order_amount(remaining, instrument.contract_size, instrument.min_trade_amount)
            if remaining <= 0:
                average_price = weighted_price / total_filled if total_filled > 0 else Decimal("0")
                return {
                    "responses": responses,
                    "last_response": responses[-1],
                    "average_price": average_price,
                    "filled": total_filled,
                    "unfilled": requested_total - total_filled,
                }
            # Retry capacity: the first leg may have partially filled which
            # reduces our position; re-poll positions once to reflect that
            # rather than using the stale manage-loop snapshot.
            retry_capacity = self._option_reduce_only_capacity(instrument_name, direction)
            retry_amount = align_option_order_amount(
                min(remaining, retry_capacity), instrument.contract_size, instrument.min_trade_amount
            )
            if retry_amount <= 0:
                average_price = weighted_price / total_filled if total_filled > 0 else Decimal("0")
                return {
                    "responses": responses,
                    "last_response": responses[-1],
                    "average_price": average_price,
                    "filled": total_filled,
                    "unfilled": requested_total - total_filled,
                }
            if direction == "buy":
                retry_price = self.strategy.close_buy_price(instrument, retry_book)
            else:
                retry_price = self.strategy.close_sell_price(instrument, retry_book)
            retry_price = self._positive_option_limit_price(instrument, retry_price)
            response = place_fn(
                instrument_name=instrument_name,
                amount=retry_amount,
                label=label,
                order_type="limit",
                price=retry_price,
                time_in_force="immediate_or_cancel",
                reduce_only=True,
            )
            responses.append(response)
            filled = self._response_filled_amount(response)
            avg = self._response_average_price(response)
            if filled > 0:
                weighted_price += avg * filled
                total_filled += filled

        average_price = weighted_price / total_filled if total_filled > 0 else Decimal("0")
        return {
            "responses": responses,
            "last_response": responses[-1],
            "average_price": average_price,
            "filled": total_filled,
            "unfilled": requested_total - total_filled,
        }

    def _positive_option_limit_price(self, instrument: OptionInstrument, price: Decimal) -> Decimal:
        if price > 0:
            return price
        tick = instrument.tick_size_for_price(instrument.tick_size) if instrument.tick_size > 0 else Decimal("0")
        if tick > 0:
            return tick
        return Decimal("0.0001")

    def _option_reduce_only_capacity(
        self,
        instrument_name: str,
        order_direction: str,
        *,
        option_positions: list[Position] | None = None,
    ) -> Decimal:
        """Option contracts exchange will allow to close with a reduce_only order in this direction.

        When ``option_positions`` is provided the caller (typically via
        ``RuntimeContext.option_positions``) reuses a single ``get_positions``
        snapshot for a whole manage pass instead of paying one REST roundtrip
        per group. Falls back to fetching positions on demand when the caller
        cannot supply them (e.g. close-leg retries after a partial fill).
        """
        want = order_direction.lower()
        if option_positions is not None:
            for pos in option_positions:
                if pos.instrument_name != instrument_name:
                    continue
                if pos.kind != "option":
                    continue
                size = abs(pos.size)
                if size <= 0:
                    return Decimal("0")
                if want == "sell" and pos.direction == "buy":
                    return size
                if want == "buy" and pos.direction == "sell":
                    return size
                return Decimal("0")
            return Decimal("0")

        for row in self.client.get_positions(currency="any", kind="any"):
            if str(row.get("instrument_name") or "") != instrument_name:
                continue
            if str(row.get("kind") or "").lower() != "option":
                continue
            pos_dir = str(row.get("direction") or "").lower()
            size = abs(to_decimal(row.get("size")))
            if size <= 0:
                return Decimal("0")
            if want == "sell" and pos_dir == "buy":
                return size
            if want == "buy" and pos_dir == "sell":
                return size
            return Decimal("0")
        return Decimal("0")

    @staticmethod
    def _noop_option_order_response() -> dict[str, Any]:
        return {"order": {"filled_amount": "0", "average_price": "0", "order_state": "filled"}}

    def _entry_naked_short_request(
        self,
        candidate: NakedPutCandidate,
        label: str,
        *,
        aggressive: bool = False,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
    ) -> dict[str, Any]:
        quantity = quantity or candidate.quantity
        instrument = self._entry_leg_instrument(candidate.currency, candidate.short_leg, quantity)
        book = OrderBookSnapshot(
            instrument_name=candidate.short_leg.instrument_name,
            best_bid_price=candidate.short_leg.best_bid_price,
            best_bid_amount=quantity,
            best_ask_price=candidate.short_leg.best_ask_price,
            best_ask_amount=quantity,
            mark_price=candidate.screening_mark,
            index_price=candidate.short_leg.index_price,
            delta=candidate.short_leg.delta,
            iv=Decimal("0"),
            open_interest=Decimal("0"),
        )
        if aggressive:
            return {
                "instrument_name": candidate.short_leg.instrument_name,
                "amount": format_decimal(quantity, 8),
                "price": format_decimal(
                    candidate.short_leg.entry_price
                    if candidate.short_leg.entry_price > 0
                    else self.strategy.sell_taker_price(instrument, book),
                    8,
                ),
                "label": label,
                "time_in_force": "immediate_or_cancel",
            }
        return {
            "instrument_name": candidate.short_leg.instrument_name,
            "amount": format_decimal(quantity, 8),
            "price": format_decimal(price if price is not None else self.strategy.sell_mid_price(instrument, book), 8),
            "label": label,
            "time_in_force": "good_til_cancelled",
            "post_only": True,
            "reject_post_only": True,
        }

    def _entry_long_buy_request(
        self,
        candidate: NakedPutCandidate,
        label: str,
        *,
        quantity: Decimal,
    ) -> dict[str, Any]:
        if candidate.long_leg is None:
            raise ExchangeError("bull put spread entry requires a long_leg")
        return {
            "instrument_name": candidate.long_leg.instrument_name,
            "amount": format_decimal(quantity, 8),
            "price": format_decimal(candidate.long_leg.entry_price, 8),
            "label": label,
            "time_in_force": "immediate_or_cancel",
        }

    def _execute_repriced_naked_short(
        self,
        context: RuntimeContext,
        candidate: NakedPutCandidate,
        *,
        label: str,
        quantity: Decimal,
    ) -> dict[str, Any]:
        requests: list[dict[str, Any]] = []
        responses: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        filled_amount = Decimal("0")
        waited = 0
        latest_candidate: NakedPutCandidate | None = candidate
        locked_short = candidate.short_leg.instrument_name
        reason = "timed_out"
        while filled_amount < quantity and waited < self.config.short_entry_wait_seconds:
            remaining = quantity - filled_amount
            inst = self._find_instrument(context, locked_short)
            book = self._get_orderbook(locked_short, context.orderbook_cache)
            summ = context.summaries.get(candidate.collateral_currency)
            if summ is None:
                reason = "no_summary"
                break
            if (candidate.strategy or "") == "covered_call":
                refreshed, refresh_fail = self.strategy.refresh_covered_call_candidate(
                    instrument=inst,
                    book=book,
                    regime=context.regime_by_currency.get(candidate.currency, RiskRegime.CRISIS),
                    collateral_currency=candidate.collateral_currency,
                    currency=candidate.currency,
                    quantity=remaining,
                    summary_equity=summ.equity,
                )
            else:
                im_by = self._naked_im_by_expiry(
                    context.state,
                    candidate.collateral_currency,
                    orderbook_cache=context.orderbook_cache,
                )
                exp_im = im_by.get(inst.expiration_timestamp_ms, Decimal("0"))
                refreshed, refresh_fail = self.strategy.refresh_naked_candidate(
                    option_type=candidate.option_type,
                    instrument=inst,
                    book=book,
                    regime=context.regime_by_currency.get(candidate.currency, RiskRegime.CRISIS),
                    summary_equity=summ.equity,
                    summary_maintenance_margin=summ.maintenance_margin,
                    collateral_currency=candidate.collateral_currency,
                    currency=candidate.currency,
                    quantity=remaining,
                    existing_im_for_expiry=exp_im,
                )
            if refreshed is None or refreshed.net_apr < self.config.min_net_apr:
                reason = "candidate_failed_recheck"
                if refresh_fail:
                    reason = f"candidate_failed_recheck:{refresh_fail}"
                elif refreshed is not None:
                    reason = "candidate_failed_recheck:net_apr_below_min"
                break
            latest_candidate = refreshed
            aggressive = len(requests) > 0
            request = self._entry_naked_short_request(latest_candidate, label, quantity=remaining, aggressive=aggressive)
            response = self._place_entry_order(context, "sell", request)
            window = min(self.config.order_poll_seconds, self.config.short_entry_wait_seconds - waited)
            state = self._await_entry_order(response, max_wait_seconds=window)
            requests.append(request)
            responses.append(state)
            trades.extend(self._order_trades(state))
            newly_filled = self._response_filled_amount(state)
            filled_amount += newly_filled
            if filled_amount >= quantity:
                reason = "filled"
                break
            waited += window
        if filled_amount <= 0 and reason == "timed_out":
            reason = "unfilled"
        return {
            "candidate": latest_candidate,
            "filled_amount": filled_amount,
            "requests": requests,
            "responses": responses,
            "trades": trades,
            "first_request": requests[0] if requests else None,
            "last_response": responses[-1] if responses else None,
            "reason": reason,
        }

    def _execute_naked_put_entry(
        self,
        context: RuntimeContext,
        candidate: NakedPutCandidate,
        group_id: str,
    ) -> dict[str, Any]:
        labels = self._spread_labels(candidate.currency, group_id)
        execution = self._execute_repriced_naked_short(
            context,
            candidate,
            label=labels["short"],
            quantity=candidate.quantity,
        )
        short_request = execution["first_request"]
        short_state = execution["last_response"]
        if execution["filled_amount"] <= 0:
            return {
                "action": "entry_aborted_short_unfilled",
                "candidate": candidate.to_dict(),
                "requests": {"short_leg": short_request, "short_attempts": execution["requests"]},
                "responses": {"short_leg": short_state, "short_attempts": execution["responses"]},
                "reason": execution["reason"],
            }
        if execution["reason"] == "candidate_failed_recheck":
            return {
                "action": "entry_aborted_naked_disqualified",
                "candidate": candidate.to_dict(),
                "requests": {"short_leg": short_request, "short_attempts": execution["requests"]},
                "responses": {"short_leg": short_state, "short_attempts": execution["responses"]},
                "reason": execution["reason"],
            }
        kept_quantity = execution["filled_amount"]
        final_c = execution["candidate"] or candidate
        primary_short_average_price = self._filled_average_price(execution["responses"])
        short_instrument = self._find_instrument(context, final_c.short_leg.instrument_name)
        short_book = self._get_orderbook(final_c.short_leg.instrument_name, context.orderbook_cache)
        idx = short_book.index_price
        short_trades = execution["trades"]
        short_entry_fee = self._sum_trade_fees_usdc(short_trades)
        if short_entry_fee <= 0:
            short_entry_fee = self._option_fee_usdc(
                premium=primary_short_average_price,
                quantity=kept_quantity,
                index_price=idx,
                base_currency=final_c.currency,
                quote_currency=final_c.short_leg.quote_currency,
                settlement_currency=final_c.short_leg.settlement_currency,
            )
        actual_credit_usdc = self._premium_value_usdc(
            premium=primary_short_average_price,
            quantity=kept_quantity,
            index_price=idx,
            instrument=short_instrument,
        )
        actual_net_credit = actual_credit_usdc - short_entry_fee
        im_per = final_c.estimated_im_total / final_c.quantity if final_c.quantity > 0 else Decimal("0")
        # ``im_per`` is already in the candidate's collateral-currency unit
        # (USDC for linear, BTC/ETH for inverse); store that raw figure so
        # scanner capacity math stays unit-clean. ``max_loss`` meanwhile
        # remains USDC-scale for reporting / risk thresholds.
        estimated_im_collateral = im_per * kept_quantity
        if final_c.collateral_currency.upper() == "USDC":
            max_loss_usdc = estimated_im_collateral
        else:
            max_loss_usdc = estimated_im_collateral * idx if idx > 0 else final_c.estimated_im_total

        group = TradeGroup(
            group_id=group_id,
            currency=final_c.currency,
            collateral_currency=final_c.collateral_currency,
            quantity=kept_quantity,
            entry_timestamp_ms=utc_now_ms(),
            expiration_timestamp_ms=final_c.short_leg.expiration_timestamp_ms,
            short_instrument_name=final_c.short_leg.instrument_name,
            short_strike=final_c.short_leg.strike,
            entry_credit=actual_net_credit,
            original_entry_credit=actual_net_credit,
            max_loss=max_loss_usdc,
            estimated_im_collateral=estimated_im_collateral,
            regime_at_entry=final_c.regime.value,
            entry_fee=short_entry_fee,
            entry_net_apr=final_c.net_apr,
            short_label=labels["short"],
            hedge_label=labels["hedge"] if self.config.enable_perp_hedge else "",
            hedge_instrument_name=self._perp_instrument(final_c.currency) if self.config.enable_perp_hedge else "",
            option_type=final_c.option_type,
            strategy=final_c.strategy or "naked_short",
            covered_underlying_quantity=final_c.covered_underlying_quantity,
        )
        action_name = f"{group.strategy}_entered"
        if group.strategy == "naked_short":
            action_name = f"naked_{final_c.option_type}_entered"
        return {
            "action": action_name,
            "candidate": final_c.to_dict(),
            "group": group,
            "entry_fee": short_entry_fee,
            "execution_mode": "naked_short_post_only" if group.strategy == "naked_short" else group.strategy,
            "requests": {"short_leg": short_request, "short_attempts": execution["requests"]},
            "responses": {"short_leg": short_state, "short_attempts": execution["responses"]},
            "trades": {"short_leg": short_trades},
        }

    def _close_entry_long_remainder(
        self,
        context: RuntimeContext,
        *,
        instrument_name: str,
        quantity: Decimal,
        label: str,
    ) -> dict[str, Any]:
        if quantity <= 0:
            return self._noop_option_order_response()
        instrument = self._find_instrument(context, instrument_name)
        book = self._get_orderbook(instrument_name, context.orderbook_cache)
        amount = align_option_order_amount(quantity, instrument.contract_size, instrument.min_trade_amount)
        if amount <= 0:
            return self._noop_option_order_response()
        return self.client.place_sell_order(
            instrument_name=instrument_name,
            amount=amount,
            label=label,
            order_type="limit",
            price=self._positive_option_limit_price(instrument, self.strategy.close_sell_price(instrument, book)),
            time_in_force="immediate_or_cancel",
            reduce_only=True,
        )

    def _execute_bull_put_spread_entry(
        self,
        context: RuntimeContext,
        candidate: NakedPutCandidate,
        group_id: str,
    ) -> dict[str, Any]:
        if candidate.long_leg is None:
            return {"action": "entry_aborted_missing_long_leg", "candidate": candidate.to_dict()}
        labels = self._spread_labels(candidate.currency, group_id)
        long_request = self._entry_long_buy_request(candidate, labels["long"], quantity=candidate.quantity)
        long_response = self._place_entry_order(context, "buy", long_request)
        long_state = self._await_entry_order(long_response, max_wait_seconds=self.config.order_poll_seconds)
        long_filled = self._response_filled_amount(long_state)
        if long_filled <= 0:
            return {
                "action": "entry_aborted_long_unfilled",
                "candidate": candidate.to_dict(),
                "requests": {"long_leg": long_request},
                "responses": {"long_leg": long_state},
            }

        execution = self._execute_repriced_naked_short(
            context,
            candidate,
            label=labels["short"],
            quantity=min(candidate.quantity, long_filled),
        )
        short_request = execution["first_request"]
        short_state = execution["last_response"]
        short_filled = execution["filled_amount"]
        if short_filled <= 0:
            unwind = self._close_entry_long_remainder(
                context,
                instrument_name=candidate.long_leg.instrument_name,
                quantity=long_filled,
                label=f"{labels['long']}-abort",
            )
            return {
                "action": "entry_aborted_short_unfilled",
                "candidate": candidate.to_dict(),
                "requests": {"long_leg": long_request, "short_leg": short_request, "short_attempts": execution["requests"]},
                "responses": {"long_leg": long_state, "short_leg": short_state, "short_attempts": execution["responses"], "long_unwind": unwind},
                "reason": execution["reason"],
            }

        excess_long = long_filled - short_filled
        long_unwind = None
        if excess_long > 0:
            long_unwind = self._close_entry_long_remainder(
                context,
                instrument_name=candidate.long_leg.instrument_name,
                quantity=excess_long,
                label=f"{labels['long']}-excess",
            )

        kept_quantity = short_filled
        final_c = execution["candidate"] or candidate
        short_instrument = self._find_instrument(context, final_c.short_leg.instrument_name)
        long_instrument = self._find_instrument(context, candidate.long_leg.instrument_name)
        short_book = self._get_orderbook(final_c.short_leg.instrument_name, context.orderbook_cache)
        idx = short_book.index_price
        short_trades = execution["trades"]
        long_trades = self._order_trades(long_state)
        short_avg = self._filled_average_price(execution["responses"])
        long_avg = self._response_average_price(long_state)
        short_fee = self._sum_trade_fees_usdc(short_trades) or self._option_fee_usdc(
            premium=short_avg,
            quantity=kept_quantity,
            index_price=idx,
            base_currency=short_instrument.base_currency,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        long_fee = self._sum_trade_fees_usdc(long_trades) or self._option_fee_usdc(
            premium=long_avg,
            quantity=kept_quantity,
            index_price=idx,
            base_currency=long_instrument.base_currency,
            quote_currency=long_instrument.quote_currency,
            settlement_currency=long_instrument.settlement_currency,
        )
        short_credit = self._premium_value_usdc(
            premium=short_avg,
            quantity=kept_quantity,
            index_price=idx,
            instrument=short_instrument,
        )
        long_debit = self._premium_value_usdc(
            premium=long_avg,
            quantity=kept_quantity,
            index_price=idx,
            instrument=long_instrument,
        )
        actual_net_credit = short_credit - long_debit - short_fee - long_fee
        width_usdc = max(final_c.short_leg.strike - candidate.long_leg.strike, Decimal("0")) * kept_quantity
        max_loss_usdc = max(width_usdc - actual_net_credit, Decimal("0"))
        estimated_im_collateral = (
            max_loss_usdc
            if final_c.collateral_currency.upper() == "USDC"
            else (max_loss_usdc / idx if idx > 0 else Decimal("0"))
        )

        group = TradeGroup(
            group_id=group_id,
            currency=final_c.currency,
            collateral_currency=final_c.collateral_currency,
            quantity=kept_quantity,
            entry_timestamp_ms=utc_now_ms(),
            expiration_timestamp_ms=final_c.short_leg.expiration_timestamp_ms,
            short_instrument_name=final_c.short_leg.instrument_name,
            short_strike=final_c.short_leg.strike,
            entry_credit=actual_net_credit,
            original_entry_credit=actual_net_credit,
            max_loss=max_loss_usdc,
            estimated_im_collateral=estimated_im_collateral,
            regime_at_entry=final_c.regime.value,
            entry_fee=short_fee + long_fee,
            entry_net_apr=final_c.net_apr,
            short_label=labels["short"],
            long_label=labels["long"],
            hedge_label=labels["hedge"] if self.config.enable_perp_hedge else "",
            hedge_instrument_name=self._perp_instrument(final_c.currency) if self.config.enable_perp_hedge else "",
            option_type="put",
            strategy="bull_put_spread",
            long_instrument_name=candidate.long_leg.instrument_name,
            long_strike=candidate.long_leg.strike,
        )
        responses: dict[str, Any] = {"long_leg": long_state, "short_leg": short_state, "short_attempts": execution["responses"]}
        if long_unwind is not None:
            responses["long_unwind"] = long_unwind
        return {
            "action": "bull_put_spread_entered",
            "candidate": final_c.to_dict(),
            "group": group,
            "entry_fee": short_fee + long_fee,
            "execution_mode": "bull_put_spread_buy_protection_first",
            "requests": {"long_leg": long_request, "short_leg": short_request, "short_attempts": execution["requests"]},
            "responses": responses,
            "trades": {"long_leg": long_trades, "short_leg": short_trades},
        }

    def _build_portfolio_snapshot(
        self,
        *,
        state: StrategyState,
        summaries: dict[str, AccountSummary],
        regime_by_currency: dict[str, RiskRegime],
        regime_detail_by_currency: dict[str, tuple[str, ...]],
        future_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> PortfolioSnapshot:
        total_equity_usdc = self._total_equity_usdc(summaries, orderbook_cache)
        per_book_equities = self._book_equities_usdc(summaries, orderbook_cache)
        per_book_native_equities = self._book_equities_native(summaries)
        # Aggregate day_start: prefer the sum of per-book starts (covers the
        # three-book world); fall back to the legacy scalar for v1 state files
        # that still hold only ``day_start_equity_usdc``.
        per_book_day_start: dict[str, Decimal] = {}
        per_book_native_day_start: dict[str, Decimal] = {}
        for book, equity in per_book_equities.items():
            start = state.day_start_equity_by_book.get(book)
            per_book_day_start[book] = start if (start and start > 0) else equity
            native_equity = per_book_native_equities.get(book, Decimal("0"))
            native_start = state.day_start_equity_native_by_book.get(book)
            if native_start and native_start > 0:
                per_book_native_day_start[book] = native_start
            elif book == "USDC":
                per_book_native_day_start[book] = per_book_day_start[book]
            else:
                per_book_native_day_start[book] = native_equity
        if state.day_start_equity_by_book:
            day_start_equity = sum(per_book_day_start.values(), Decimal("0"))
        else:
            day_start_equity = state.day_start_equity_usdc or total_equity_usdc

        # ------------------------------------------------------------------
        # Daily PnL (exclude deposit/withdraw/transfer)
        # ------------------------------------------------------------------
        # ``day_net_flow_usdc_by_book`` is refreshed from Deribit's transaction log
        # and tracks external cash flow since UTC day-start.
        day_net_flow_usdc_by_book: dict[str, Decimal] = {
            book: state.day_net_flow_usdc_by_book.get(book, Decimal("0"))
            for book in per_book_equities
        }
        day_net_flow_native_by_book: dict[str, Decimal] = {
            book: state.day_net_flow_native_by_book.get(book, Decimal("0"))
            for book in per_book_equities
        }
        day_pnl_usdc_ex_flow_by_book: dict[str, Decimal] = {
            book: per_book_equities.get(book, Decimal("0"))
            - per_book_day_start.get(book, Decimal("0"))
            - day_net_flow_usdc_by_book.get(book, Decimal("0"))
            for book in per_book_equities
        }
        day_net_flow_usdc = sum(day_net_flow_usdc_by_book.values(), Decimal("0"))
        day_pnl_usdc_ex_flow = total_equity_usdc - day_start_equity - day_net_flow_usdc
        day_pnl_usdc_ex_flow_ex_spot_by_book: dict[str, Decimal] = {}
        for book in per_book_equities:
            native_equity = per_book_native_equities.get(book, Decimal("0"))
            native_start = per_book_native_day_start.get(book, native_equity)
            native_flow = day_net_flow_native_by_book.get(book, Decimal("0"))
            spot = Decimal("1") if book == "USDC" else self._currency_index_price(book, orderbook_cache)
            day_pnl_usdc_ex_flow_ex_spot_by_book[book] = (
                native_equity - native_start - native_flow
            ) * spot
        day_pnl_usdc_ex_flow_ex_spot = sum(
            day_pnl_usdc_ex_flow_ex_spot_by_book.values(),
            Decimal("0"),
        )
        # Per-book drawdown is the primary gate; aggregate drawdown is kept for
        # reports and is the *worst* book's drawdown so a single breach still
        # shows up without misleading dilution across pools.
        #
        # Drawdown is measured in each book's collateral unit (BTC / ETH /
        # USDC), not USDC-equivalent equity. This keeps spot price moves from
        # tripping inverse-native books such as covered calls.
        #
        # Two corrections vs. a naive (start - now) / start:
        #
        # 1. External cash-flow adjustment. ``day_net_flow_native_by_book`` is
        #    refreshed from Deribit's transaction log each cycle. Adding it
        #    to ``start`` turns the formula into "expected equity if no
        #    trading happened", so a user withdrawal / deposit does not
        #    masquerade as a trading loss.
        #
        # 2. Dust floor. Books that started the day below
        #    ``min_book_equity_usdc`` on the reporting view are excluded
        #    entirely. Without this a few-cent BTC dust balance can produce
        #    >100% phantom drawdowns when the tiny balance is moved around.
        per_book_drawdown: dict[str, Decimal] = {}
        min_equity = self.config.min_book_equity_usdc
        for book, equity in per_book_native_equities.items():
            start_usdc = per_book_day_start.get(book, Decimal("0"))
            if start_usdc <= min_equity:
                continue
            start = per_book_native_day_start.get(book, equity)
            if start <= 0:
                continue
            net_flow = day_net_flow_native_by_book.get(book)
            if net_flow is None:
                net_flow = day_net_flow_usdc_by_book.get(book, Decimal("0")) if book == "USDC" else Decimal("0")
            adjusted_start = start + net_flow
            per_book_drawdown[book] = safe_div(
                max(adjusted_start - equity, Decimal("0")),
                start,
            )
        day_drawdown_pct = max(per_book_drawdown.values()) if per_book_drawdown else Decimal("0")
        effective_capital = self._effective_capital(total_equity_usdc)
        open_max_loss = self._open_max_loss(state)
        open_max_loss_pct = safe_div(open_max_loss, effective_capital)
        initial_margin_usdc = self._aggregate_margin(summaries, orderbook_cache, margin_kind="initial")
        maintenance_margin_usdc = self._aggregate_margin(summaries, orderbook_cache, margin_kind="maintenance")
        initial_margin_ratio = safe_div(initial_margin_usdc, total_equity_usdc)
        maintenance_margin_ratio = safe_div(maintenance_margin_usdc, total_equity_usdc)
        projected_max_profit_run_rate = self._projected_max_profit_run_rate(state)
        projected_max_profit_apr = safe_div(projected_max_profit_run_rate, total_equity_usdc)
        target_annual_pnl = effective_capital * self.config.target_portfolio_apr
        target_progress_ratio = safe_div(projected_max_profit_run_rate, target_annual_pnl)
        overall_regime = self._overall_regime(regime_by_currency.values())
        now_ms = utc_now_ms()
        # Per-book cooldown: keep legacy aggregate read as the portfolio-wide
        # fallback, but prefer book-specific values when present.
        cooldown_by_book: dict[str, int | None] = {
            book: state.cooldown_until_ms_by_book.get(book) for book in per_book_equities
        }
        cooling_by_book: dict[str, bool] = {
            book: bool(ts and ts > now_ms) for book, ts in cooldown_by_book.items()
        }
        legacy_cooling = bool(state.cooldown_until_ms and state.cooldown_until_ms > now_ms)
        cooling_down = legacy_cooling or any(cooling_by_book.values())
        open_groups = self._open_groups(state)
        crisis_open_group = any(regime_by_currency.get(group.currency, RiskRegime.CRISIS) is RiskRegime.CRISIS for group in open_groups)
        crisis_derisk = self.config.hard_derisk_on_crisis_open_group and crisis_open_group
        hard_stop_groups = (
            [group for group in open_groups if not self._is_covered_call_group(group)]
            if self._is_covered_call_strategy()
            else open_groups
        )
        hard_stop_open_group = any(
            group.short_delta >= self._defense_delta_thresholds(group)[1]
            or group.loss_pct_of_max_loss >= self.config.hard_stop_loss_pct
            for group in hard_stop_groups
        )
        per_currency_ratios = self._per_currency_margin_ratios(summaries)
        # Per-book gates. A single book breaching its hard IM/MM ceiling or its
        # own drawdown floor halts that book only — the other books stay live.
        hard_derisk_by_book: dict[str, bool] = {book: False for book in per_book_equities}
        halt_entries_by_book: dict[str, bool] = {book: False for book in per_book_equities}
        halt_reasons_by_book: dict[str, list[str]] = {book: [] for book in per_book_equities}
        book_hard_breaches: list[str] = []

        for book in per_book_equities:
            shielded = self._covered_call_book_fully_collateralized(state, summaries, book)
            dd = per_book_drawdown.get(book, Decimal("0"))
            if not shielded and dd >= self.config.hard_derisk_drawdown_pct:
                hard_derisk_by_book[book] = True
                halt_reasons_by_book[book].append(
                    f"hard_derisk: day_drawdown_pct >= hard_derisk_drawdown_pct "
                    f"({format_decimal(dd, 8)} >= {format_decimal(self.config.hard_derisk_drawdown_pct, 6)})"
                )
            if not shielded and dd >= self.config.halt_drawdown_pct:
                halt_entries_by_book[book] = True
                halt_reasons_by_book[book].append(
                    f"day_drawdown_pct >= halt_drawdown_pct "
                    f"({format_decimal(dd, 8)} >= {format_decimal(self.config.halt_drawdown_pct, 6)})"
                )
            if cooling_by_book.get(book) and not shielded:
                halt_entries_by_book[book] = True
                halt_reasons_by_book[book].append("cooldown_active")

        for collateral_ccy, (book_im, book_mm) in per_currency_ratios.items():
            if self._covered_call_book_im_mm_shielded(
                state,
                summaries,
                collateral_ccy,
                available_cover=self._available_covered_call_quantity_from_summaries(
                    state, summaries, collateral_ccy
                ),
            ):
                continue
            if book_im >= self.config.book_im_hard:
                breach = (
                    f"{collateral_ccy}: im_ratio>=book_im_hard "
                    f"({format_decimal(book_im, 8)}>={format_decimal(self.config.book_im_hard, 6)})"
                )
                book_hard_breaches.append(breach)
                hard_derisk_by_book[collateral_ccy] = True
                halt_entries_by_book[collateral_ccy] = True
                halt_reasons_by_book.setdefault(collateral_ccy, []).append(
                    f"hard_derisk: book {breach}"
                )
            if book_mm >= self.config.book_mm_hard:
                breach = (
                    f"{collateral_ccy}: mm_ratio>=book_mm_hard "
                    f"({format_decimal(book_mm, 8)}>={format_decimal(self.config.book_mm_hard, 6)})"
                )
                book_hard_breaches.append(breach)
                hard_derisk_by_book[collateral_ccy] = True
                halt_entries_by_book[collateral_ccy] = True
                halt_reasons_by_book.setdefault(collateral_ccy, []).append(
                    f"hard_derisk: book {breach}"
                )

        book_hard_derisk = bool(book_hard_breaches) or any(hard_derisk_by_book.values())
        hard_derisk = book_hard_derisk or crisis_derisk or hard_stop_open_group
        # When macro feeds are unavailable `_determine_regime_with_detail` returns
        # ELEVATED with a detail line prefixed "data_unavailable". Treat that as a
        # halt signal so we don't open new risk while blind; but leave hard_derisk
        # clear so existing positions aren't liquidated on a data blip.
        data_unavailable_regime = any(
            any(note.startswith("data_unavailable") for note in regime_detail_by_currency.get(currency, ()))
            for currency in regime_by_currency
        )
        non_normal_regime = any(
            regime is not RiskRegime.NORMAL for regime in regime_by_currency.values()
        )
        halt_new_entries = (
            cooling_down
            or open_max_loss_pct >= self.config.halt_open_max_loss_pct
            or hard_derisk
            or non_normal_regime
            or data_unavailable_regime
            or any(halt_entries_by_book.values())
        )
        halt_entry_reasons: list[str] = []
        if legacy_cooling:
            halt_entry_reasons.append("cooldown_active")
        if open_max_loss_pct >= self.config.halt_open_max_loss_pct:
            halt_entry_reasons.append(
                f"open_max_loss_pct >= halt_open_max_loss_pct "
                f"({format_decimal(open_max_loss_pct, 8)} >= {format_decimal(self.config.halt_open_max_loss_pct, 6)})"
            )
        if crisis_derisk:
            halt_entry_reasons.append("hard_derisk: open_trade_group_in_crisis_regime_currency")
        if hard_stop_open_group:
            halt_entry_reasons.append("hard_derisk: open_group_hard_defense_or_stop_trigger")
        # Surface per-book halts so log lines still show which book triggered.
        for book in sorted(halt_entries_by_book):
            for reason in halt_reasons_by_book.get(book, []):
                prefixed = f"book={book} {reason}"
                if prefixed not in halt_entry_reasons:
                    halt_entry_reasons.append(prefixed)
        if data_unavailable_regime:
            affected = [
                currency
                for currency in sorted(regime_by_currency)
                if any(note.startswith("data_unavailable") for note in regime_detail_by_currency.get(currency, ()))
            ]
            halt_entry_reasons.append(
                "regime data_unavailable: " + ", ".join(affected)
            )
        elif non_normal_regime:
            escalated = [
                f"{currency}={regime.value}"
                for currency, regime in sorted(regime_by_currency.items())
                if regime is not RiskRegime.NORMAL
            ]
            halt_entry_reasons.append("regime non_normal: " + ", ".join(escalated))
        if halt_new_entries and not halt_entry_reasons:
            halt_entry_reasons.append("halt_new_entries (composite; check portfolio flags)")
        return PortfolioSnapshot(
            total_equity_usdc=total_equity_usdc,
            day_start_equity_usdc=day_start_equity,
            day_net_flow_usdc=day_net_flow_usdc,
            day_pnl_usdc_ex_flow=day_pnl_usdc_ex_flow,
            day_drawdown_pct=day_drawdown_pct,
            open_max_loss=open_max_loss,
            open_max_loss_pct=open_max_loss_pct,
            initial_margin_ratio=initial_margin_ratio,
            maintenance_margin_ratio=maintenance_margin_ratio,
            projected_max_profit_run_rate_usdc=projected_max_profit_run_rate,
            projected_max_profit_apr=projected_max_profit_apr,
            target_progress_ratio=target_progress_ratio,
            regime=overall_regime,
            halt_new_entries=halt_new_entries,
            hard_derisk=hard_derisk,
            cooldown_until_ms=state.cooldown_until_ms,
            cooling_down=cooling_down,
            delta_totals_by_currency=self._delta_totals_by_currency(summaries, state, future_positions),
            regime_by_currency=regime_by_currency,
            halt_entry_reasons=tuple(halt_entry_reasons),
            regime_detail_by_currency=regime_detail_by_currency,
            margin_ratios_by_currency=per_currency_ratios,
            equity_by_book=per_book_equities,
            day_start_equity_by_book=per_book_day_start,
            day_net_flow_usdc_by_book=day_net_flow_usdc_by_book,
            day_pnl_usdc_ex_flow_by_book=day_pnl_usdc_ex_flow_by_book,
            day_pnl_usdc_ex_flow_ex_spot=day_pnl_usdc_ex_flow_ex_spot,
            day_pnl_usdc_ex_flow_ex_spot_by_book=day_pnl_usdc_ex_flow_ex_spot_by_book,
            day_drawdown_pct_by_book=per_book_drawdown,
            cooldown_until_ms_by_book=cooldown_by_book,
            cooling_down_by_book=cooling_by_book,
            hard_derisk_by_book=hard_derisk_by_book,
            halt_entries_by_book=halt_entries_by_book,
            halt_entry_reasons_by_book={
                book: tuple(reasons) for book, reasons in halt_reasons_by_book.items()
            },
        )

    def _determine_regime_with_detail(
        self,
        currency: str,
        *,
        markets: list[OptionInstrument],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> tuple[RiskRegime, list[str]]:
        if not markets:
            return RiskRegime.CRISIS, ["no_option_markets_loaded_for_currency"]
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
        ok, liq_notes = self.strategy.core_naked_liquidity_detail(currency, markets, loader)
        if not ok:
            return RiskRegime.CRISIS, ["naked_core_liquidity_check_failed", *liq_notes]

        drawdown = self._index_drawdown_24h(currency)
        dvol_ratio = self._dvol_ratio(currency)

        # If any of the macro feeds is unavailable, do NOT flip to crisis. Instead,
        # fall back to the last cached regime (if any), otherwise hold at elevated
        # so that new entries are halted but open positions are not force-derisked.
        if drawdown is None or dvol_ratio is None:
            missing = []
            if drawdown is None:
                missing.append("index_chart_data")
            if dvol_ratio is None:
                missing.append("volatility_index_data")
            cached = self._last_regime_cache.get(currency)
            if cached is not None:
                cached_regime, _ = cached
                return cached_regime, [
                    f"data_unavailable({','.join(missing)}); using cached regime={cached_regime.value}",
                ]
            return RiskRegime.ELEVATED, [
                f"data_unavailable({','.join(missing)}); defaulting to elevated (halt new entries)",
            ]

        if drawdown <= -self.config.index_drawdown_crisis_pct:
            regime = RiskRegime.CRISIS
            detail = [
                f"index_24h_drawdown <= -index_drawdown_crisis_pct "
                f"({format_decimal(drawdown, 8)} <= -{format_decimal(self.config.index_drawdown_crisis_pct, 6)})",
            ]
        elif dvol_ratio > self.config.dvol_crisis_multiplier:
            regime = RiskRegime.CRISIS
            detail = [
                f"dvol_ratio > dvol_crisis_multiplier "
                f"({format_decimal(dvol_ratio, 6)} > {format_decimal(self.config.dvol_crisis_multiplier, 6)})",
            ]
        elif drawdown <= -self.config.index_drawdown_elevated_pct or dvol_ratio > self.config.dvol_elevated_multiplier:
            regime = RiskRegime.ELEVATED
            detail = [
                f"elevated: drawdown={format_decimal(drawdown, 8)} "
                f"dvol_ratio={format_decimal(dvol_ratio, 6)} "
                f"(thresholds -elevated {format_decimal(self.config.index_drawdown_elevated_pct, 6)} / {format_decimal(self.config.dvol_elevated_multiplier, 6)})",
            ]
        else:
            regime = RiskRegime.NORMAL
            detail = ["market_conditions_normal"]

        self._last_regime_cache[currency] = (regime, utc_now_ms())
        return regime, detail

    def _determine_regime(
        self,
        currency: str,
        *,
        markets: list[OptionInstrument],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> RiskRegime:
        regime, _ = self._determine_regime_with_detail(currency, markets=markets, orderbook_cache=orderbook_cache)
        return regime

    def _build_hedge_plan(self, context: RuntimeContext, currency: str, *, mode: str) -> HedgePlan | None:
        current_delta = context.snapshot.delta_totals_by_currency.get(currency, Decimal("0"))
        index_price = self._currency_index_price(currency, context.orderbook_cache)
        if index_price <= 0:
            return None
        effective_capital = self._effective_capital(context.snapshot.total_equity_usdc)
        target_pct = self.config.hard_hedge_delta_cap_pct if mode == "hard" else self.config.soft_hedge_delta_cap_pct
        target_cap_base = (effective_capital * target_pct) / index_price
        current_hedge = self._current_hedge_base(context.future_positions, currency)
        option_delta = current_delta - current_hedge
        if option_delta > target_cap_base:
            target_hedge_base = target_cap_base - option_delta
        elif option_delta < -target_cap_base:
            target_hedge_base = -target_cap_base - option_delta
        else:
            return None
        delta_change_base = target_hedge_base - current_hedge
        if abs(delta_change_base) <= Decimal("0.0001"):
            return None
        side = "sell" if delta_change_base < 0 else "buy"
        order_amount = self._align_future_order_amount(
            context,
            instrument_name=self._perp_instrument(currency),
            amount=abs(delta_change_base) * index_price,
        )
        if order_amount <= 0:
            return None
        delta_change_base = order_amount / index_price
        if side == "sell":
            delta_change_base *= Decimal("-1")
        target_hedge_base = current_hedge + delta_change_base
        return HedgePlan(
            currency=currency,
            mode=mode,
            instrument_name=self._perp_instrument(currency),
            side=side,
            delta_change_base=delta_change_base,
            order_amount=order_amount,
            target_delta_cap_base=target_cap_base,
            current_delta_base=current_delta,
            current_hedge_base=current_hedge,
            target_hedge_base=target_hedge_base,
            note=f"{mode}_hedge",
        )

    def _execute_hedge_plan(self, context: RuntimeContext, plan: HedgePlan, *, live: bool) -> dict[str, Any]:
        payload = {"action": "hedge", "plan": plan.to_dict(), "live": live}
        if not live:
            return payload
        response = self.client.place_order(
            direction=plan.side,
            instrument_name=plan.instrument_name,
            amount=plan.order_amount,
            label=self._hedge_label(plan.currency, plan.mode),
            order_type="market",
            reduce_only=plan.side == "buy" and plan.current_hedge_base < 0,
        )
        payload["response"] = response
        return payload

    def _close_perp_position(self, position: Position, *, live: bool) -> dict[str, Any] | None:
        if position.size == 0:
            return None
        if not live:
            return {"action": "close_perp_preview", "instrument_name": position.instrument_name, "direction": position.direction}
        response = self.client.close_position(position.instrument_name, order_type="market")
        return {"action": "close_perp", "instrument_name": position.instrument_name, "response": response}

    def _place_entry_order(self, context: RuntimeContext, direction: str, request: dict[str, Any]) -> dict[str, Any]:
        instrument = self._find_instrument(context, str(request["instrument_name"]))
        amount = align_option_order_amount(
            to_decimal(request["amount"]),
            instrument.contract_size,
            instrument.min_trade_amount,
        )
        if amount <= 0:
            raise ExchangeError(
                f"entry order amount aligned to zero for {request['instrument_name']}; "
                f"requested quantity is below exchange minimum/step"
            )
        if "reject_post_only" in request:
            reject_post_only = request["reject_post_only"]
        else:
            reject_post_only = False if request.get("post_only") is not None else None
        return self.client.place_order(
            direction=direction,
            instrument_name=request["instrument_name"],
            amount=amount,
            label=request["label"],
            order_type="limit",
            price=to_decimal(request["price"]),
            time_in_force=request["time_in_force"],
            post_only=request.get("post_only"),
            reject_post_only=reject_post_only,
            reduce_only=False,
        )

    def _option_exit_request(
        self,
        *,
        instrument: OptionInstrument,
        book: OrderBookSnapshot,
        label: str,
        quantity: Decimal,
        passive: bool,
    ) -> dict[str, Any]:
        if passive:
            ask_price = book.best_ask_price
            if ask_price <= 0:
                bid_price = book.best_bid_price
                step = instrument.tick_size_for_price(bid_price) if bid_price > 0 else instrument.tick_size
                ask_price = bid_price + step if bid_price > 0 else instrument.tick_size
            return {
                "instrument_name": instrument.instrument_name,
                "amount": format_decimal(quantity, 8),
                "price": format_decimal(ask_price, 8),
                "label": label,
                "time_in_force": "good_til_cancelled",
                "post_only": True,
            }
        bid_price = book.best_bid_price if book.best_bid_price > 0 else instrument.tick_size
        return {
            "instrument_name": instrument.instrument_name,
            "amount": format_decimal(quantity, 8),
            "price": format_decimal(bid_price, 8),
            "label": label,
            "time_in_force": "immediate_or_cancel",
        }

    def _place_option_exit_order(self, context: RuntimeContext, request: dict[str, Any]) -> dict[str, Any]:
        instrument_name = str(request["instrument_name"])
        instrument = self._find_instrument(context, instrument_name)
        raw = to_decimal(request["amount"])
        capacity = self._option_reduce_only_capacity(
            instrument_name, "sell", option_positions=context.option_positions
        )
        capped = min(raw, capacity)
        amount = align_option_order_amount(capped, instrument.contract_size, instrument.min_trade_amount)
        if amount <= 0:
            LOGGER.warning(
                "skip option exit sell on %s (requested=%s aligned_cap=%s exchange_long=%s)",
                instrument_name,
                raw,
                capped,
                capacity,
            )
            return self._noop_option_order_response()
        return self.client.place_sell_order(
            instrument_name=instrument_name,
            amount=amount,
            label=request["label"],
            order_type="limit",
            price=to_decimal(request["price"]),
            time_in_force=request["time_in_force"],
            post_only=request.get("post_only"),
            reject_post_only=False if request.get("post_only") is not None else None,
            reduce_only=True,
        )

    def _entry_leg_instrument(self, currency: str, leg: SpreadLeg, quantity: Decimal) -> OptionInstrument:
        """Reconstruct a minimal ``OptionInstrument`` for repricing an entry leg.

        ``option_type`` must be inferred from the leg's instrument name (``-P`` /
        ``-C``) instead of being hardcoded to ``put``; otherwise short call
        re-prices would surface with the wrong type and break tick-grid lookups.
        """
        option_type = "call" if leg.instrument_name.upper().endswith("-C") else "put"
        return OptionInstrument(
            instrument_name=leg.instrument_name,
            base_currency=currency,
            quote_currency=leg.quote_currency,
            settlement_currency=leg.settlement_currency,
            instrument_type=leg.instrument_type,
            tick_size=leg.tick_size,
            tick_size_steps=leg.tick_size_steps,
            min_trade_amount=leg.min_trade_amount,
            contract_size=leg.contract_size,
            option_type=option_type,
            expiration_timestamp_ms=leg.expiration_timestamp_ms,
            strike=leg.strike,
            instrument_state="open",
        )

    def _filled_amounts_by_instrument(self, responses: list[dict[str, Any]]) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for response in responses:
            order = self._response_order(response)
            instrument_name = str(order.get("instrument_name") or "")
            if not instrument_name:
                continue
            totals[instrument_name] = totals.get(instrument_name, Decimal("0")) + self._response_filled_amount(response)
        return {instrument_name: amount for instrument_name, amount in totals.items() if amount > 0}

    def _account_summaries_by_currency(self) -> dict[str, AccountSummary]:
        if not self.config.has_private_credentials:
            return {}
        return {
            row.currency: row
            for row in (AccountSummary.from_api(item) for item in self.client.get_account_summaries(extended=True))
            if row.currency
        }

    def _load_supported_option_markets(self) -> dict[str, list[OptionInstrument]]:
        managed = set(self.config.managed_currencies)
        if self.config.option_markets_profile == "linear_usdc":
            markets_usdc: dict[str, list[OptionInstrument]] = {c: [] for c in self.config.managed_currencies}
            for market in (
                OptionInstrument.from_api(row)
                for row in self.client.get_instruments("USDC", kind="option", expired=False)
            ):
                if market.base_currency in managed and self._supports_option_market(market):
                    markets_usdc[market.base_currency].append(market)
            return markets_usdc

        linear_markets = [
            OptionInstrument.from_api(row)
            for row in self.client.get_instruments("USDC", kind="option", expired=False)
        ]
        linear_by_currency: dict[str, list[OptionInstrument]] = {currency: [] for currency in self.config.managed_currencies}
        for market in linear_markets:
            if market.base_currency in managed and self._supports_option_market(market):
                linear_by_currency[market.base_currency].append(market)

        markets_by_currency: dict[str, list[OptionInstrument]] = {}
        for currency in self.config.managed_currencies:
            inverse_markets = [
                OptionInstrument.from_api(row)
                for row in self.client.get_instruments(currency, kind="option", expired=False)
            ]
            combined: dict[str, OptionInstrument] = {}
            for market in inverse_markets + linear_by_currency.get(currency, []):
                if self._supports_option_market(market):
                    combined[market.instrument_name] = market
            markets_by_currency[currency] = list(combined.values())
        return markets_by_currency

    def _load_perpetual_markets(self) -> dict[str, OptionInstrument]:
        markets: dict[str, OptionInstrument] = {}
        for currency in self.config.managed_currencies:
            perp_name = self._perp_instrument(currency)
            for row in self.client.get_instruments(currency, kind="future", expired=False):
                instrument = OptionInstrument.from_api(row)
                if instrument.instrument_name == perp_name:
                    markets[instrument.instrument_name] = instrument
                    break
        return markets

    def _get_orderbook(self, instrument_name: str, cache: dict[str, OrderBookSnapshot]) -> OrderBookSnapshot:
        if instrument_name not in cache:
            cache[instrument_name] = OrderBookSnapshot.from_api(self.client.get_order_book(instrument_name))
        return cache[instrument_name]

    def _supports_option_market(self, market: OptionInstrument) -> bool:
        if self.config.option_markets_profile == "linear_usdc":
            return (
                market.quote_currency == "USDC"
                and market.settlement_currency == "USDC"
                and market.base_currency in self.config.managed_currencies
            )
        if self.config.option_markets_profile == "inverse_native" and market.quote_currency == "USDC" and market.settlement_currency == "USDC":
            return False
        if market.quote_currency == "USDC" and market.settlement_currency == "USDC":
            return True
        return (
            market.instrument_type == "reversed"
            or (
                market.base_currency
                and market.quote_currency in {"", market.base_currency}
                and market.settlement_currency == market.base_currency
            )
        )

    def _find_instrument(self, context: RuntimeContext, instrument_name: str) -> OptionInstrument:
        return self._find_or_fetch_instrument(context.markets_by_currency, instrument_name)

    def _align_future_order_amount(self, context: RuntimeContext, *, instrument_name: str, amount: Decimal) -> Decimal:
        instrument = context.future_markets_by_name.get(instrument_name)
        if instrument is None:
            LOGGER.warning("missing future instrument metadata for %s; skipping amount=%s", instrument_name, amount)
            return Decimal("0")
        return align_option_order_amount(amount, instrument.contract_size, instrument.min_trade_amount)

    @staticmethod
    def _find_instrument_by_markets(
        markets_by_currency: dict[str, list[OptionInstrument]],
        instrument_name: str,
    ) -> OptionInstrument:
        for markets in markets_by_currency.values():
            for instrument in markets:
                if instrument.instrument_name == instrument_name:
                    return instrument
        raise KeyError(f"Missing instrument metadata for {instrument_name}")

    def _find_or_fetch_instrument(
        self,
        markets_by_currency: dict[str, list[OptionInstrument]],
        instrument_name: str,
    ) -> OptionInstrument:
        try:
            return self._find_instrument_by_markets(markets_by_currency, instrument_name)
        except KeyError:
            pass

        cached = self._instrument_metadata_cache.get(instrument_name)
        if cached is not None:
            return cached

        instrument = self._fetch_option_instrument_metadata(instrument_name)
        if instrument is None:
            raise KeyError(f"Missing instrument metadata for {instrument_name}")
        self._instrument_metadata_cache[instrument_name] = instrument
        LOGGER.info("loaded exact instrument metadata for profile-filtered instrument %s", instrument_name)
        return instrument

    def _fetch_option_instrument_metadata(self, instrument_name: str) -> OptionInstrument | None:
        try:
            row = self.client.get_instrument(instrument_name)
        except Exception as exc:
            LOGGER.warning("exact instrument metadata lookup failed for %s (%s)", instrument_name, exc)
        else:
            if isinstance(row, dict) and str(row.get("instrument_name") or "") == instrument_name:
                return OptionInstrument.from_api(row)

        for currency in self._instrument_metadata_lookup_currencies(instrument_name):
            try:
                rows = self.client.get_instruments(currency, kind="option", expired=False)
            except Exception as exc:
                LOGGER.warning("instrument metadata lookup failed for %s via %s (%s)", instrument_name, currency, exc)
                continue
            for row in rows:
                if str(row.get("instrument_name") or "") == instrument_name:
                    return OptionInstrument.from_api(row)
        return None

    def _instrument_metadata_lookup_currencies(self, instrument_name: str) -> tuple[str, ...]:
        parsed = parse_option_name(instrument_name) or {}
        candidates: list[str] = []
        quote = str(parsed.get("quote_currency") or "").upper()
        base = str(parsed.get("base_currency") or "").upper()
        if quote:
            candidates.append(quote)
        if base:
            candidates.append(base)
        candidates.extend(self.config.managed_currencies)
        candidates.append("USDC")

        seen: set[str] = set()
        ordered: list[str] = []
        for currency in candidates:
            currency = (currency or "").upper()
            if not currency or currency in seen:
                continue
            seen.add(currency)
            ordered.append(currency)
        return tuple(ordered)

    def _await_entry_order(self, initial_response: dict[str, Any] | None, *, max_wait_seconds: int | None = None) -> dict[str, Any]:
        if not isinstance(initial_response, dict):
            return {}
        order = self._response_order(initial_response)
        order_id = str(order.get("order_id") or "")
        state = initial_response
        waited = 0
        wait_limit = self.config.short_entry_wait_seconds if max_wait_seconds is None else max_wait_seconds
        while order_id and waited < wait_limit:
            order = self._response_order(state)
            order_state = str(order.get("order_state") or "").lower()
            if order_state in {"filled", "cancelled", "rejected"}:
                break
            step = min(self.config.order_poll_seconds, wait_limit - waited)
            if step <= 0:
                break
            self.sleep_fn(step)
            waited += step
            state = self.client.get_order_state(order_id)

        final_order = self._response_order(state)
        final_state = str(final_order.get("order_state") or "").lower()
        if order_id and final_state not in {"filled", "cancelled", "rejected"}:
            self.client.cancel_order(order_id)
            state = self.client.get_order_state(order_id)
        return state

    @staticmethod
    def _premium_value_usdc(
        *,
        premium: Decimal,
        quantity: Decimal,
        index_price: Decimal,
        instrument: OptionInstrument,
    ) -> Decimal:
        return premium_value_usdc(
            index_price=index_price,
            premium=premium,
            quantity=quantity,
            base_currency=instrument.base_currency,
            quote_currency=instrument.quote_currency,
            settlement_currency=instrument.settlement_currency,
        )

    def _option_fee_usdc(
        self,
        *,
        premium: Decimal,
        quantity: Decimal,
        index_price: Decimal,
        base_currency: str,
        quote_currency: str,
        settlement_currency: str,
    ) -> Decimal:
        return option_trade_fee_usdc(
            index_price=index_price,
            premium=premium,
            quantity=quantity,
            fee_rate=self.config.option_fee_rate,
            fee_cap_rate=self.config.option_fee_cap_rate,
            base_currency=base_currency,
            quote_currency=quote_currency,
            settlement_currency=settlement_currency,
        )

    def _sum_trade_fees_usdc(self, trades: list[dict[str, Any]]) -> Decimal:
        total = Decimal("0")
        for trade in trades:
            fee = to_decimal(trade.get("fee"))
            if fee <= 0:
                continue
            fee_currency = str(trade.get("fee_currency") or "").upper()
            if fee_currency == "USDC":
                total += fee
                continue
            if fee_currency in self.config.managed_currencies:
                total += fee * to_decimal(trade.get("index_price") or trade.get("underlying_price"))
                continue
            total += fee
        return total

    def _order_trades(self, response: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(response, dict):
            return []
        trades = list(response.get("trades") or [])
        if trades:
            return trades
        order = self._response_order(response)
        order_id = str(order.get("order_id") or "")
        if not order_id:
            return []
        return self.client.get_user_trades_by_order(order_id)

    def _summary_equity_usdc(
        self,
        summary: AccountSummary | None,
        currency: str,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> Decimal:
        if summary is None:
            return Decimal("0")
        if currency == "USDC":
            return summary.equity
        if currency in self.config.managed_currencies:
            return summary.equity * self._currency_index_price(currency, orderbook_cache)
        return Decimal("0")

    def _collateral_capital_base_usdc(
        self,
        summaries: dict[str, AccountSummary],
        collateral_currency: str,
        orderbook_cache: dict[str, OrderBookSnapshot],
        *,
        total_equity_usdc: Decimal | None = None,
    ) -> Decimal:
        effective_cap = self._effective_capital(total_equity_usdc) if total_equity_usdc is not None else self.config.reference_capital_usdc
        return min(
            effective_cap,
            self._summary_equity_usdc(summaries.get(collateral_currency), collateral_currency, orderbook_cache),
        )

    def _open_groups(self, state: StrategyState) -> list[TradeGroup]:
        return [group for group in state.groups if group.status == "open"]

    @staticmethod
    def _short_option_open_size(position: Position) -> Decimal | None:
        """Absolute open size (contracts) for a short single option, or ``None``.

        Deribit often uses ``direction == "sell"`` with **negative** ``size`` for shorts; older code
        required ``size > 0``, which skipped every real short leg and broke adoption / reconcile.
        """
        if position.kind != "option" or position.size == 0:
            return None
        if position.direction == "sell":
            return abs(position.size)
        if position.size < 0:
            return abs(position.size)
        return None

    @staticmethod
    def _long_option_open_size(position: Position) -> Decimal | None:
        """Absolute open size for a long option position, or ``None``."""
        if position.kind != "option" or position.size == 0:
            return None
        if position.direction == "buy":
            return abs(position.size)
        return None

    def _active_scan_strategy_keys(self) -> tuple[str, ...]:
        if self.config.option_strategy == "covered_call":
            return ("covered_call",)
        if self.config.option_strategy == "bull_put_spread":
            return ("bull_put_spread",)
        if self.config.enable_short_put or self.config.enable_short_call:
            return ("naked_short",)
        return ()

    @staticmethod
    def _group_strategy_key(group: TradeGroup) -> str:
        return normalize_strategy_name(group.strategy, default="naked_short")

    def _open_group_count_for_strategy(self, state: StrategyState, strategy: str) -> int:
        return len(
            [
                group
                for group in self._open_groups(state)
                if self._group_strategy_key(group) == strategy
            ]
        )

    def _open_group_count_for_currency(
        self,
        state: StrategyState,
        currency: str,
        *,
        strategy: str | None = None,
    ) -> int:
        ccy = currency.upper()
        return len(
            [
                group
                for group in self._open_groups(state)
                if group.currency == ccy
                and (strategy is None or self._group_strategy_key(group) == strategy)
            ]
        )

    def _strategy_at_concurrent_limit(self, state: StrategyState, strategy: str) -> bool:
        return (
            self.config.max_concurrent_groups > 0
            and self._open_group_count_for_strategy(state, strategy) >= self.config.max_concurrent_groups
        )

    def _strategy_at_currency_limit(self, state: StrategyState, strategy: str, currency: str) -> bool:
        return (
            self.config.max_groups_per_currency > 0
            and self._open_group_count_for_currency(state, currency, strategy=strategy)
            >= self.config.max_groups_per_currency
        )

    def _reserved_covered_call_quantity(self, state: StrategyState, currency: str) -> Decimal:
        ccy = currency.upper()
        return sum(
            (
                group.covered_underlying_quantity
                for group in self._open_groups(state)
                if group.currency == ccy and (group.strategy or "") == "covered_call"
            ),
            Decimal("0"),
        )

    def _available_covered_call_quantity(self, context: RuntimeContext, currency: str) -> Decimal:
        return self._available_covered_call_quantity_from_summaries(
            context.state,
            context.summaries,
            currency,
        )

    def _naked_candidate_matches_open_group(self, state: StrategyState, candidate: NakedPutCandidate) -> bool:
        for group in self._open_groups(state):
            if group.short_instrument_name == candidate.short_leg.instrument_name:
                return True
        return False

    def _group_collateral_currency(self, group: TradeGroup) -> str:
        return group.collateral_currency or ("USDC" if "_USDC-" in group.short_instrument_name else group.currency)

    def _open_max_loss(self, state: StrategyState, *, collateral_currency: str | None = None) -> Decimal:
        return sum(
            (
                group.max_loss
                for group in self._open_groups(state)
                if collateral_currency is None or self._group_collateral_currency(group) == collateral_currency
            ),
            Decimal("0"),
        )

    def _projected_max_profit_run_rate(self, state: StrategyState, *, collateral_currency: str | None = None) -> Decimal:
        return sum(
            (
                self._max_profit_run_rate_for_group(group)
                for group in self._open_groups(state)
                if collateral_currency is None or self._group_collateral_currency(group) == collateral_currency
            ),
            Decimal("0"),
        )

    def _max_profit_run_rate_for_group(self, group: TradeGroup) -> Decimal:
        dte = group.dte_days
        if dte <= 0:
            return Decimal("0")
        return group.entry_credit * (Decimal("365") / dte)

    def _remaining_max_profit_run_rate_for_group(self, group: TradeGroup) -> Decimal:
        dte = group.dte_days
        if dte <= 0:
            return Decimal("0")
        remaining_credit = max(group.entry_credit - group.current_debit, Decimal("0"))
        return remaining_credit * (Decimal("365") / dte)

    def _next_group_id(self, state: StrategyState) -> str:
        value = state.next_group_id
        state.next_group_id += 1
        return f"{value:04d}"

    def _mark_group_closed(
        self,
        group: TradeGroup,
        *,
        reason: str,
        closed_timestamp_ms: int,
        realized_close_debit: Decimal | None = None,
        realized_close_fee: Decimal | None = None,
        realized_pnl: Decimal | None = None,
        realized_return_on_max_loss: Decimal | None = None,
        realized_annualized_return: Decimal | None = None,
    ) -> None:
        group.status = "closed"
        group.last_action = reason
        group.close_reason = reason
        group.closed_timestamp_ms = closed_timestamp_ms
        group.realized_close_debit = realized_close_debit
        group.realized_close_fee = realized_close_fee
        group.realized_pnl = realized_pnl
        group.realized_return_on_max_loss = realized_return_on_max_loss
        group.realized_annualized_return = realized_annualized_return

    def _spread_labels(self, currency: str, group_id: str) -> dict[str, str]:
        prefix = self.config.order_label_prefix
        lower = currency.lower()
        return {
            "long": f"{prefix}-spread-{lower}-{group_id}-long",
            "short": f"{prefix}-spread-{lower}-{group_id}-short",
            "hedge": f"{prefix}-hedge-{lower}-{group_id}",
        }

    def _hedge_label(self, currency: str, suffix: str) -> str:
        return f"{self.config.order_label_prefix}-hedge-{currency.lower()}-{suffix}"

    def _perp_instrument(self, currency: str) -> str:
        return f"{currency.upper()}-PERPETUAL"

    def _day_start_ms_from_key(self, day_key: str) -> int:
        """Convert a ``YYYY-MM-DD`` key back to its UTC-midnight epoch ms.

        Returns 0 if the key is missing or malformed so callers can skip the
        transaction-log query rather than crash.
        """
        if not day_key:
            return 0
        try:
            from datetime import datetime, timezone

            dt = datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            return 0

    def _refresh_cash_flows_by_book(
        self,
        state: StrategyState,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> None:
        """Refresh the net external cash-flow tally for each traded book.

        Queries Deribit's ``private/get_transaction_log`` for every currency
        in ``traded_collaterals``, filters to external-flow types (deposit,
        withdrawal, transfer), and sums the signed amounts in native and
        USDC-equivalent terms. Drawdown uses the native tally; the USDC tally is
        retained for reporting and backward-compatible state.

        Calls are throttled per book by ``cash_flow_query_interval_seconds``.
        Failures are logged and swallowed so a single API flake does not
        block the rest of the cycle.
        """
        if not self.config.has_private_credentials:
            return
        day_start_ms = self._day_start_ms_from_key(state.day_key)
        if day_start_ms <= 0:
            return
        now_ms = utc_now_ms()
        interval_ms = max(self.config.cash_flow_query_interval_seconds, 1) * 1000

        for collateral_raw in self.config.traded_collaterals:
            collateral = collateral_raw.upper()
            last_query = state.last_flow_query_ms_by_book.get(collateral, 0)
            if last_query and (now_ms - last_query) < interval_ms:
                continue

            try:
                payloads = self.client.get_transaction_log(
                    currency=collateral,
                    start_timestamp=day_start_ms,
                    end_timestamp=now_ms,
                    count=100,
                )
            except Exception as exc:
                LOGGER.warning(
                    "cash_flow_refresh_failed currency=%s err=%s",
                    collateral,
                    exc,
                )
                continue

            net_native = Decimal("0")
            for payload in payloads:
                if not isinstance(payload, dict):
                    continue
                entry_type = str(payload.get("type") or "").lower()
                if entry_type not in EXTERNAL_FLOW_TRANSACTION_TYPES:
                    continue
                amount_raw = payload.get("change")
                if amount_raw is None:
                    amount_raw = payload.get("amount")
                net_native += to_decimal(amount_raw)

            if collateral == "USDC":
                net_usdc = net_native
            else:
                index_price = self._currency_index_price(collateral, orderbook_cache)
                net_usdc = net_native * index_price

            state.day_net_flow_usdc_by_book[collateral] = net_usdc
            state.day_net_flow_native_by_book[collateral] = net_native
            state.last_flow_query_ms_by_book[collateral] = now_ms

    def _reset_daily_state(self, state: StrategyState, summaries: dict[str, AccountSummary]) -> StrategyState:
        today_key = utc_now().strftime("%Y-%m-%d")
        total_equity = self._total_equity_usdc(summaries, {})
        per_book = self._book_equities_usdc(summaries, {})
        per_book_native = self._book_equities_native(summaries)
        if state.day_key != today_key:
            state.day_key = today_key
            state.day_start_equity_usdc = total_equity
            state.day_start_equity_by_book = dict(per_book)
            state.day_start_equity_native_by_book = dict(per_book_native)
            # New UTC day → flow tallies reset to zero and query timestamps
            # cleared so the next cycle re-queries from the fresh day-start.
            state.day_net_flow_usdc_by_book = {book: Decimal("0") for book in per_book}
            state.day_net_flow_native_by_book = {book: Decimal("0") for book in per_book_native}
            state.last_flow_query_ms_by_book = {}
        else:
            # First run after schema upgrade: backfill any missing per-book entry
            # from the current equity so we don't treat "unset" as "zero drop".
            for book, equity in per_book.items():
                state.day_start_equity_by_book.setdefault(book, equity)
                state.day_net_flow_usdc_by_book.setdefault(book, Decimal("0"))
            for book, equity in per_book_native.items():
                native_start = state.day_start_equity_by_book.get(book, equity) if book == "USDC" else equity
                state.day_start_equity_native_by_book.setdefault(book, native_start)
                state.day_net_flow_native_by_book.setdefault(book, Decimal("0"))
        state.last_equity_usdc = total_equity
        state.last_equity_by_book = dict(per_book)
        state.last_equity_native_by_book = dict(per_book_native)
        # Drop expired per-book cooldowns so the dict doesn't grow unbounded.
        now_ms = utc_now_ms()
        state.cooldown_until_ms_by_book = {
            book: ts for book, ts in state.cooldown_until_ms_by_book.items() if ts and ts > now_ms
        }
        return state

    def _topup_existing_naked_groups(
        self,
        context: RuntimeContext,
        *,
        live: bool,
    ) -> list[dict[str, Any]]:
        """When groups are at capacity, use surplus margin to increase quantity of open naked groups."""
        if not self.config.enable_naked_topup:
            return []
        open_naked = list(self._open_groups(context.state))
        if not open_naked:
            return []

        orderbook_cache = context.orderbook_cache
        loader = lambda instrument_name: self._get_orderbook(instrument_name, orderbook_cache)
        actions: list[dict[str, Any]] = []

        for group in open_naked:
            currency = group.currency
            regime = context.regime_by_currency.get(currency, RiskRegime.CRISIS)
            if regime is RiskRegime.CRISIS:
                continue
            collateral_ccy = self._group_collateral_currency(group)
            collateral_summary = context.summaries.get(collateral_ccy)
            if collateral_summary is None or collateral_summary.equity <= 0:
                continue
            try:
                inst = self._find_instrument(context, group.short_instrument_name)
            except KeyError:
                continue
            if inst.dte_days() < self.config.time_exit_dte:
                continue
            book = loader(group.short_instrument_name)
            im_by_exp = dict(
                self._naked_im_by_expiry(
                    context.state, collateral_ccy, orderbook_cache=context.orderbook_cache
                )
            )
            exp_key = group.expiration_timestamp_ms
            own_im = self._group_im_in_collateral(group, orderbook_cache=context.orderbook_cache)
            if exp_key in im_by_exp:
                im_by_exp[exp_key] = max(im_by_exp[exp_key] - own_im, Decimal("0"))
            group_option_type = getattr(group, "option_type", "put") or "put"
            if group_option_type == "call":
                builder = self.strategy.build_naked_short_call_candidates
            else:
                builder = self.strategy.build_naked_short_put_candidates
            candidates = builder(
                [inst],
                loader,
                regime=regime,
                summary_equity=collateral_summary.equity,
                summary_maintenance_margin=collateral_summary.maintenance_margin,
                collateral_currency=collateral_ccy,
                currency=currency,
                existing_im_by_expiry=im_by_exp,
            )
            if not candidates:
                continue
            new_max_qty = candidates[0].quantity
            topup_qty = new_max_qty - group.quantity
            if topup_qty < inst.min_trade_amount:
                continue
            adjusted_exp_im = im_by_exp.get(exp_key, Decimal("0"))
            topup_candidate, _topup_refresh_detail = self.strategy.refresh_naked_candidate(
                option_type=group_option_type,
                instrument=inst,
                book=book,
                regime=regime,
                summary_equity=collateral_summary.equity,
                summary_maintenance_margin=collateral_summary.maintenance_margin,
                collateral_currency=collateral_ccy,
                currency=currency,
                quantity=topup_qty,
                existing_im_for_expiry=adjusted_exp_im,
            )
            if topup_candidate is None:
                continue

            if not live:
                actions.append({
                    "action": "dry_run_topup_naked",
                    "group_id": group.group_id,
                    "instrument": group.short_instrument_name,
                    "current_qty": group.quantity,
                    "topup_qty": topup_qty,
                    "new_total_qty": new_max_qty,
                    "candidate": topup_candidate.to_dict(),
                })
                continue

            execution = self._execute_repriced_naked_short(
                context,
                topup_candidate,
                label=group.short_label or f"trial_{currency}_{group.group_id}_short",
                quantity=topup_qty,
            )
            filled = execution["filled_amount"]
            if filled <= 0:
                actions.append({
                    "action": "topup_unfilled",
                    "group_id": group.group_id,
                    "instrument": group.short_instrument_name,
                    "topup_qty": topup_qty,
                    "reason": execution["reason"],
                })
                continue
            actions.append({
                "action": "topup_naked_executed",
                "group_id": group.group_id,
                "instrument": group.short_instrument_name,
                "current_qty": group.quantity,
                "topup_filled": filled,
                "new_total_qty": group.quantity + filled,
                "reason": execution["reason"],
            })
            LOGGER.info(
                "topup group=%s instrument=%s filled=%s (was %s → %s)",
                group.group_id,
                group.short_instrument_name,
                format_decimal(filled, 8),
                format_decimal(group.quantity, 8),
                format_decimal(group.quantity + filled, 8),
            )

        return actions

    def _sync_naked_open_groups_from_positions(self, state: StrategyState, option_positions: list[Position]) -> None:
        """Align naked short group quantity with exchange; scale entry_credit / max_loss proportionally (manual scale-in)."""
        open_naked = [g for g in state.groups if g.status == "open"]
        short_counts = Counter(g.short_instrument_name for g in open_naked)
        dupes = {name for name, n in short_counts.items() if n > 1}
        if dupes:
            LOGGER.warning("naked qty sync skipped: multiple open groups share short leg %s", sorted(dupes))

        sell_qty_by_name: dict[str, Decimal] = {}
        for p in option_positions:
            q = self._short_option_open_size(p)
            if q is not None:
                sell_qty_by_name[p.instrument_name] = q

        for group in open_naked:
            if group.short_instrument_name in dupes:
                continue
            ex_qty = sell_qty_by_name.get(group.short_instrument_name)
            if ex_qty is None:
                continue
            if ex_qty == group.quantity:
                if group.last_action and group.last_action.endswith("_incomplete"):
                    group.last_action = ""
                continue
            if group.quantity > 0:
                old_q = group.quantity
                group.quantity = ex_qty
                group.max_loss = group.max_loss * ex_qty / old_q
                group.estimated_im_collateral = group.estimated_im_collateral * ex_qty / old_q
                group.entry_credit = group.entry_credit * ex_qty / old_q
                group.original_entry_credit = group.original_entry_credit * ex_qty / old_q
                group.entry_fee = group.entry_fee * ex_qty / old_q
                LOGGER.info(
                    "naked group=%s quantity synced to exchange size=%s (was %s)",
                    group.group_id,
                    format_decimal(ex_qty, 8),
                    format_decimal(old_q, 8),
                )
            else:
                group.quantity = ex_qty
                LOGGER.warning("naked group=%s had quantity 0; set to exchange size=%s", group.group_id, ex_qty)
            group.last_action = "quantity_synced_exchange"

    def _try_find_instrument(
        self, markets_by_currency: dict[str, list[OptionInstrument]], instrument_name: str
    ) -> OptionInstrument | None:
        try:
            return self._find_instrument_by_markets(markets_by_currency, instrument_name)
        except KeyError:
            return None

    def _find_bull_put_spread_long_match(
        self,
        *,
        short_instrument: OptionInstrument,
        short_quantity: Decimal,
        option_positions: list[Position],
        markets_by_currency: dict[str, list[OptionInstrument]],
        excluded_long_names: set[str],
    ) -> tuple[Position, OptionInstrument, Decimal] | None:
        if short_instrument.option_type != "put" or short_quantity <= 0:
            return None
        candidates: list[tuple[Decimal, Position, OptionInstrument, Decimal]] = []
        for position in option_positions:
            if position.instrument_name in excluded_long_names:
                continue
            long_quantity = self._long_option_open_size(position)
            if long_quantity is None or long_quantity < short_quantity:
                continue
            long_instrument = self._try_find_instrument(markets_by_currency, position.instrument_name)
            if long_instrument is None:
                continue
            if (
                long_instrument.option_type != "put"
                or long_instrument.base_currency != short_instrument.base_currency
                or long_instrument.quote_currency != short_instrument.quote_currency
                or long_instrument.settlement_currency != short_instrument.settlement_currency
                or long_instrument.expiration_timestamp_ms != short_instrument.expiration_timestamp_ms
                or long_instrument.strike >= short_instrument.strike
            ):
                continue
            quantity = align_option_order_amount(
                short_quantity,
                short_instrument.contract_size,
                short_instrument.min_trade_amount,
            )
            quantity = align_option_order_amount(
                quantity,
                long_instrument.contract_size,
                long_instrument.min_trade_amount,
            )
            if quantity <= 0 or long_quantity < quantity:
                continue
            candidates.append((short_instrument.strike - long_instrument.strike, position, long_instrument, quantity))
        if not candidates:
            return None
        _, position, long_instrument, quantity = min(candidates, key=lambda item: item[0])
        return position, long_instrument, quantity

    def _bull_put_spread_adoption_metrics(
        self,
        *,
        short_position: Position,
        short_instrument: OptionInstrument,
        long_position: Position,
        long_instrument: OptionInstrument,
        quantity: Decimal,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> dict[str, Decimal]:
        short_book = self._get_orderbook(short_instrument.instrument_name, orderbook_cache)
        long_book = self._get_orderbook(long_instrument.instrument_name, orderbook_cache)
        idx = short_book.index_price if short_book.index_price > 0 else long_book.index_price
        short_premium = abs(short_position.average_price) if short_position.average_price != 0 else short_book.mark_price
        long_premium = abs(long_position.average_price) if long_position.average_price != 0 else long_book.mark_price
        short_credit = self._premium_value_usdc(
            premium=short_premium,
            quantity=quantity,
            index_price=idx,
            instrument=short_instrument,
        )
        long_debit = self._premium_value_usdc(
            premium=long_premium,
            quantity=quantity,
            index_price=idx,
            instrument=long_instrument,
        )
        short_fee = self._option_fee_usdc(
            premium=short_premium,
            quantity=quantity,
            index_price=idx,
            base_currency=short_instrument.base_currency,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        long_fee = self._option_fee_usdc(
            premium=long_premium,
            quantity=quantity,
            index_price=idx,
            base_currency=long_instrument.base_currency,
            quote_currency=long_instrument.quote_currency,
            settlement_currency=long_instrument.settlement_currency,
        )
        entry_fee = short_fee + long_fee
        net_credit = short_credit - long_debit - entry_fee
        width_usdc = max(short_instrument.strike - long_instrument.strike, Decimal("0")) * quantity
        max_loss_usdc = max(width_usdc - net_credit, Decimal("0"))
        collateral = "USDC" if short_instrument.quote_currency.upper() == "USDC" and short_instrument.settlement_currency.upper() == "USDC" else short_instrument.base_currency
        estimated_im_collateral = max_loss_usdc if collateral == "USDC" else (max_loss_usdc / idx if idx > 0 else Decimal("0"))
        return {
            "entry_credit": net_credit,
            "entry_fee": entry_fee,
            "max_loss": max_loss_usdc,
            "estimated_im_collateral": estimated_im_collateral,
        }

    def _promote_group_to_bull_put_spread(
        self,
        group: TradeGroup,
        *,
        short_position: Position,
        short_instrument: OptionInstrument,
        long_position: Position,
        long_instrument: OptionInstrument,
        quantity: Decimal,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> None:
        metrics = self._bull_put_spread_adoption_metrics(
            short_position=short_position,
            short_instrument=short_instrument,
            long_position=long_position,
            long_instrument=long_instrument,
            quantity=quantity,
            orderbook_cache=orderbook_cache,
        )
        labels = self._spread_labels(short_instrument.base_currency, group.group_id)
        group.strategy = "bull_put_spread"
        group.option_type = "put"
        group.quantity = quantity
        group.long_instrument_name = long_instrument.instrument_name
        group.long_strike = long_instrument.strike
        group.long_label = group.long_label or labels["long"]
        group.short_label = group.short_label or labels["short"]
        group.entry_credit = metrics["entry_credit"]
        group.original_entry_credit = metrics["entry_credit"]
        group.entry_fee = metrics["entry_fee"]
        group.max_loss = metrics["max_loss"]
        group.estimated_im_collateral = metrics["estimated_im_collateral"]
        group.last_action = "adopted_bull_put_spread_from_exchange"

    def _sync_bull_put_spread_groups_from_positions(
        self,
        state: StrategyState,
        *,
        option_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets_by_currency: dict[str, list[OptionInstrument]],
    ) -> None:
        if self.config.option_strategy != "bull_put_spread":
            return
        short_positions = {p.instrument_name: p for p in option_positions if self._short_option_open_size(p) is not None}
        used_long_names = {g.long_instrument_name for g in self._open_groups(state) if g.long_instrument_name}
        for group in self._open_groups(state):
            if group.option_type != "put" or group.long_instrument_name:
                continue
            short_position = short_positions.get(group.short_instrument_name)
            if short_position is None:
                continue
            short_instrument = self._try_find_instrument(markets_by_currency, group.short_instrument_name)
            if short_instrument is None:
                continue
            short_quantity = self._short_option_open_size(short_position) or group.quantity
            match = self._find_bull_put_spread_long_match(
                short_instrument=short_instrument,
                short_quantity=short_quantity,
                option_positions=option_positions,
                markets_by_currency=markets_by_currency,
                excluded_long_names=used_long_names,
            )
            if match is None:
                continue
            long_position, long_instrument, quantity = match
            self._promote_group_to_bull_put_spread(
                group,
                short_position=short_position,
                short_instrument=short_instrument,
                long_position=long_position,
                long_instrument=long_instrument,
                quantity=quantity,
                orderbook_cache=orderbook_cache,
            )
            used_long_names.add(long_instrument.instrument_name)

    def _adopt_untracked_naked_short_positions(
        self,
        state: StrategyState,
        *,
        option_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets_by_currency: dict[str, list[OptionInstrument]],
    ) -> None:
        """Create ``TradeGroup`` rows for naked short options on the exchange that are missing from state.

        Without this, positions opened outside the bot (or after state loss) never appear in
        ``open_groups``, so ``manage`` / early-exit / TP logic never runs on them.
        """
        if not self.config.enable_adopt_exchange_positions:
            return
        tracked_open = {g.short_instrument_name for g in state.groups if g.status == "open"}
        tracked_longs = {g.long_instrument_name for g in state.groups if g.status == "open" and g.long_instrument_name}

        for position in option_positions:
            open_sz = self._short_option_open_size(position)
            if open_sz is None:
                continue
            name = position.instrument_name
            if name in tracked_open:
                continue
            if name.endswith("-P"):
                if not self.config.enable_short_put:
                    continue
                option_type = "put"
            elif name.endswith("-C"):
                if not self.config.enable_short_call:
                    continue
                option_type = "call"
            else:
                continue

            try:
                inst = self._find_or_fetch_instrument(markets_by_currency, name)
            except KeyError:
                LOGGER.warning("adopt skipped: missing instrument metadata for %s", name)
                continue

            try:
                book = self._get_orderbook(name, orderbook_cache)
            except Exception as exc:
                LOGGER.warning("adopt skipped: no orderbook for %s (%s)", name, exc)
                continue

            idx = book.index_price
            if idx <= 0:
                continue
            mark = book.mark_price if book.mark_price > 0 else (book.best_bid_price + book.best_ask_price) / Decimal("2")
            if mark <= 0:
                continue

            qty = align_option_order_amount(open_sz, inst.contract_size, inst.min_trade_amount)
            if qty <= 0:
                continue

            premium = abs(position.average_price) if position.average_price != 0 else mark
            entry_fee = self._option_fee_usdc(
                premium=premium,
                quantity=qty,
                index_price=idx,
                base_currency=inst.base_currency,
                quote_currency=inst.quote_currency,
                settlement_currency=inst.settlement_currency,
            )
            gross = self._premium_value_usdc(
                premium=premium,
                quantity=qty,
                index_price=idx,
                instrument=inst,
            )
            net_credit = gross - entry_fee

            usdc_linear = inst.quote_currency.upper() == "USDC" and inst.settlement_currency.upper() == "USDC"
            collateral = "USDC" if usdc_linear else inst.base_currency

            if usdc_linear:
                if option_type == "call":
                    im_1 = linear_usdc_short_call_initial_per_contract_usdc(
                        index_price=idx,
                        strike=inst.strike,
                        mark_usdc=mark,
                        contract_size=inst.contract_size,
                    )
                else:
                    im_1 = linear_usdc_short_put_initial_per_contract_usdc(
                        index_price=idx,
                        strike=inst.strike,
                        mark_usdc=mark,
                        contract_size=inst.contract_size,
                    )
                estimated_im_collateral = im_1 * qty  # USDC-native
                max_loss_usdc = estimated_im_collateral
            else:
                if option_type == "call":
                    im_u = short_call_initial_unit(index_price=idx, strike=inst.strike, mark_price=mark)
                else:
                    im_u = short_put_initial_unit(index_price=idx, strike=inst.strike, mark_price=mark)
                estimated_im_collateral = im_u * qty  # BTC/ETH-native
                max_loss_usdc = estimated_im_collateral * idx if idx > 0 else estimated_im_collateral

            group_id = self._next_group_id(state)
            labels = self._spread_labels(inst.base_currency, group_id)
            spread_match = (
                self._find_bull_put_spread_long_match(
                    short_instrument=inst,
                    short_quantity=qty,
                    option_positions=option_positions,
                    markets_by_currency=markets_by_currency,
                    excluded_long_names=tracked_longs,
                )
                if self.config.option_strategy == "bull_put_spread" and option_type == "put"
                else None
            )
            strategy = (
                "bull_put_spread"
                if spread_match is not None
                else (
                    "covered_call"
                    if self.config.option_strategy == "covered_call"
                    and option_type == "call"
                    and collateral == inst.base_currency
                    else "naked_short"
                )
            )
            group = TradeGroup(
                group_id=group_id,
                currency=inst.base_currency,
                collateral_currency=collateral,
                quantity=qty,
                entry_timestamp_ms=utc_now_ms(),
                expiration_timestamp_ms=inst.expiration_timestamp_ms,
                short_instrument_name=name,
                short_strike=inst.strike,
                entry_credit=net_credit,
                original_entry_credit=net_credit,
                max_loss=max_loss_usdc,
                estimated_im_collateral=estimated_im_collateral,
                regime_at_entry=RiskRegime.NORMAL.value,
                entry_fee=entry_fee,
                short_label=labels["short"],
                option_type=option_type,
                strategy=strategy,
                covered_underlying_quantity=qty if strategy == "covered_call" else Decimal("0"),
            )
            if spread_match is not None:
                long_position, long_instrument, spread_quantity = spread_match
                self._promote_group_to_bull_put_spread(
                    group,
                    short_position=position,
                    short_instrument=inst,
                    long_position=long_position,
                    long_instrument=long_instrument,
                    quantity=spread_quantity,
                    orderbook_cache=orderbook_cache,
                )
                tracked_longs.add(long_instrument.instrument_name)
            group.last_action = "adopted_from_exchange"
            state.groups.append(group)
            tracked_open.add(name)
            LOGGER.info(
                "adopted %s %s from exchange: group=%s qty=%s entry_credit_usdc=%s max_loss_usdc=%s",
                strategy,
                name,
                group_id,
                format_decimal(qty, 8),
                format_decimal(net_credit, 8),
                format_decimal(max_loss_usdc, 8),
            )

    def _reconcile_state(
        self,
        state: StrategyState,
        *,
        option_positions: list[Position],
        orderbook_cache: dict[str, OrderBookSnapshot],
        markets_by_currency: dict[str, list[OptionInstrument]],
    ) -> StrategyState:
        self._sync_naked_open_groups_from_positions(state, option_positions)
        self._sync_bull_put_spread_groups_from_positions(
            state,
            option_positions=option_positions,
            orderbook_cache=orderbook_cache,
            markets_by_currency=markets_by_currency,
        )
        self._adopt_untracked_naked_short_positions(
            state,
            option_positions=option_positions,
            orderbook_cache=orderbook_cache,
            markets_by_currency=markets_by_currency,
        )
        open_short_option_names = {
            p.instrument_name for p in option_positions if self._short_option_open_size(p) is not None
        }
        for group in state.groups:
            if group.status != "open":
                continue
            still_open = group.short_instrument_name in open_short_option_names
            if still_open:
                continue
            closed_timestamp_ms = utc_now_ms()
            estimated_close_debit = self._estimate_reconcile_close_debit(group, orderbook_cache)
            realized_pnl: Decimal | None = None
            realized_return_on_max_loss: Decimal | None = None
            realized_annualized_return: Decimal | None = None
            idx_px = Decimal("0")
            try:
                idx_px = self._get_orderbook(group.short_instrument_name, orderbook_cache).index_price
            except Exception:
                idx_px = Decimal("0")
            if estimated_close_debit is not None:
                realized_pnl = group.entry_credit - estimated_close_debit
                realized_return_on_max_loss = safe_div(realized_pnl, group.max_loss)
                realized_annualized_return = self._realized_annualized_return_on_im_native(
                    group,
                    realized_pnl,
                    index_price_usd=idx_px,
                    closed_timestamp_ms=closed_timestamp_ms,
                    orderbook_cache=orderbook_cache,
                )
            if (
                self.config.covered_call_spot_exit_enabled
                and not self.config.covered_call_robust_exit_enabled
                and self._is_covered_call_group(group)
                and group.spot_exit_status not in {"submitted", "filled", "pending"}
                and self._covered_call_itm_from_cache(group, orderbook_cache)
            ):
                group.spot_exit_status = "pending"
                group.spot_exit_amount = group.covered_underlying_quantity if group.covered_underlying_quantity > 0 else group.quantity
                group.spot_exit_instrument_name = self._covered_call_spot_instrument(group.currency)
                group.spot_exit_reason = "covered_call_settlement_exit"
            self._mark_group_closed(
                group,
                reason="reconciled_external",
                closed_timestamp_ms=closed_timestamp_ms,
                realized_close_debit=estimated_close_debit,
                realized_pnl=realized_pnl,
                realized_return_on_max_loss=realized_return_on_max_loss,
                realized_annualized_return=realized_annualized_return,
            )
            if realized_pnl is not None:
                LOGGER.info("reconcile group=%s estimated_pnl=%s", group.group_id, realized_pnl)
            else:
                LOGGER.warning("reconcile group=%s could not estimate PnL", group.group_id)
        return state

    def _estimate_reconcile_close_debit(
        self,
        group: TradeGroup,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> Decimal | None:
        if group.current_debit > 0:
            return group.current_debit
        try:
            short_book = self._get_orderbook(group.short_instrument_name, orderbook_cache)
            mark_debit = short_book.mark_price * group.quantity
            if "_USDC-" in group.short_instrument_name:
                return max(mark_debit, Decimal("0"))
            index_price = short_book.index_price
            if index_price > 0 and group.short_instrument_name.startswith(("BTC-", "ETH-")):
                mark_debit *= index_price
            return max(mark_debit, Decimal("0"))
        except Exception:
            if group.current_debit >= 0:
                return group.current_debit
            return None

    def _refresh_group(
        self,
        *,
        context_markets: dict[str, list[OptionInstrument]],
        group: TradeGroup,
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> None:
        short_book = self._get_orderbook(group.short_instrument_name, orderbook_cache)
        short_instrument = self._find_or_fetch_instrument(context_markets, group.short_instrument_name)
        # Estimate close-cost premium. We prefer best_ask (what we'd actually
        # pay to cross) but fall back to mark when the ask side is empty /
        # stale, otherwise current_debit collapses to zero and profit_capture
        # falsely hits 100% -> spurious take_profit. Use max(ask, mark) so a
        # wide-spread book where ask > mark still reflects realistic cost.
        close_premium = max(short_book.best_ask_price, short_book.mark_price)
        if close_premium <= 0:
            close_premium = Decimal("0")
        group.current_debit = max(
            self._premium_value_usdc(
                premium=close_premium,
                quantity=group.quantity,
                index_price=short_book.index_price,
                instrument=short_instrument,
            ),
            Decimal("0"),
        )
        group.current_close_fee = self._option_fee_usdc(
            premium=close_premium,
            quantity=group.quantity,
            index_price=short_book.index_price,
            base_currency=short_instrument.base_currency,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        group.current_debit += group.current_close_fee
        if group.long_instrument_name:
            try:
                long_book = self._get_orderbook(group.long_instrument_name, orderbook_cache)
                long_instrument = self._find_or_fetch_instrument(context_markets, group.long_instrument_name)
                long_close_premium = long_book.best_bid_price if long_book.best_bid_price > 0 else long_book.mark_price
                long_credit = self._premium_value_usdc(
                    premium=max(long_close_premium, Decimal("0")),
                    quantity=group.quantity,
                    index_price=long_book.index_price,
                    instrument=long_instrument,
                )
                long_close_fee = self._option_fee_usdc(
                    premium=max(long_close_premium, Decimal("0")),
                    quantity=group.quantity,
                    index_price=long_book.index_price,
                    base_currency=long_instrument.base_currency,
                    quote_currency=long_instrument.quote_currency,
                    settlement_currency=long_instrument.settlement_currency,
                )
                group.current_debit = max(group.current_debit - max(long_credit - long_close_fee, Decimal("0")), Decimal("0"))
                group.current_close_fee += long_close_fee
            except Exception as exc:
                LOGGER.warning("refresh_group %s: unable to refresh long leg %s (%s)", group.group_id, group.long_instrument_name, exc)
        group.profit_capture = safe_div(max(group.entry_credit - group.current_debit, Decimal("0")), group.entry_credit)
        group.short_delta = abs(short_book.delta)

    def _delta_totals_by_currency(
        self,
        summaries: dict[str, AccountSummary],
        state: StrategyState,
        future_positions: list[Position],
    ) -> dict[str, Decimal]:
        """Self-compute per-currency net delta from our own tracked legs + perp hedge.

        We intentionally do NOT use ``summary.delta_total`` (even as a fallback)
        because Deribit includes hedge perps from every strategy/subaccount that
        shares credentials, which can blow up the hedge plan. The matching
        ``summary.delta_total`` monitoring value is exposed via the account
        summaries for dashboards but hedging decisions run off this value.

        Formula per open group:
            group_delta = option_sign * abs(greek_delta) * quantity

        where ``option_sign`` is +1 for a short put (short puts are long-delta)
        and -1 for a short call (short calls are short-delta). Stage B introduces
        ``option_type`` on TradeGroup; until then all groups are short puts.
        """
        _ = summaries  # retained for signature parity / future parity checks
        totals: dict[str, Decimal] = {}
        for currency in self.config.managed_currencies:
            group_delta = Decimal("0")
            for group in self._open_groups(state):
                if group.currency != currency:
                    continue
                option_type = getattr(group, "option_type", "put") or "put"
                option_sign = Decimal("-1") if option_type == "call" else Decimal("1")
                group_delta += option_sign * group.short_delta * group.quantity
            hedge_delta = sum(
                (
                    position.signed_size_currency
                    for position in future_positions
                    if position.instrument_name == self._perp_instrument(currency)
                ),
                Decimal("0"),
            )
            totals[currency] = group_delta + hedge_delta
        return totals

    def _current_hedge_base(self, positions: list[Position], currency: str) -> Decimal:
        instrument_name = self._perp_instrument(currency)
        return sum((position.signed_size_currency for position in positions if position.instrument_name == instrument_name), Decimal("0"))

    def _overall_regime(self, values: Any) -> RiskRegime:
        severity = {RiskRegime.NORMAL: 0, RiskRegime.ELEVATED: 1, RiskRegime.CRISIS: 2}
        worst = RiskRegime.NORMAL
        for value in values:
            if severity[value] > severity[worst]:
                worst = value
        return worst

    def _update_recovery_counts(self, state: StrategyState, regimes: dict[str, RiskRegime]) -> None:
        for currency, regime in regimes.items():
            if regime is RiskRegime.NORMAL:
                state.normal_recovery_counts[currency] = state.normal_recovery_counts.get(currency, 0) + 1
            else:
                state.normal_recovery_counts[currency] = 0

    def _currency_index_price(self, currency: str, orderbook_cache: dict[str, OrderBookSnapshot]) -> Decimal:
        """USD index for one coin of ``currency`` (BTC/ETH).

        Prefer Deribit **public/get_index_price** (``eth_usdc`` / ``eth_usd`` etc.) — the
        same composite spot family used for margin / index products — then fall back to
        the perpetual order book ``index_price``. Relying on perp OB first can drift from
        what traders call \"spot\" when the book feed is thin or lagging.
        """
        for index_name in (f"{currency.lower()}_usdc", f"{currency.lower()}_usd"):
            try:
                payload = self.client.get_index_price(index_name)
            except Exception:
                continue
            value = to_decimal(payload.get("index_price"))
            if value > 0:
                return value
        perp = self._perp_instrument(currency)
        try:
            book = self._get_orderbook(perp, orderbook_cache)
            if book.index_price > 0:
                return book.index_price
        except Exception:
            pass
        return Decimal("0")

    def _stage_c_collateral_books(self) -> frozenset[str]:
        """Pools included in Stage-C headline equity / margin rollups.

        Matches :meth:`_book_equities_usdc` so dashboard ``total_equity_usdc``
        does not pull in inverse dust when ``TRADED_COLLATERALS`` / scan scope
        is USDC-only while ``MANAGED_CURRENCIES`` still lists BTC/ETH for
        linear option discovery.
        """
        traded = {c.upper() for c in self.config.traded_collaterals}
        scanned = {c.upper() for c in self.config.scan_underlyings}
        books: set[str] = set()
        for c in ("BTC", "ETH"):
            if c in traded and c in scanned:
                books.add(c)
        if "USDC" in traded:
            books.add("USDC")
        return frozenset(books)

    def _total_equity_usdc(self, summaries: dict[str, AccountSummary], orderbook_cache: dict[str, OrderBookSnapshot]) -> Decimal:
        return sum(self._book_equities_usdc(summaries, orderbook_cache).values(), Decimal("0"))

    def _book_equities_usdc(
        self,
        summaries: dict[str, AccountSummary],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> dict[str, Decimal]:
        """Per-book equity expressed in USDC.

        Keys match ``Book.collateral`` (``"BTC"``, ``"ETH"``, ``"USDC"``) so the
        same dict can be joined with ``state.*_by_book`` views.

        Only books whose collateral is whitelisted in ``traded_collaterals``
        are included. Pools omitted from that list are *not* tracked here, so
        their residual dust cannot leak into drawdown or IM gates. The
        ``scan_underlyings`` list additionally filters the BTC/ETH inverse
        books so that, for example, a USDC-only deployment will not build a
        BTC book even if ``BTC`` is in ``traded_collaterals``.
        """
        result: dict[str, Decimal] = {}
        traded = {c.upper() for c in self.config.traded_collaterals}
        scanned = {c.upper() for c in self.config.scan_underlyings}
        for currency in ("BTC", "ETH"):
            if currency not in traded:
                continue
            if currency not in scanned:
                continue
            summary = summaries.get(currency)
            if summary is None:
                result[currency] = Decimal("0")
                continue
            result[currency] = summary.equity * self._currency_index_price(currency, orderbook_cache)
        if "USDC" in traded:
            usdc_summary = summaries.get("USDC")
            result["USDC"] = usdc_summary.equity if usdc_summary is not None else Decimal("0")
        return result

    def _book_equities_native(
        self,
        summaries: dict[str, AccountSummary],
    ) -> dict[str, Decimal]:
        """Per-book equity in each book's own collateral unit."""
        result: dict[str, Decimal] = {}
        traded = {c.upper() for c in self.config.traded_collaterals}
        scanned = {c.upper() for c in self.config.scan_underlyings}
        for currency in ("BTC", "ETH"):
            if currency not in traded:
                continue
            if currency not in scanned:
                continue
            summary = summaries.get(currency)
            result[currency] = summary.equity if summary is not None else Decimal("0")
        if "USDC" in traded:
            usdc_summary = summaries.get("USDC")
            result["USDC"] = usdc_summary.equity if usdc_summary is not None else Decimal("0")
        return result

    def _aggregate_margin(
        self,
        summaries: dict[str, AccountSummary],
        orderbook_cache: dict[str, OrderBookSnapshot],
        *,
        margin_kind: str,
    ) -> Decimal:
        total = Decimal("0")
        scope = self._stage_c_collateral_books()
        for currency in scope:
            summary = summaries.get(currency)
            if summary is None:
                continue
            if margin_kind == "initial":
                amount = summary.initial_margin
            else:
                amount = summary.maintenance_margin
            if currency == "USDC":
                total += amount
            else:
                total += amount * self._currency_index_price(currency, orderbook_cache)
        return total

    def _per_currency_margin_ratios(
        self, summaries: dict[str, AccountSummary],
    ) -> dict[str, tuple[Decimal, Decimal]]:
        """Per-account (im_ratio, mm_ratio) for each segregated margin account."""
        result: dict[str, tuple[Decimal, Decimal]] = {}
        scope = self._stage_c_collateral_books()
        for currency, summary in summaries.items():
            if currency not in scope:
                continue
            equity = summary.equity
            if equity <= 0:
                continue
            im_ratio = safe_div(summary.initial_margin, equity)
            mm_ratio = safe_div(summary.maintenance_margin, equity)
            result[currency] = (im_ratio, mm_ratio)
        return result

    def _index_drawdown_24h(self, currency: str) -> Decimal | None:
        """Return 24h index drawdown as Decimal, or None if the feed is unavailable.

        Returning None (instead of a conservative sentinel like -1) lets callers
        distinguish "market is actually down" from "we don't know right now".
        """
        any_success = False
        for index_name in (f"{currency.lower()}_usdc", f"{currency.lower()}_usd"):
            try:
                points = self.client.get_index_chart_data(index_name, range_name="1d")
            except Exception:
                continue
            if not points:
                any_success = True
                continue
            if len(points) >= 2:
                start_price = to_decimal(points[0][1])
                end_price = to_decimal(points[-1][1])
                if start_price > 0:
                    return (end_price / start_price) - Decimal("1")
            any_success = True
        if any_success:
            # API responded but didn't have enough candles yet — treat as unknown.
            return None
        return None

    def _dvol_ratio(self, currency: str) -> Decimal | None:
        """Return latest DVOL over 30-day median, or None if the feed is unavailable."""
        end_timestamp = utc_now_ms()
        start_timestamp = end_timestamp - (30 * 24 * 3600 * 1000)
        try:
            payload = self.client.get_volatility_index_data(
                currency,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                resolution="1D",
            )
        except Exception:
            return None
        rows = payload.get("data") or []
        closes = [to_decimal(row[4]) for row in rows if len(row) >= 5]
        if not closes:
            return None
        sorted_closes = sorted(closes)
        midpoint = len(sorted_closes) // 2
        median = (
            sorted_closes[midpoint]
            if len(sorted_closes) % 2 == 1
            else (sorted_closes[midpoint - 1] + sorted_closes[midpoint]) / Decimal("2")
        )
        if median <= 0:
            return None
        return closes[-1] / median

    @staticmethod
    def _response_average_price(response: dict[str, Any] | None) -> Decimal:
        if not isinstance(response, dict):
            return Decimal("0")
        order = response.get("order") or response
        if not isinstance(order, dict):
            return Decimal("0")
        return to_decimal(order.get("average_price") or order.get("price"))

    def _filled_average_price(self, responses: list[dict[str, Any]]) -> Decimal:
        total_filled = Decimal("0")
        weighted_price = Decimal("0")
        for response in responses:
            filled = self._response_filled_amount(response)
            if filled <= 0:
                continue
            average_price = self._response_average_price(response)
            total_filled += filled
            weighted_price += average_price * filled
        if total_filled <= 0:
            return Decimal("0")
        return weighted_price / total_filled

    @staticmethod
    def _response_filled_amount(response: dict[str, Any] | None) -> Decimal:
        if not isinstance(response, dict):
            return Decimal("0")
        order = response.get("order") or response
        if not isinstance(order, dict):
            return Decimal("0")
        return to_decimal(order.get("filled_amount"))

    @staticmethod
    def _response_order(response: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {}
        order = response.get("order") or response
        return order if isinstance(order, dict) else {}

    def _realized_sample_days(self, groups: list[TradeGroup]) -> Decimal:
        timestamps = [
            (group.entry_timestamp_ms, group.closed_timestamp_ms)
            for group in groups
            if group.closed_timestamp_ms is not None and group.entry_timestamp_ms > 0
        ]
        if not timestamps:
            return Decimal("0")
        start_ms = min(entry_ms for entry_ms, _ in timestamps)
        end_ms = max(close_ms for _, close_ms in timestamps if close_ms is not None)
        if end_ms <= start_ms:
            return Decimal("0")
        return Decimal(str(end_ms - start_ms)) / Decimal("86400000")

    def _window_realized_groups(self, groups: list[TradeGroup], days: int) -> tuple[list[TradeGroup], Decimal]:
        if not groups:
            return [], Decimal("0")
        if days <= 0:
            return groups, self._realized_sample_days(groups)
        cutoff_ms = utc_now_ms() - (days * 24 * 3600 * 1000)
        return [group for group in groups if (group.closed_timestamp_ms or 0) >= cutoff_ms], Decimal(str(days))

    def _annualize_apr(self, pnl: Decimal, sample_days: Decimal, capital: Decimal) -> Decimal:
        if pnl == 0 or sample_days <= 0 or capital <= 0:
            return Decimal("0")
        return safe_div(pnl, capital) * (Decimal("365") / sample_days)

    def _fetch_option_positions_for_report(self) -> list[Position]:
        if not self.config.has_private_credentials:
            return []
        try:
            rows = self.client.get_positions(currency="any", kind="any")
        except Exception:
            return []
        return [Position.from_api(row) for row in rows if str(row.get("kind") or "").lower() == "option"]

    def _trade_groups_payload(
        self,
        groups: list[TradeGroup],
        option_positions: list[Position] | None = None,
        orderbook_cache: dict[str, OrderBookSnapshot] | None = None,
    ) -> list[dict[str, Any]]:
        by_name = {p.instrument_name: p for p in (option_positions or []) if p.instrument_name}
        return [
            self._group_payload(
                group,
                short_position=by_name.get(group.short_instrument_name),
                orderbook_cache=orderbook_cache,
            )
            for group in groups
        ]

    def _report_group_payload(self, group: TradeGroup) -> dict[str, Any]:
        return {
            "group_id": group.group_id,
            "currency": group.currency,
            "collateral_currency": self._group_collateral_currency(group),
            "strategy": group.strategy or "naked_short",
            "quantity": format_decimal(group.quantity, 8),
            "status": group.status,
            "regime_at_entry": group.regime_at_entry,
            "entry_timestamp": ms_to_datetime(group.entry_timestamp_ms),
            "closed_timestamp": ms_to_datetime(group.closed_timestamp_ms),
            "closed_timestamp_ms": group.closed_timestamp_ms,
            "holding_days": group.holding_days,
            "close_reason": group.close_reason or group.last_action or None,
            "short_instrument_name": group.short_instrument_name,
            "long_instrument_name": group.long_instrument_name or None,
            "entry_credit": group.entry_credit,
            "entry_fee": group.entry_fee,
            "entry_net_apr": group.entry_net_apr,
            "entry_timestamp_ms": group.entry_timestamp_ms,
            "max_loss": group.max_loss,
            "realized_close_debit": group.realized_close_debit,
            "realized_close_fee": group.realized_close_fee,
            "realized_pnl": group.realized_pnl,
            "realized_return_on_max_loss": group.realized_return_on_max_loss,
            "realized_annualized_return": group.realized_annualized_return,
            "spot_exit_status": group.spot_exit_status or None,
            "spot_exit_amount": group.spot_exit_amount if group.spot_exit_amount > 0 else None,
            "spot_exit_instrument_name": group.spot_exit_instrument_name or None,
            "spot_exit_order_id": group.spot_exit_order_id or None,
            "spot_exit_reason": group.spot_exit_reason or None,
        }

    def _group_payload(
        self,
        group: TradeGroup,
        *,
        short_position: Position | None = None,
        orderbook_cache: dict[str, OrderBookSnapshot] | None = None,
    ) -> dict[str, Any]:
        expiry = ms_to_datetime(group.expiration_timestamp_ms)
        unrealized_usdc = group.entry_credit - group.current_debit
        payload: dict[str, Any] = {
            "group_id": group.group_id,
            "currency": group.currency,
            "collateral_currency": self._group_collateral_currency(group),
            "strategy": group.strategy or "naked_short",
            "quantity": format_decimal(group.quantity, 8),
            "expiry": expiry.isoformat() if expiry is not None else None,
            "expiration_timestamp_ms": group.expiration_timestamp_ms,
            "dte_days": format_decimal(group.dte_days, 4),
            "short_instrument_name": group.short_instrument_name,
            "short_strike": format_decimal(group.short_strike, 8),
            "long_instrument_name": group.long_instrument_name or None,
            "long_strike": format_decimal(group.long_strike, 8) if group.long_strike > 0 else None,
            "covered_underlying_quantity": format_decimal(group.covered_underlying_quantity, 8),
            "entry_credit": format_decimal(group.entry_credit, 8),
            "entry_fee": format_decimal(group.entry_fee, 8),
            "entry_net_apr": format_decimal(group.entry_net_apr, 8),
            "entry_timestamp_ms": group.entry_timestamp_ms,
            "max_loss": format_decimal(group.max_loss, 8),
            "current_debit": format_decimal(group.current_debit, 8),
            "current_close_fee": format_decimal(group.current_close_fee, 8),
            "profit_capture": format_decimal(group.profit_capture, 8),
            "short_delta": format_decimal(group.short_delta, 8),
            "loss_pct_of_max_loss": format_decimal(group.loss_pct_of_max_loss, 8),
            "status": group.status,
            "last_action": group.last_action,
            "close_reason": group.close_reason or None,
            "closed_timestamp_ms": group.closed_timestamp_ms,
            "spot_exit_status": group.spot_exit_status or None,
            "spot_exit_amount": format_decimal(group.spot_exit_amount, 8) if group.spot_exit_amount > 0 else None,
            "spot_exit_instrument_name": group.spot_exit_instrument_name or None,
            "spot_exit_order_id": group.spot_exit_order_id or None,
            "spot_exit_reason": group.spot_exit_reason or None,
            "realized_close_debit": format_decimal(group.realized_close_debit, 8) if group.realized_close_debit is not None else None,
            "realized_close_fee": format_decimal(group.realized_close_fee, 8) if group.realized_close_fee is not None else None,
            "realized_pnl": format_decimal(group.realized_pnl, 8) if group.realized_pnl is not None else None,
            "realized_return_on_max_loss": format_decimal(group.realized_return_on_max_loss, 8)
            if group.realized_return_on_max_loss is not None
            else None,
            "realized_annualized_return": format_decimal(group.realized_annualized_return, 8)
            if group.realized_annualized_return is not None
            else None,
            "unrealized_usdc_estimate": format_decimal(unrealized_usdc, 8),
            "unrealized_coin_native": None,
            "short_floating_profit_loss": None,
            "short_has_floating_profit_loss": False,
            "short_floating_profit_loss_usd": None,
            "short_has_floating_profit_loss_usd": False,
        }
        coll = self._group_collateral_currency(group).upper()
        if coll in ("BTC", "ETH") and orderbook_cache is not None:
            idx = self._currency_index_price(coll, orderbook_cache)
            if idx > 0:
                payload["unrealized_coin_native"] = format_decimal(unrealized_usdc / idx, 12)
        if short_position is not None and short_position.instrument_name == group.short_instrument_name:
            payload["short_average_price"] = format_decimal(short_position.average_price, 8)
            payload["short_mark_price"] = format_decimal(short_position.mark_price, 8)
            payload["short_has_floating_profit_loss"] = short_position.has_floating_profit_loss
            if short_position.has_floating_profit_loss:
                payload["short_floating_profit_loss"] = format_decimal(
                    short_position.floating_profit_loss,
                    8,
                )
            payload["short_has_floating_profit_loss_usd"] = short_position.has_floating_profit_loss_usd
            if short_position.has_floating_profit_loss_usd:
                payload["short_floating_profit_loss_usd"] = format_decimal(
                    short_position.floating_profit_loss_usd,
                    8,
                )
        else:
            payload["short_average_price"] = None
            payload["short_mark_price"] = None
        return payload

    @staticmethod
    def _order_payload(order: OpenOrder) -> dict[str, Any]:
        return {
            "order_id": order.order_id,
            "instrument_name": order.instrument_name,
            "direction": order.direction,
            "order_state": order.order_state,
            "order_type": order.order_type,
            "amount": format_decimal(order.amount, 8),
            "filled_amount": format_decimal(order.filled_amount, 8),
            "price": format_decimal(order.price, 8),
            "average_price": format_decimal(order.average_price, 8),
            "post_only": order.post_only,
            "reduce_only": order.reduce_only,
            "label": order.label,
            "creation_timestamp_ms": order.creation_timestamp_ms,
        }

    @staticmethod
    def _position_payload(position: Position) -> dict[str, Any]:
        return {
            "instrument_name": position.instrument_name,
            "direction": position.direction,
            "kind": position.kind,
            "size": format_decimal(position.size, 8),
            "size_currency": format_decimal(position.size_currency, 8),
            "mark_price": format_decimal(position.mark_price, 8),
            "average_price": format_decimal(position.average_price, 8),
            "index_price": format_decimal(position.index_price, 4),
            "floating_profit_loss": format_decimal(position.floating_profit_loss, 8),
            "has_floating_profit_loss": position.has_floating_profit_loss,
            "floating_profit_loss_usd": format_decimal(position.floating_profit_loss_usd, 8),
            "has_floating_profit_loss_usd": position.has_floating_profit_loss_usd,
            "delta": format_decimal(position.delta, 8),
        }
