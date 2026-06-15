from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC
from decimal import Decimal
from typing import Any

from ..client import DeribitClient
from ..config import BotConfig
from ..entry_gates import (
    candidate_entry_halted,
    entry_cooldown_active,
    last_entry_timestamp_ms_by_book,
    open_group_count_for_book,
    underlying_entry_halted,
)
from ..fees import option_trade_fee_native, option_trade_fee_usdc, premium_value_usdc
from ..live_heartbeat import LiveHeartbeatRecord, heartbeat_path_for_state, write_live_heartbeat
from ..models import (
    AccountSummary,
    NakedPutCandidate,
    OpenOrder,
    OptionInstrument,
    OrderBookSnapshot,
    Position,
    RiskRegime,
    StrategyState,
    TradeGroup,
    is_phantom_reconcile_close,
    normalize_strategy_name,
    open_short_instrument_names,
)
from ..state import StrategyStateStore, load_performance_exclusion_group_ids
from ..strategy import StrategySelector
from ..trade_apr import realized_apr_from_close
from ..trade_journal import (
    TradeJournalStore,
    ingest_engine_actions,
    journal_db_path_for_state,
    scope_key_for_state,
)
from ..utils import (
    align_option_order_amount,
    format_decimal,
    ms_to_datetime,
    parse_option_name,
    safe_div,
    to_decimal,
    utc_now,
    utc_now_ms,
)
from ..vol_metrics import (
    dvol_iv_rank_from_daily_rows,
    index_chart_close_series,
    iv_minus_rv_spread,
    realized_vol_annualized_from_index_series,
    trend_signal_from_index_series,
)
from .context import (
    LOGGER,
    ExchangePrefetch,
    RuntimeContext,
)


class EngineBase:
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
        self._trade_journal_store: TradeJournalStore | None = None

    def _journal_scope_key(self) -> str:
        return scope_key_for_state(self.config.state_file)

    def _telegram_scope(self) -> dict[str, str]:
        parts = self.config.state_file.parts
        investor_id = "local"
        slug = self.config.order_label_prefix
        try:
            idx = parts.index("investors")
            if idx + 2 <= len(parts) - 1:
                investor_id = parts[idx + 1]
                slug = self.config.state_file.stem
        except ValueError:
            pass
        return {
            "investor_id": investor_id,
            "slug": slug,
            "strategy": self.config.option_strategy,
            "deribit_env": self.config.env,
        }

    def _telegram_alert(
        self,
        title: str,
        *,
        body: str = "",
        event_key: str,
        level: str = "warning",
        extra: dict[str, Any] | None = None,
    ) -> None:
        from ..telegram_alerts import format_alert_message, send_telegram_alert

        scope = self._telegram_scope()
        message = format_alert_message(
            title=title,
            body=body,
            level=level,
            extra=extra,
            **scope,
        )
        send_telegram_alert(message, event_key=event_key, level=level)

    def _write_live_heartbeat(
        self,
        *,
        cycle: int,
        regime: str | None = None,
        last_error: str | None = None,
    ) -> None:
        scope = self._telegram_scope()
        record = LiveHeartbeatRecord(
            ts_ms=utc_now_ms(),
            cycle=cycle,
            regime=regime,
            last_error=last_error,
            investor_id=scope["investor_id"],
            slug=scope["slug"],
            live=True,
        )
        write_live_heartbeat(heartbeat_path_for_state(self.config.state_file), record)

    def _trade_journal(self) -> TradeJournalStore:
        if self._trade_journal_store is None:
            self._trade_journal_store = TradeJournalStore(journal_db_path_for_state(self.config.state_file))
        return self._trade_journal_store

    def _persist_trade_journal_actions(self, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        try:
            inserted = ingest_engine_actions(
                self._trade_journal(),
                scope_key=self._journal_scope_key(),
                actions=actions,
                default_strategy=self.config.option_strategy,
            )
            if inserted:
                LOGGER.debug("trade journal: recorded %s fill(s)", inserted)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("trade journal persist failed: %s", exc)

    def _persist_trade_journal_result(self, result: dict[str, Any] | None) -> None:
        if not result:
            return
        self._persist_trade_journal_actions([result])

    def _repair_reconciled_bot_income_exits_in_state(self, state: StrategyState) -> bool:
        """Re-tag bot take-profit fills that were saved as reconciled_external after a close crash."""
        from ..trade_journal_backfill import (
            reconcile_profit_sweep_from_exchange,
            repair_reconciled_bot_income_exit_group,
        )

        changed = False
        try:
            journal = self._trade_journal()
            scope = self._journal_scope_key()
        except Exception:  # noqa: BLE001
            return False
        for group in state.groups:
            if group.status != "closed" or not group.group_id:
                continue
            try:
                executions = journal.list_executions(scope, group_id=group.group_id, limit=50)
            except Exception:  # noqa: BLE001
                continue
            if repair_reconciled_bot_income_exit_group(
                group,
                executions,
                order_label_prefix=self.config.order_label_prefix,
                profit_sweep_enabled=self.config.covered_call_profit_sweep_enabled,
            ):
                changed = True
            if self.config.has_private_credentials and reconcile_profit_sweep_from_exchange(
                group,
                client=self.client,
                order_label_prefix=self.config.order_label_prefix,
            ):
                changed = True
        return changed

    def _book_equity_native(
        self,
        collateral_currency: str,
        summaries: dict[str, AccountSummary] | None = None,
    ) -> Decimal:
        book = str(collateral_currency or "USDC").upper()
        rows = summaries if summaries is not None else self._account_summaries_by_currency()
        summary = rows.get(book)
        if summary is None:
            return Decimal("0")
        return summary.equity

    def _realized_pnl_native_for_apr_book(
        self,
        group: TradeGroup,
        realized_pnl_usdc: Decimal,
        *,
        index_price_usd: Decimal,
    ) -> Decimal:
        book = self._group_collateral_currency(group).upper()
        if book == "USDC":
            return realized_pnl_usdc
        if group.realized_pnl_collateral_native is not None:
            return group.realized_pnl_collateral_native
        computed = group.compute_coin_profit_native(
            allow_ledger_spot_infer=index_price_usd > 0,
        )
        if computed is not None:
            return computed
        if index_price_usd <= 0:
            return Decimal("0")
        return realized_pnl_usdc / index_price_usd

    def _coin_profit_native_for_sweep(self, group: TradeGroup) -> Decimal | None:
        """Authoritative realized premium profit for profit-sweep sizing."""
        from ..profit_sweep_ops import realized_spot_profit_native_for_group

        native = realized_spot_profit_native_for_group(group)
        if native is None:
            return None
        group.realized_pnl_collateral_native = native
        close_idx = group.close_index_usd
        if close_idx is not None and close_idx > 0:
            group.backfill_realized_pnl_usdc(spot_index_usd=close_idx)
        return native

    def _resolve_fee_discount_first_trade_ms(self) -> int | None:
        cached = getattr(self, "_fee_discount_first_trade_ms", None)
        if cached is not None:
            return cached if cached > 0 else None
        from ..fee_discount import resolve_first_option_trade_timestamp_ms

        resolved = resolve_first_option_trade_timestamp_ms(
            state_path=self.config.state_file,
            client=self.client,
        )
        self._fee_discount_first_trade_ms = int(resolved or 0)
        if hasattr(self, "strategy") and resolved is not None:
            self.strategy.first_option_trade_timestamp_ms = resolved
        return resolved

    def _option_fee_discount_rate_at(self, at_timestamp_ms: int | None = None) -> Decimal:
        from ..utils import utc_now_ms

        at_ms = int(at_timestamp_ms if at_timestamp_ms is not None else utc_now_ms())
        if self.config.option_fee_discount_rate <= 0 or self.config.option_fee_discount_months <= 0:
            return Decimal("0")
        # Populate the shared FeeDiscountContext (owned by the strategy) so
        # screening and execution resolve the same anchor/rate.
        self._resolve_fee_discount_first_trade_ms()
        return self.strategy.fee_discount.rate_at(at_ms)

    def _option_fee_native(
        self,
        *,
        premium: Decimal,
        quantity: Decimal,
        index_price: Decimal,
        quote_currency: str,
        settlement_currency: str,
        at_timestamp_ms: int | None = None,
    ) -> Decimal:
        return option_trade_fee_native(
            index_price=index_price,
            premium=premium,
            quantity=quantity,
            fee_rate=self.config.option_fee_rate,
            fee_cap_rate=self.config.option_fee_cap_rate,
            quote_currency=quote_currency,
            settlement_currency=settlement_currency,
            fee_discount_rate=self._option_fee_discount_rate_at(at_timestamp_ms),
        )

    def _compute_realized_pnl_collateral_native(
        self,
        group: TradeGroup,
        *,
        short_entry_price: Decimal,
        short_close_price: Decimal,
        index_at_entry: Decimal,
        index_at_close: Decimal,
        short_instrument: OptionInstrument,
        long_entry_price: Decimal | None = None,
        long_close_price: Decimal | None = None,
        long_instrument: OptionInstrument | None = None,
        realized_pnl_usdc: Decimal | None = None,
    ) -> Decimal | None:
        book = self._group_collateral_currency(group).upper()
        if book == "USDC":
            return realized_pnl_usdc
        if short_entry_price > 0:
            group.short_entry_average_price = short_entry_price
        if short_close_price > 0:
            group.short_close_average_price = short_close_price
        if index_at_entry > 0:
            group.entry_index_usd = index_at_entry
        if index_at_close > 0:
            group.close_index_usd = index_at_close
        if long_entry_price is not None and long_entry_price > 0:
            group.long_entry_average_price = long_entry_price
        if long_close_price is not None and long_close_price > 0:
            group.long_close_average_price = long_close_price
        return group.compute_realized_pnl_native()

    def _finalize_close_collateral_native(
        self,
        group: TradeGroup,
        *,
        realized_pnl_usdc: Decimal,
        short_close_price: Decimal,
        index_at_close: Decimal,
        short_instrument: OptionInstrument,
        long_close_price: Decimal | None = None,
        long_instrument: OptionInstrument | None = None,
    ) -> None:
        group.short_close_average_price = short_close_price
        group.close_index_usd = index_at_close
        entry_price = group.resolved_short_entry_price()
        index_at_entry = group.entry_index_usd if group.entry_index_usd > 0 else index_at_close
        native = self._compute_realized_pnl_collateral_native(
            group,
            short_entry_price=entry_price,
            short_close_price=short_close_price,
            index_at_entry=index_at_entry,
            index_at_close=index_at_close,
            short_instrument=short_instrument,
            long_entry_price=group.long_entry_average_price if group.long_entry_average_price > 0 else None,
            long_close_price=long_close_price,
            long_instrument=long_instrument,
            realized_pnl_usdc=realized_pnl_usdc,
        )
        if native is not None:
            group.realized_pnl_collateral_native = native
            book = self._group_collateral_currency(group).upper()
            if book == "USDC":
                group.realized_pnl = realized_pnl_usdc
            elif index_at_close > 0:
                group.backfill_realized_pnl_usdc(spot_index_usd=index_at_close)

    def _option_contract_size(self, instrument_name: str) -> Decimal:
        try:
            payload = self.client.get_instrument(instrument_name)
            return to_decimal(payload.get("contract_size") or "1")
        except Exception:  # noqa: BLE001
            return Decimal("1")

    def _entry_net_apr_for_group(self, group: TradeGroup) -> Decimal:
        contract_size = self._option_contract_size(group.short_instrument_name)
        if group.entry_index_usd <= 0 and group.collateral_book().upper() != "USDC":
            cache: dict[str, OrderBookSnapshot] = {}
            group.entry_index_usd = self._currency_index_price(group.currency, cache)
        apr = group.entry_net_apr_at_open(contract_size=contract_size)
        if apr > 0:
            return apr
        return group.entry_net_apr

    def _attach_open_group_stats(self, group: TradeGroup) -> None:
        """Snapshot book equity at open; persist to state + trade journal DB."""
        book = self._group_collateral_currency(group)
        summaries = self._account_summaries_by_currency()
        equity = self._book_equity_native(book, summaries)
        group.entry_book_equity = equity
        if group.entry_index_usd <= 0 and book.upper() != "USDC":
            cache: dict[str, OrderBookSnapshot] = {}
            group.entry_index_usd = self._currency_index_price(group.currency, cache)
        group.entry_net_apr = self._entry_net_apr_for_group(group)
        try:
            self._trade_journal().record_group_stats_open(
                scope_key=self._journal_scope_key(),
                group_id=group.group_id,
                collateral_book=book,
                opened_ts_ms=group.entry_timestamp_ms,
                entry_book_equity=group.entry_book_equity,
                entry_net_apr=group.entry_net_apr,
                entry_credit_usdc=group.entry_credit,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("trade journal open stats failed for %s: %s", group.group_id, exc)

    def _snapshot_close_apr_on_equity(
        self,
        group: TradeGroup,
        *,
        realized_pnl: Decimal,
        closed_timestamp_ms: int,
        summaries: dict[str, AccountSummary] | None = None,
        index_price_usd: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        book = self._group_collateral_currency(group)
        close_equity = self._book_equity_native(book, summaries)
        idx = index_price_usd if index_price_usd is not None else Decimal("0")
        if idx <= 0 and book.upper() != "USDC":
            cache: dict[str, OrderBookSnapshot] = {}
            idx = self._currency_index_price(group.currency, cache)
        if group.realized_pnl_collateral_native is not None and book.upper() != "USDC":
            pnl_native = group.realized_pnl_collateral_native
        else:
            pnl_native = self._realized_pnl_native_for_apr_book(
                group,
                realized_pnl,
                index_price_usd=idx,
            )
        contract_size = self._option_contract_size(group.short_instrument_name)
        apr = realized_apr_from_close(
            strategy=group.strategy or self.config.option_strategy,
            collateral_currency=book,
            option_type=group.option_type,
            quantity=group.quantity,
            contract_size=contract_size,
            strike=group.short_strike,
            index_price_usd=idx,
            estimated_im_collateral=group.estimated_im_collateral,
            covered_underlying_quantity=group.covered_underlying_quantity,
            pnl_collateral_native=pnl_native,
            entry_timestamp_ms=group.entry_timestamp_ms,
            closed_timestamp_ms=closed_timestamp_ms,
        )
        return close_equity, apr

    def _persist_group_stats_close(self, group: TradeGroup) -> None:
        if group.closed_timestamp_ms is None or group.realized_pnl is None:
            return
        if group.close_book_equity is None or group.realized_apr_on_equity is None:
            return
        try:
            self._trade_journal().record_group_stats_close(
                scope_key=self._journal_scope_key(),
                group_id=group.group_id,
                collateral_book=self._group_collateral_currency(group),
                closed_ts_ms=group.closed_timestamp_ms,
                close_book_equity=group.close_book_equity,
                realized_pnl_usdc=group.realized_pnl,
                realized_apr_on_equity=group.realized_apr_on_equity,
                holding_days=group.holding_days,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("trade journal close stats failed for %s: %s", group.group_id, exc)

    def _journal_reconcile_close(self, group: TradeGroup, *, closed_timestamp_ms: int) -> None:
        try:
            close_premium = group.short_close_average_price
            if close_premium is None or close_premium <= 0:
                close_premium = None
            self._trade_journal().record_reconcile_close(
                scope_key=self._journal_scope_key(),
                group_id=group.group_id,
                instrument_name=group.short_instrument_name,
                strategy=group.strategy or self.config.option_strategy,
                reason=group.close_reason or "reconciled_external",
                quantity=group.quantity,
                close_debit_usdc=group.realized_close_debit,
                close_premium_native=close_premium,
                closed_timestamp_ms=closed_timestamp_ms,
                realized_pnl=group.realized_pnl,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("trade journal reconcile failed for %s: %s", group.group_id, exc)

    def _reconcile_buy_close_premium_native(
        self,
        group: TradeGroup,
        *,
        short_book: OrderBookSnapshot,
        closed_timestamp_ms: int,
    ) -> Decimal:
        """Actual or best-estimate buy-to-close premium per contract in collateral coin."""
        if self.config.has_private_credentials:
            try:
                start_ms = max(int(group.entry_timestamp_ms or 0) - 60_000, 0)
                end_ms = int(closed_timestamp_ms) + 300_000
                payload = self.client.get_user_trades_by_instrument(
                    group.short_instrument_name,
                    start_timestamp=start_ms,
                    end_timestamp=end_ms,
                    count=200,
                    sorting="asc",
                    historical=True,
                )
                buy_trades = [
                    row for row in (payload.get("trades") or []) if str(row.get("direction") or "").lower() == "buy"
                ]
                if buy_trades:
                    total_amount = Decimal("0")
                    weighted = Decimal("0")
                    for trade in buy_trades:
                        amount = to_decimal(trade.get("amount"))
                        price = to_decimal(trade.get("price"))
                        if amount <= 0 or price <= 0:
                            continue
                        weighted += price * amount
                        total_amount += amount
                    if total_amount > 0 and total_amount >= group.quantity * Decimal("0.5"):
                        return weighted / total_amount
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(
                    "reconcile trade lookup failed group=%s instrument=%s: %s",
                    group.group_id,
                    group.short_instrument_name,
                    exc,
                )
        close_premium = short_book.buy_close_premium(max_spread_ratio=self.config.early_exit_max_spread_ratio)
        if close_premium <= 0:
            close_premium = max(short_book.best_ask_price, short_book.mark_price)
        return max(close_premium, Decimal("0"))

    def _reconcile_coin_close_ledger(
        self,
        group: TradeGroup,
        *,
        short_book: OrderBookSnapshot,
        short_instrument: OptionInstrument,
        closed_timestamp_ms: int,
    ) -> Decimal | None:
        """Coin collateral: resolve native close premium, then derive USDC ledger once."""
        index_price = short_book.index_price
        if index_price <= 0:
            index_price = group.close_index_usd or group.entry_index_usd or Decimal("0")
        close_premium = self._reconcile_buy_close_premium_native(
            group,
            short_book=short_book,
            closed_timestamp_ms=closed_timestamp_ms,
        )
        if close_premium <= 0 or index_price <= 0:
            return None
        close_fee_collateral = self._option_fee_native(
            premium=close_premium,
            quantity=group.quantity,
            index_price=index_price,
            quote_currency=short_instrument.quote_currency,
            settlement_currency=short_instrument.settlement_currency,
        )
        group.apply_coin_close_from_native(
            short_close_premium=close_premium,
            index_usd=index_price,
            close_fee_collateral=close_fee_collateral,
        )
        return group.realized_close_debit

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
        pnl_native = self._realized_pnl_native_for_apr_book(
            group,
            realized_pnl_usdc,
            index_price_usd=index_price_usd,
        )
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

    def fetch_exchange_prefetch(self) -> ExchangePrefetch:
        """One Deribit round-trip bundle reused by multi-strategy dashboard rows."""
        summaries = self._account_summaries_by_currency()
        open_orders = (
            [OpenOrder.from_api(row) for row in self.client.get_open_orders(kind="any")]
            if self.config.has_private_credentials
            else []
        )
        positions = (
            [Position.from_api(row) for row in self.client.get_positions(currency="any", kind="any")]
            if self.config.has_private_credentials
            else []
        )
        option_positions = [item for item in positions if item.kind == "option"]
        future_positions = [
            item for item in positions if item.kind in {"future", "future_combo"} or "PERPETUAL" in item.instrument_name
        ]
        return ExchangePrefetch(
            summaries=summaries,
            open_orders=open_orders,
            positions=positions,
            option_positions=option_positions,
            future_positions=future_positions,
            future_markets_by_name=self._load_perpetual_markets(),
            markets_by_currency=self._load_supported_option_markets(),
        )

    def status(self) -> dict[str, Any]:
        context = self._load_runtime()
        self.state_store.save(context.state)
        return self._status_payload(context)

    def status_with_exchange_prefetch(
        self,
        prefetch: ExchangePrefetch,
        *,
        dashboard_display: bool = False,
    ) -> dict[str, Any]:
        context, reconcile_closed = self._load_runtime_from_exchange(
            prefetch,
            dashboard_display=dashboard_display,
        )
        if not dashboard_display or reconcile_closed:
            self.state_store.save(context.state)
        return self._status_payload(context)

    def report(self, *, days: int = 30) -> dict[str, Any]:
        state = self.state_store.load()
        open_groups = self._open_groups(state)
        excluded_group_ids = load_performance_exclusion_group_ids(self.state_store.path)
        open_short_names = open_short_instrument_names(state.groups)
        all_closed_groups = [group for group in state.groups if group.status == "closed"]
        closed_groups = [
            group
            for group in all_closed_groups
            if group.group_id not in excluded_group_ids
            and not is_phantom_reconcile_close(group, open_short_names=open_short_names)
        ]
        realized_groups = [
            group for group in closed_groups if group.realized_pnl is not None and group.closed_timestamp_ms is not None
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
            open_trades = self._trade_groups_payload(open_groups, runtime.option_positions, runtime.orderbook_cache)
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

    def _status_payload(self, context: RuntimeContext) -> dict[str, Any]:
        underlying_index_usd: dict[str, str] = {}
        for sym in ("BTC", "ETH"):
            idx = self._currency_index_price(sym, context.orderbook_cache)
            underlying_index_usd[sym] = format_decimal(idx, 4) if idx > 0 else "0"
        payload: dict[str, Any] = {
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
        if self.config.option_strategy == "covered_call" and self.config.has_private_credentials:
            try:
                from ..profit_sweep_repair import premium_sweep_fill_stats_by_book

                fill_stats = premium_sweep_fill_stats_by_book(
                    self.client,
                    self.config.order_label_prefix,
                )
                if fill_stats:
                    payload["premium_sweep_fill_stats_by_book"] = fill_stats
            except Exception:  # noqa: BLE001
                LOGGER.debug("premium_sweep_fill_stats_by_book failed", exc_info=True)
        return payload

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
            OptionInstrument.from_api(row) for row in self.client.get_instruments("USDC", kind="option", expired=False)
        ]
        linear_by_currency: dict[str, list[OptionInstrument]] = {
            currency: [] for currency in self.config.managed_currencies
        }
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
        # Map each wanted perp to the currency bucket it is listed under:
        # linear USDC perps are returned by ``get_instruments(currency="USDC")``,
        # inverse perps under their coin.
        wanted = {self._perp_instrument(currency) for currency in self.config.managed_currencies}
        query_currencies: set[str] = set()
        for perp_name in wanted:
            if perp_name.endswith("_USDC-PERPETUAL"):
                query_currencies.add("USDC")
            else:
                query_currencies.add(perp_name.split("-", 1)[0])
        for query_currency in query_currencies:
            try:
                rows = self.client.get_instruments(query_currency, kind="future", expired=False)
            except Exception as exc:
                LOGGER.warning("failed to load future instruments for %s (%s)", query_currency, exc)
                continue
            for row in rows:
                instrument = OptionInstrument.from_api(row)
                if instrument.instrument_name in wanted:
                    markets[instrument.instrument_name] = instrument
        return markets

    def _get_orderbook(self, instrument_name: str, cache: dict[str, OrderBookSnapshot]) -> OrderBookSnapshot:
        if instrument_name not in cache:
            cache[instrument_name] = OrderBookSnapshot.from_api(self.client.get_order_book(instrument_name))
        return cache[instrument_name]

    def _prefetch_scan_book_summaries(
        self,
        markets_by_currency: dict[str, list[OptionInstrument]],
        orderbook_cache: dict[str, OrderBookSnapshot],
    ) -> None:
        """Pre-seed no-bid strikes from one batch summary per currency to avoid N+1 order-book calls.

        A strike with no bid is rejected at the ``best_bid<=0`` gate before delta
        is evaluated, so seeding the cache with a bid=0 snapshot is
        behavior-preserving for both candidate scanning and rejection
        diagnostics, while skipping a per-instrument ``get_order_book`` call.
        """
        if not self.config.scan_book_summary_prefilter:
            return
        for currency in sorted(markets_by_currency):
            if not markets_by_currency.get(currency):
                continue
            try:
                rows = self.client.get_book_summary_by_currency(currency, kind="option")
            except Exception:
                continue
            for row in rows:
                name = str(row.get("instrument_name") or "")
                if not name or name in orderbook_cache:
                    continue
                snapshot = OrderBookSnapshot.from_book_summary(row)
                if snapshot.best_bid_price <= 0:
                    orderbook_cache[name] = snapshot

    def _supports_option_market(self, market: OptionInstrument) -> bool:
        if self.config.option_markets_profile == "linear_usdc":
            return (
                market.quote_currency == "USDC"
                and market.settlement_currency == "USDC"
                and market.base_currency in self.config.managed_currencies
            )
        if (
            self.config.option_markets_profile == "inverse_native"
            and market.quote_currency == "USDC"
            and market.settlement_currency == "USDC"
        ):
            return False
        if market.quote_currency == "USDC" and market.settlement_currency == "USDC":
            return True
        return market.instrument_type == "reversed" or (
            market.base_currency
            and market.quote_currency in {"", market.base_currency}
            and market.settlement_currency == market.base_currency
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
        at_timestamp_ms: int | None = None,
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
            fee_discount_rate=self._option_fee_discount_rate_at(at_timestamp_ms),
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

    def _sum_trade_fees_native(self, trades: list[dict[str, Any]], collateral_currency: str) -> Decimal:
        """Sum trade fees already charged in the collateral book (BTC/ETH)."""
        book = collateral_currency.upper()
        total = Decimal("0")
        for trade in trades:
            fee = to_decimal(trade.get("fee"))
            if fee <= 0:
                continue
            fee_currency = str(trade.get("fee_currency") or "").upper()
            if fee_currency == book:
                total += fee
        return total

    def _short_leg_ledger(
        self,
        *,
        fee_sign: Decimal,
        premium: Decimal,
        quantity: Decimal,
        index_price: Decimal,
        trades: list[dict[str, Any]],
        instrument: OptionInstrument,
        collateral_currency: str,
        at_timestamp_ms: int | None = None,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Cash-flow ledger for one short leg; ``fee_sign`` is -1 for entry credit, +1 for close debit.

        Returns ``(value_usdc, fee_usdc, fee_collateral)`` where ``value_usdc`` is a
        net credit (entry) or total debit (close) in USDC.
        """
        usdc_linear = instrument.quote_currency.upper() == "USDC" and instrument.settlement_currency.upper() == "USDC"
        book = collateral_currency.upper()
        if usdc_linear or book == "USDC":
            fee_usdc = self._sum_trade_fees_usdc(trades)
            if fee_usdc <= 0:
                fee_usdc = self._option_fee_usdc(
                    premium=premium,
                    quantity=quantity,
                    index_price=index_price,
                    base_currency=instrument.base_currency,
                    quote_currency=instrument.quote_currency,
                    settlement_currency=instrument.settlement_currency,
                    at_timestamp_ms=at_timestamp_ms,
                )
            gross_usdc = self._premium_value_usdc(
                premium=premium,
                quantity=quantity,
                index_price=index_price,
                instrument=instrument,
            )
            return gross_usdc + fee_sign * fee_usdc, fee_usdc, Decimal("0")

        fee_collateral = self._sum_trade_fees_native(trades, book)
        if fee_collateral <= 0:
            fee_collateral = self._option_fee_native(
                premium=premium,
                quantity=quantity,
                index_price=index_price,
                quote_currency=instrument.quote_currency,
                settlement_currency=instrument.settlement_currency,
                at_timestamp_ms=at_timestamp_ms,
            )
        gross_native = premium * quantity
        total_native = gross_native + fee_sign * fee_collateral
        if index_price <= 0:
            return Decimal("0"), Decimal("0"), fee_collateral
        return total_native * index_price, fee_collateral * index_price, fee_collateral

    def _short_entry_ledger(
        self,
        *,
        premium: Decimal,
        quantity: Decimal,
        index_price: Decimal,
        trades: list[dict[str, Any]],
        instrument: OptionInstrument,
        collateral_currency: str,
        at_timestamp_ms: int | None = None,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return ``(net_credit_usdc, entry_fee_usdc, entry_fee_collateral)`` for one short leg."""
        return self._short_leg_ledger(
            fee_sign=Decimal("-1"),
            premium=premium,
            quantity=quantity,
            index_price=index_price,
            trades=trades,
            instrument=instrument,
            collateral_currency=collateral_currency,
            at_timestamp_ms=at_timestamp_ms,
        )

    def _close_short_ledger(
        self,
        *,
        premium: Decimal,
        quantity: Decimal,
        index_price: Decimal,
        trades: list[dict[str, Any]],
        instrument: OptionInstrument,
        collateral_currency: str,
        at_timestamp_ms: int | None = None,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return ``(total_close_debit_usdc, close_fee_usdc, close_fee_collateral)`` for buy-to-close."""
        return self._short_leg_ledger(
            fee_sign=Decimal("1"),
            premium=premium,
            quantity=quantity,
            index_price=index_price,
            trades=trades,
            instrument=instrument,
            collateral_currency=collateral_currency,
            at_timestamp_ms=at_timestamp_ms,
        )

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
        effective_cap = (
            self._effective_capital(total_equity_usdc)
            if total_equity_usdc is not None
            else self.config.reference_capital_usdc
        )
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
        return len([group for group in self._open_groups(state) if self._group_strategy_key(group) == strategy])

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
                if group.currency == ccy and (strategy is None or self._group_strategy_key(group) == strategy)
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

    def _strategy_at_book_limit(self, state: StrategyState, book: str, *, strategy: str | None = None) -> bool:
        return (
            self.config.max_groups_per_book > 0
            and open_group_count_for_book(self._open_groups(state), book, strategy=strategy)
            >= self.config.max_groups_per_book
        )

    def _last_entry_timestamp_by_book(self, state: StrategyState) -> dict[str, int]:
        return last_entry_timestamp_ms_by_book(state.groups)

    def _book_entry_cooldown_active(self, state: StrategyState, book: str) -> bool:
        return entry_cooldown_active(
            book=book,
            last_entry_by_book=self._last_entry_timestamp_by_book(state),
            now_ms=utc_now_ms(),
            cooldown_minutes=self.config.entry_cooldown_minutes,
        )

    def _filter_enterable_candidates(
        self,
        context: RuntimeContext,
        candidates: list[NakedPutCandidate],
    ) -> list[NakedPutCandidate]:
        """Keep scan winners whose underlying regime and collateral book may still enter."""
        return [c for c in candidates if not candidate_entry_halted(context.snapshot, c)]

    def _underlying_entry_halted(self, context: RuntimeContext, underlying: str) -> bool:
        return underlying_entry_halted(context.snapshot, underlying)

    def _refresh_vol_entry_context(self) -> None:
        need_vol = self.config.enable_iv_entry_gate or self.config.enable_dynamic_target_delta
        need_trend = self.config.enable_trend_side_bias
        if not need_vol and not need_trend:
            self.strategy.update_vol_entry_context()
            return
        iv_rank_by_currency: dict[str, Decimal] = {}
        iv_minus_rv_by_currency: dict[str, Decimal] = {}
        trend_by_currency: dict[str, Decimal] = {}
        end_timestamp = utc_now_ms()
        start_timestamp = end_timestamp - (self.config.iv_rank_lookback_days * 24 * 3600 * 1000)
        for currency in self.config.managed_currencies:
            ccy = currency.upper()
            current_iv = Decimal("0")
            if need_vol:
                try:
                    dvol_payload = self.client.get_volatility_index_data(
                        ccy,
                        start_timestamp=start_timestamp,
                        end_timestamp=end_timestamp,
                        resolution="1D",
                    )
                    dvol_rows = dvol_payload.get("data") or []
                    rank = dvol_iv_rank_from_daily_rows(
                        dvol_rows,
                        ts_ms=end_timestamp,
                        lookback_days=self.config.iv_rank_lookback_days,
                    )
                    if rank is not None:
                        iv_rank_by_currency[ccy] = rank
                    current_iv = to_decimal(dvol_rows[-1][4]) if dvol_rows else Decimal("0")
                except Exception:
                    current_iv = Decimal("0")
            index_series: list[tuple[int, Decimal]] = []
            try:
                index_payload = self.client.get_index_chart_data(f"{ccy.lower()}_usd", range_name="1y")
                index_series = index_chart_close_series(index_payload)
            except Exception:
                index_series = []
            if need_vol and index_series:
                try:
                    rv = realized_vol_annualized_from_index_series(
                        index_series,
                        end_ts_ms=end_timestamp,
                        window=self.config.rv_lookback_days,
                    )
                    if current_iv > 0 and rv is not None:
                        spread = iv_minus_rv_spread(iv=current_iv / Decimal("100"), rv=rv)
                        if spread is not None:
                            iv_minus_rv_by_currency[ccy] = spread
                except Exception:
                    pass
            if need_trend and index_series:
                try:
                    trend = trend_signal_from_index_series(
                        index_series,
                        end_ts_ms=end_timestamp,
                        ma_window=self.config.trend_ma_days,
                        ref_pct=self.config.trend_side_ref_pct,
                    )
                    if trend is not None:
                        trend_by_currency[ccy] = trend
                except Exception:
                    pass
        self.strategy.update_vol_entry_context(
            iv_rank_by_currency=iv_rank_by_currency,
            iv_minus_rv_by_currency=iv_minus_rv_by_currency,
            trend_by_currency=trend_by_currency,
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

    def _covered_call_open_group_count(self, state: StrategyState, currency: str) -> int:
        return self._open_group_count_for_currency(state, currency.upper(), strategy="covered_call")

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

    def _projected_max_profit_run_rate(
        self, state: StrategyState, *, collateral_currency: str | None = None
    ) -> Decimal:
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
        index_price_usd: Decimal | None = None,
    ) -> None:
        group.status = "closed"
        group.last_action = reason
        group.close_incomplete_streak = 0
        group.close_reason = reason
        group.closed_timestamp_ms = closed_timestamp_ms
        group.realized_close_debit = realized_close_debit
        group.realized_close_fee = realized_close_fee
        group.realized_pnl = realized_pnl
        group.realized_return_on_max_loss = realized_return_on_max_loss
        if realized_pnl is not None:
            close_equity, apr_on_equity = self._snapshot_close_apr_on_equity(
                group,
                realized_pnl=realized_pnl,
                closed_timestamp_ms=closed_timestamp_ms,
                index_price_usd=index_price_usd,
            )
            group.close_book_equity = close_equity
            group.realized_apr_on_equity = apr_on_equity
            group.realized_annualized_return = apr_on_equity
        elif realized_annualized_return is not None:
            group.realized_annualized_return = realized_annualized_return
        self._persist_group_stats_close(group)

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
        # Linear USDC-margined perp matches the USDC option collateral book.
        # (Inverse ``{CCY}-PERPETUAL`` would settle in the coin and live in a
        # different margin book.)
        return f"{currency.upper()}_USDC-PERPETUAL"

    def _perp_uses_base_amount(self, currency: str) -> bool:
        """True when the hedge perp sizes orders in base (coin) units.

        Linear USDC perps take ``amount`` in the base currency (e.g. BTC);
        inverse coin-margined perps take ``amount`` as a USD notional.
        """
        return self._perp_instrument(currency).endswith("_USDC-PERPETUAL")

    def _day_start_ms_from_key(self, day_key: str) -> int:
        """Convert a ``YYYY-MM-DD`` key back to its UTC-midnight epoch ms.

        Returns 0 if the key is missing or malformed so callers can skip the
        transaction-log query rather than crash.
        """
        if not day_key:
            return 0
        try:
            from datetime import datetime

            dt = datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=UTC)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            return 0

    def _flow_query_start_ms(self, state: StrategyState, collateral: str) -> int:
        """Earliest transaction-log timestamp for external cash-flow tallies."""
        day_start_ms = self._day_start_ms_from_key(state.day_key)
        if day_start_ms <= 0:
            return 0
        anchor_ms = state.day_equity_anchor_ms_by_book.get(collateral.upper(), 0)
        if anchor_ms <= 0:
            return day_start_ms
        return max(day_start_ms, anchor_ms)

    def _try_find_instrument(
        self, markets_by_currency: dict[str, list[OptionInstrument]], instrument_name: str
    ) -> OptionInstrument | None:
        try:
            return self._find_instrument_by_markets(markets_by_currency, instrument_name)
        except KeyError:
            return None

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
        if "USDT" in traded:
            books.add("USDT")
        return frozenset(books)

    def _total_equity_usdc(
        self, summaries: dict[str, AccountSummary], orderbook_cache: dict[str, OrderBookSnapshot]
    ) -> Decimal:
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
        if "USDT" in traded:
            usdt_summary = summaries.get("USDT")
            result["USDT"] = usdt_summary.equity if usdt_summary is not None else Decimal("0")
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
        if "USDT" in traded:
            usdt_summary = summaries.get("USDT")
            result["USDT"] = usdt_summary.equity if usdt_summary is not None else Decimal("0")
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
            if currency in ("USDC", "USDT"):
                total += amount
            else:
                total += amount * self._currency_index_price(currency, orderbook_cache)
        return total

    def _per_currency_margin_ratios(
        self,
        summaries: dict[str, AccountSummary],
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
        group.backfill_realized_pnl_collateral_native()
        group.backfill_realized_pnl_usdc()
        return {
            "group_id": group.group_id,
            "currency": group.currency,
            "collateral_currency": self._group_collateral_currency(group),
            "strategy": group.strategy or "naked_short",
            "option_type": group.option_type,
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
            "short_entry_average_price": format_decimal(group.short_entry_average_price, 8)
            if group.short_entry_average_price > 0
            else None,
            "short_close_average_price": format_decimal(group.short_close_average_price, 8)
            if group.short_close_average_price is not None
            else None,
            "entry_index_usd": format_decimal(group.entry_index_usd, 8) if group.entry_index_usd > 0 else None,
            "close_index_usd": format_decimal(group.close_index_usd, 8) if group.close_index_usd is not None else None,
            "entry_credit": group.entry_credit,
            "entry_fee": group.entry_fee,
            "entry_net_apr": group.entry_net_apr,
            "entry_book_equity": group.entry_book_equity,
            "entry_timestamp_ms": group.entry_timestamp_ms,
            "max_loss": group.max_loss,
            "realized_close_debit": group.realized_close_debit,
            "realized_close_fee": group.realized_close_fee,
            "realized_pnl_collateral_native": format_decimal(group.realized_pnl_collateral_native, 8)
            if group.realized_pnl_collateral_native is not None
            else None,
            "realized_pnl": group.realized_pnl,
            "realized_return_on_max_loss": group.realized_return_on_max_loss,
            "realized_annualized_return": group.realized_annualized_return,
            "close_book_equity": group.close_book_equity,
            "realized_apr_on_equity": group.realized_apr_on_equity,
            "covered_underlying_quantity": format_decimal(group.covered_underlying_quantity, 8)
            if group.covered_underlying_quantity > 0
            else None,
            "spot_exit_status": group.spot_exit_status or None,
            "spot_exit_amount": group.spot_exit_amount if group.spot_exit_amount > 0 else None,
            "spot_exit_instrument_name": group.spot_exit_instrument_name or None,
            "spot_exit_order_id": group.spot_exit_order_id or None,
            "spot_exit_reason": group.spot_exit_reason or None,
            "profit_sweep_status": group.profit_sweep_status or None,
            "profit_sweep_amount": format_decimal(group.profit_sweep_amount, 8)
            if group.profit_sweep_amount > 0
            else None,
            "profit_sweep_instrument_name": group.profit_sweep_instrument_name or None,
            "profit_sweep_order_id": group.profit_sweep_order_id or None,
            "profit_sweep_quote_proceeds": format_decimal(group.profit_sweep_quote_proceeds, 4)
            if group.profit_sweep_quote_proceeds > 0
            else None,
            "profit_sweep_quote_proceeds_lifetime": format_decimal(group.profit_sweep_quote_proceeds_lifetime, 4)
            if group.profit_sweep_quote_proceeds_lifetime > 0
            else None,
            "profit_sweep_reason": group.profit_sweep_reason or None,
        }

    def _group_payload(
        self,
        group: TradeGroup,
        *,
        short_position: Position | None = None,
        orderbook_cache: dict[str, OrderBookSnapshot] | None = None,
    ) -> dict[str, Any]:
        spot_usd: Decimal | None = None
        coll = self._group_collateral_currency(group).upper()
        if coll in ("BTC", "ETH") and orderbook_cache is not None:
            idx = self._currency_index_price(coll, orderbook_cache)
            if idx > 0:
                spot_usd = idx
        journal_rows: list[dict[str, Any]] | None = None
        if group.is_coin_collateral() and group.status == "closed":
            try:
                journal_rows = self._trade_journal().list_executions(
                    self._journal_scope_key(),
                    group_id=group.group_id,
                    limit=50,
                )
            except Exception:
                journal_rows = None
        group.backfill_realized_pnl_collateral_native(
            spot_index_usd=spot_usd,
            journal_executions=journal_rows,
        )
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
            "entry_book_equity": format_decimal(group.entry_book_equity, 8),
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
            "realized_close_debit": format_decimal(group.realized_close_debit, 8)
            if group.realized_close_debit is not None
            else None,
            "realized_close_fee": format_decimal(group.realized_close_fee, 8)
            if group.realized_close_fee is not None
            else None,
            "short_close_average_price": format_decimal(group.short_close_average_price, 8)
            if group.short_close_average_price is not None
            else None,
            "close_index_usd": format_decimal(group.close_index_usd, 8) if group.close_index_usd is not None else None,
            "realized_pnl_collateral_native": format_decimal(group.realized_pnl_collateral_native, 8)
            if group.realized_pnl_collateral_native is not None
            else None,
            "realized_pnl": format_decimal(group.realized_pnl, 8) if group.realized_pnl is not None else None,
            "realized_return_on_max_loss": format_decimal(group.realized_return_on_max_loss, 8)
            if group.realized_return_on_max_loss is not None
            else None,
            "realized_annualized_return": format_decimal(group.realized_annualized_return, 8)
            if group.realized_annualized_return is not None
            else None,
            "close_book_equity": format_decimal(group.close_book_equity, 8)
            if group.close_book_equity is not None
            else None,
            "realized_apr_on_equity": format_decimal(group.realized_apr_on_equity, 8)
            if group.realized_apr_on_equity is not None
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
            pnl_scale = self._group_leg_pnl_scale(short_position, group.quantity)
            payload["short_has_floating_profit_loss"] = short_position.has_floating_profit_loss
            if short_position.has_floating_profit_loss:
                payload["short_floating_profit_loss"] = format_decimal(
                    short_position.floating_profit_loss * pnl_scale,
                    8,
                )
            payload["short_has_floating_profit_loss_usd"] = short_position.has_floating_profit_loss_usd
            if short_position.has_floating_profit_loss_usd:
                payload["short_floating_profit_loss_usd"] = format_decimal(
                    short_position.floating_profit_loss_usd * pnl_scale,
                    8,
                )
        else:
            payload["short_average_price"] = None
            payload["short_mark_price"] = None
        return payload

    @staticmethod
    def _group_leg_pnl_scale(position: Position, group_quantity: Decimal) -> Decimal:
        """Scale exchange leg PnL to one trade group when multiple groups share the instrument."""
        pos_size = abs(position.size)
        if pos_size <= 0 or group_quantity <= 0:
            return Decimal("1")
        if pos_size == group_quantity:
            return Decimal("1")
        return group_quantity / pos_size

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
