from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from .fees import premium_value_native
from .utils import dte_days, parse_option_name, safe_div, to_decimal

_COIN_COLLATERAL_BOOKS = frozenset({"BTC", "ETH"})
_MIN_PLAUSIBLE_UNDERLYING_INDEX_USD = Decimal("100")
# ``reconciled_external`` within this holding window while the same short leg is still open
# is treated as a position-sync glitch (not a real close).
PHANTOM_RECONCILE_MAX_HOLDING_MS = 300_000


def open_short_instrument_names(groups: list[TradeGroup]) -> set[str]:
    return {g.short_instrument_name for g in groups if g.status != "closed" and g.short_instrument_name}


def is_phantom_reconcile_close(group: TradeGroup, *, open_short_names: set[str]) -> bool:
    if group.status != "closed":
        return False
    if (group.close_reason or "").lower() != "reconciled_external":
        return False
    entry_ms = int(group.entry_timestamp_ms or 0)
    closed_ms = group.closed_timestamp_ms
    if not closed_ms or closed_ms <= entry_ms:
        return False
    if closed_ms - entry_ms > PHANTOM_RECONCILE_MAX_HOLDING_MS:
        return False
    return group.short_instrument_name in open_short_names


class OptionSide(str, Enum):
    PUT = "put"
    CALL = "call"


def _infer_option_type(instrument_name: str) -> str:
    """Fallback guess for legacy state entries without an explicit option_type.

    Deribit option names end with ``-P`` or ``-C`` after the strike.
    """
    name = (instrument_name or "").upper()
    if name.endswith("-C"):
        return "call"
    return "put"


def normalize_strategy_name(raw: str | None, *, default: str = "naked_short") -> str:
    """Canonical strategy id used by persisted state, API payloads, and reports.

    ``naked_short_put`` and ``naked_short_call`` are kept as legacy aliases of
    ``naked_short`` so existing state files / payloads keep loading after the
    rename.
    """
    normalized = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return default
    aliases = {
        "naked": "naked_short",
        "naked_put": "naked_short",
        "naked_call": "naked_short",
        "short_put": "naked_short",
        "short_call": "naked_short",
        "shortput": "naked_short",
        "shortcall": "naked_short",
        "naked_short_put": "naked_short",
        "naked_short_call": "naked_short",
        "put_spread": "bull_put_spread",
        "short_put_spread": "bull_put_spread",
        "bullputspread": "bull_put_spread",
        "bull_put": "bull_put_spread",
        "coveredcall": "covered_call",
    }
    return aliases.get(normalized, normalized)


def _looks_like_covered_call_group(
    payload: dict[str, Any],
    *,
    option_type: str,
    covered_underlying_quantity: Decimal,
) -> bool:
    if option_type != "call":
        return False
    if covered_underlying_quantity > 0:
        return True
    label = str(payload.get("short_label") or "")
    return label.startswith("covered_call-")


class RiskRegime(str, Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    CRISIS = "crisis"


@dataclass(frozen=True)
class OptionInstrument:
    instrument_name: str
    base_currency: str
    quote_currency: str
    settlement_currency: str
    instrument_type: str
    tick_size: Decimal
    tick_size_steps: tuple[tuple[Decimal, Decimal], ...]
    min_trade_amount: Decimal
    contract_size: Decimal
    option_type: str
    expiration_timestamp_ms: int
    strike: Decimal
    instrument_state: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> OptionInstrument:
        parsed = parse_option_name(str(payload.get("instrument_name") or ""))
        base_currency = str(payload.get("base_currency") or (parsed or {}).get("base_currency") or "")
        quote_currency = str(payload.get("quote_currency") or (parsed or {}).get("quote_currency") or "")
        settlement_currency = str(payload.get("settlement_currency") or "")
        instrument_type = str(payload.get("instrument_type") or "").lower()
        if not settlement_currency:
            if quote_currency == "USDC":
                settlement_currency = "USDC"
            elif base_currency and quote_currency in {"", base_currency}:
                settlement_currency = base_currency
        if not instrument_type:
            if quote_currency == "USDC" and settlement_currency == "USDC":
                instrument_type = "linear"
            elif base_currency and settlement_currency == base_currency:
                instrument_type = "reversed"
        tick_size_steps = tuple(
            (
                to_decimal(item.get("above_price")),
                to_decimal(item.get("tick_size")),
            )
            for item in (payload.get("tick_size_steps") or [])
            if isinstance(item, dict)
        )
        return cls(
            instrument_name=str(payload.get("instrument_name") or ""),
            base_currency=base_currency,
            quote_currency=quote_currency,
            settlement_currency=settlement_currency,
            instrument_type=instrument_type,
            tick_size=to_decimal(payload.get("tick_size") or payload.get("min_price_increment")),
            tick_size_steps=tuple(sorted(tick_size_steps, key=lambda item: item[0])),
            min_trade_amount=to_decimal(payload.get("min_trade_amount") or payload.get("min_amount")),
            contract_size=to_decimal(payload.get("contract_size") or "1"),
            option_type=str(payload.get("option_type") or (parsed or {}).get("option_type") or "").lower(),
            expiration_timestamp_ms=int(payload.get("expiration_timestamp") or 0),
            strike=to_decimal(payload.get("strike")),
            instrument_state=str(payload.get("instrument_state") or ""),
        )

    def dte_days(self) -> Decimal:
        return dte_days(self.expiration_timestamp_ms)

    def tick_size_for_price(self, price: Decimal) -> Decimal:
        tick = self.tick_size
        for above_price, step in self.tick_size_steps:
            if price >= above_price and step > 0:
                tick = step
        return tick


@dataclass(frozen=True)
class OrderBookSnapshot:
    instrument_name: str
    best_bid_price: Decimal
    best_bid_amount: Decimal
    best_ask_price: Decimal
    best_ask_amount: Decimal
    mark_price: Decimal
    index_price: Decimal
    delta: Decimal
    iv: Decimal
    open_interest: Decimal

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> OrderBookSnapshot:
        greeks = payload.get("greeks") or {}
        return cls(
            instrument_name=str(payload.get("instrument_name") or ""),
            best_bid_price=to_decimal(payload.get("best_bid_price")),
            best_bid_amount=to_decimal(payload.get("best_bid_amount")),
            best_ask_price=to_decimal(payload.get("best_ask_price")),
            best_ask_amount=to_decimal(payload.get("best_ask_amount")),
            mark_price=to_decimal(payload.get("mark_price")),
            index_price=to_decimal(payload.get("index_price") or payload.get("underlying_price")),
            delta=to_decimal(greeks.get("delta")),
            iv=to_decimal(payload.get("mark_iv")),
            open_interest=to_decimal(payload.get("open_interest")),
        )

    @classmethod
    def from_book_summary(cls, payload: dict[str, Any]) -> OrderBookSnapshot:
        """Build a snapshot from ``get_book_summary_by_currency`` (no depth amounts or greeks).

        Only valid as a liquidity prefilter: ``best_*_amount`` and ``delta`` are
        unknown and set to zero, so it must not be used past the ``best_bid<=0``
        rejection gate.
        """
        return cls(
            instrument_name=str(payload.get("instrument_name") or ""),
            best_bid_price=to_decimal(payload.get("bid_price")),
            best_bid_amount=Decimal("0"),
            best_ask_price=to_decimal(payload.get("ask_price")),
            best_ask_amount=Decimal("0"),
            mark_price=to_decimal(payload.get("mark_price")),
            index_price=to_decimal(payload.get("index_price") or payload.get("underlying_price")),
            delta=Decimal("0"),
            iv=to_decimal(payload.get("mark_iv")),
            open_interest=to_decimal(payload.get("open_interest")),
        )

    @property
    def spread_ratio(self) -> Decimal:
        midpoint = (self.best_bid_price + self.best_ask_price) / Decimal("2")
        if midpoint <= 0 or self.best_ask_price < self.best_bid_price:
            return Decimal("1")
        return (self.best_ask_price - self.best_bid_price) / midpoint

    @property
    def effective_mark(self) -> Decimal:
        """Mark price for screening/margin; falls back to bid/ask midpoint when mark is missing."""
        if self.mark_price > 0:
            return self.mark_price
        return (self.best_bid_price + self.best_ask_price) / Decimal("2")

    def quote_sane_for_close(self, *, max_spread_ratio: Decimal) -> bool:
        """True when bid/ask are usable for management exits (not stale outliers)."""
        mark = self.mark_price
        bid = self.best_bid_price
        ask = self.best_ask_price
        if bid <= 0 or ask <= 0 or ask < bid:
            return False
        if self.spread_ratio > max_spread_ratio:
            return False
        if mark <= 0:
            return True
        upper = mark * (Decimal("1") + max_spread_ratio)
        lower = mark * (Decimal("1") - max_spread_ratio)
        return ask <= upper and bid >= lower

    def buy_close_premium(self, *, max_spread_ratio: Decimal) -> Decimal:
        """Per-contract premium for buy-to-close; falls back to mark when quotes are insane."""
        mark = self.mark_price if self.mark_price > 0 else Decimal("0")
        ask = self.best_ask_price
        if self.quote_sane_for_close(max_spread_ratio=max_spread_ratio):
            if ask <= 0:
                return mark
            return max(ask, mark) if mark > 0 else ask
        return mark if mark > 0 else max(ask, Decimal("0"))

    def sell_close_premium(self, *, max_spread_ratio: Decimal) -> Decimal:
        """Per-contract premium for sell-to-close; falls back to mark when quotes are insane."""
        mark = self.mark_price if self.mark_price > 0 else Decimal("0")
        bid = self.best_bid_price
        if self.quote_sane_for_close(max_spread_ratio=max_spread_ratio):
            if bid <= 0:
                return mark
            if mark > 0:
                return min(bid, mark)
            return bid
        return mark if mark > 0 else max(bid, Decimal("0"))

    @property
    def book_notional_usdc(self) -> Decimal:
        return self.index_price * self.best_bid_amount


@dataclass(frozen=True)
class AccountSummary:
    currency: str
    balance: Decimal
    equity: Decimal
    available_funds: Decimal
    available_withdrawal_funds: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    delta_total: Decimal
    options_delta: Decimal
    options_gamma: Decimal
    options_theta: Decimal
    total_equity_usd: Decimal
    total_initial_margin_usd: Decimal
    total_maintenance_margin_usd: Decimal

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> AccountSummary:
        return cls(
            currency=str(payload.get("currency") or "").upper(),
            balance=to_decimal(payload.get("balance")),
            equity=to_decimal(payload.get("equity")),
            available_funds=to_decimal(payload.get("available_funds")),
            available_withdrawal_funds=to_decimal(payload.get("available_withdrawal_funds")),
            initial_margin=to_decimal(payload.get("initial_margin")),
            maintenance_margin=to_decimal(payload.get("maintenance_margin")),
            delta_total=to_decimal(payload.get("delta_total")),
            options_delta=to_decimal(payload.get("options_delta")),
            options_gamma=to_decimal(payload.get("options_gamma")),
            options_theta=to_decimal(payload.get("options_theta")),
            total_equity_usd=to_decimal(payload.get("total_equity_usd")),
            total_initial_margin_usd=to_decimal(payload.get("total_initial_margin_usd")),
            total_maintenance_margin_usd=to_decimal(payload.get("total_maintenance_margin_usd")),
        )


# Transaction types reported by Deribit's ``private/get_transaction_log`` that
# represent **external** cash flow (user-initiated transfers in/out of the
# account or between sub-accounts). Used to correct day-start drawdown so that
# a withdrawal isn't miscounted as a trading loss.
EXTERNAL_FLOW_TRANSACTION_TYPES: frozenset[str] = frozenset(
    {
        "deposit",
        "withdrawal",
        "transfer",
    }
)

# Investor fee baseline: only true external in/out. Exclude ``transfer`` because
# cross-book moves (USDC → BTC margin) are not net new capital.
SUBSCRIPTION_FLOW_TRANSACTION_TYPES: frozenset[str] = frozenset(
    {
        "deposit",
        "withdrawal",
    }
)


@dataclass(frozen=True)
class TransactionEntry:
    """One row from Deribit's private/get_transaction_log endpoint.

    ``amount`` is signed (positive = inflow, negative = outflow) and expressed
    in the account's native currency (BTC for the BTC sub-account, USDC for
    the USDC sub-account, etc.). The engine converts to USDC separately.
    """

    id: int
    timestamp: int
    type: str
    currency: str
    amount: Decimal
    balance: Decimal | None
    info: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> TransactionEntry:
        amount_raw = payload.get("change")
        if amount_raw is None:
            amount_raw = payload.get("amount")
        return cls(
            id=int(payload.get("id") or 0),
            timestamp=int(payload.get("timestamp") or 0),
            type=str(payload.get("type") or "").lower(),
            currency=str(payload.get("currency") or "").upper(),
            amount=to_decimal(amount_raw),
            balance=to_decimal(payload.get("balance")) if payload.get("balance") is not None else None,
            info=str(payload.get("info") or ""),
        )

    @property
    def is_external_flow(self) -> bool:
        return self.type in EXTERNAL_FLOW_TRANSACTION_TYPES

    @property
    def is_subscription_flow(self) -> bool:
        return self.type in SUBSCRIPTION_FLOW_TRANSACTION_TYPES


@dataclass(frozen=True)
class OpenOrder:
    order_id: str
    instrument_name: str
    direction: str
    order_state: str
    order_type: str
    amount: Decimal
    filled_amount: Decimal
    price: Decimal
    average_price: Decimal
    post_only: bool
    reduce_only: bool
    label: str
    creation_timestamp_ms: int | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> OpenOrder:
        return cls(
            order_id=str(payload.get("order_id") or ""),
            instrument_name=str(payload.get("instrument_name") or ""),
            direction=str(payload.get("direction") or "").lower(),
            order_state=str(payload.get("order_state") or "").lower(),
            order_type=str(payload.get("order_type") or "").lower(),
            amount=to_decimal(payload.get("amount")),
            filled_amount=to_decimal(payload.get("filled_amount")),
            price=to_decimal(payload.get("price")),
            average_price=to_decimal(payload.get("average_price")),
            post_only=bool(payload.get("post_only", False)),
            reduce_only=bool(payload.get("reduce_only", False)),
            label=str(payload.get("label") or ""),
            creation_timestamp_ms=int(payload["creation_timestamp"])
            if payload.get("creation_timestamp") is not None
            else None,
        )


@dataclass(frozen=True)
class Position:
    instrument_name: str
    direction: str
    kind: str
    size: Decimal
    size_currency: Decimal
    mark_price: Decimal
    average_price: Decimal
    floating_profit_loss: Decimal
    delta: Decimal
    #: Underlying index (spot) in quote; Deribit ``get_positions.index_price``.
    index_price: Decimal = Decimal("0")
    #: True only when Deribit returned ``floating_profit_loss`` in get_positions.
    has_floating_profit_loss: bool = False
    #: Options only: Deribit ``get_positions.floating_profit_loss_usd`` (already USD).
    floating_profit_loss_usd: Decimal = Decimal("0")
    has_floating_profit_loss_usd: bool = False

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Position:
        raw_floating_profit_loss = payload.get("floating_profit_loss")
        raw_floating_profit_loss_usd = payload.get("floating_profit_loss_usd")
        return cls(
            instrument_name=str(payload.get("instrument_name") or ""),
            direction=str(payload.get("direction") or "").lower(),
            kind=str(payload.get("kind") or "").lower(),
            size=to_decimal(payload.get("size")),
            size_currency=to_decimal(payload.get("size_currency") or payload.get("size")),
            mark_price=to_decimal(payload.get("mark_price")),
            average_price=to_decimal(payload.get("average_price")),
            floating_profit_loss=to_decimal(raw_floating_profit_loss),
            delta=to_decimal(payload.get("delta")),
            index_price=to_decimal(payload.get("index_price")),
            has_floating_profit_loss=raw_floating_profit_loss not in (None, ""),
            floating_profit_loss_usd=to_decimal(raw_floating_profit_loss_usd),
            has_floating_profit_loss_usd=raw_floating_profit_loss_usd not in (None, ""),
        )

    @property
    def signed_size_currency(self) -> Decimal:
        sign = Decimal("-1") if self.direction == "sell" else Decimal("1")
        return self.size_currency * sign


@dataclass(frozen=True)
class SpreadLeg:
    instrument_name: str
    strike: Decimal
    quantity: Decimal
    min_trade_amount: Decimal
    contract_size: Decimal
    entry_price: Decimal
    target_price: Decimal
    best_bid_price: Decimal
    best_ask_price: Decimal
    delta: Decimal
    tick_size: Decimal
    tick_size_steps: tuple[tuple[Decimal, Decimal], ...]
    expiration_timestamp_ms: int
    index_price: Decimal
    quote_currency: str
    settlement_currency: str
    instrument_type: str


@dataclass(frozen=True)
class NakedPutCandidate:
    """Deribit naked short option candidate (single leg).

    Named ``NakedPutCandidate`` for backward compatibility with serialized
    state; the ``option_type`` field ("put" or "call") selects the option side
    so the same dataclass powers both short put and short call candidates.
    """

    currency: str
    collateral_currency: str
    quantity: Decimal
    dte_days: Decimal
    short_leg: SpreadLeg
    screening_bid: Decimal
    screening_mark: Decimal
    target_limit_price: Decimal
    net_premium_native: Decimal
    fee_native: Decimal
    net_apr: Decimal
    margin_efficiency: Decimal
    estimated_im_total: Decimal
    estimated_mm_total: Decimal
    regime: RiskRegime
    preferred_delta: bool
    preferred_otm: bool
    in_target_apr_band: bool
    option_type: str = "put"
    strategy: str = ""
    long_leg: SpreadLeg | None = None
    covered_underlying_quantity: Decimal = Decimal("0")
    # Mark IV (annualized decimal) of the short leg at screening time. Used by
    # skew-aware side selection to compare put vs call richness. Defaults to 0
    # for backward compatibility with serialized candidates.
    short_iv: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, Any]:
        strategy = normalize_strategy_name(self.strategy, default="naked_short")
        payload = {
            "strategy": strategy,
            "option_type": self.option_type,
            "currency": self.currency,
            "collateral_currency": self.collateral_currency,
            "quantity": self.quantity,
            "dte_days": self.dte_days,
            "short_instrument_name": self.short_leg.instrument_name,
            "short_strike": self.short_leg.strike,
            "screening_bid": self.screening_bid,
            "screening_mark": self.screening_mark,
            "target_limit_price": self.target_limit_price,
            "net_premium_native": self.net_premium_native,
            "fee_native": self.fee_native,
            "net_apr": self.net_apr,
            "net_credit": self.net_premium_native,
            "entry_fee": self.fee_native,
            "margin_efficiency": self.margin_efficiency,
            "estimated_im_total": self.estimated_im_total,
            "estimated_mm_total": self.estimated_mm_total,
            "max_loss": self.estimated_im_total,
            "otm_ratio": self._otm_ratio(),
            "preferred_delta": self.preferred_delta,
            "preferred_otm": self.preferred_otm,
            "in_target_apr_band": self.in_target_apr_band,
            "regime": self.regime.value,
            "covered_underlying_quantity": self.covered_underlying_quantity,
            "short_iv": self.short_iv,
        }
        if self.long_leg is not None:
            payload.update(
                {
                    "long_instrument_name": self.long_leg.instrument_name,
                    "long_strike": self.long_leg.strike,
                    "long_entry_price": self.long_leg.entry_price,
                    "long_target_price": self.long_leg.target_price,
                    "long_best_bid_price": self.long_leg.best_bid_price,
                    "long_best_ask_price": self.long_leg.best_ask_price,
                    "long_delta": self.long_leg.delta,
                }
            )
        return payload

    def _otm_ratio(self) -> Decimal:
        idx = self.short_leg.index_price
        if idx <= 0:
            return Decimal("0")
        if self.option_type == "call":
            return (self.short_leg.strike / idx) - Decimal("1")
        return Decimal("1") - (self.short_leg.strike / idx)


@dataclass
class TradeGroup:
    group_id: str
    currency: str
    collateral_currency: str
    quantity: Decimal
    entry_timestamp_ms: int
    expiration_timestamp_ms: int
    short_instrument_name: str
    short_strike: Decimal
    entry_credit: Decimal
    original_entry_credit: Decimal
    max_loss: Decimal
    regime_at_entry: str
    # Initial margin consumed by this group, expressed in the **collateral
    # currency's native unit** (BTC for BTC-settled inverse, ETH for ETH-
    # settled inverse, USDC for linear-USDC). This is what scanner capacity
    # math should subtract from ``summary_equity * expiry_im_cap`` — the
    # legacy ``max_loss`` is always USDC-scale and mixes units when the
    # collateral is coin-based.
    estimated_im_collateral: Decimal = Decimal("0")
    entry_fee: Decimal = Decimal("0")
    #: Entry fee in collateral book units (BTC/ETH); USDC rows stay 0.
    entry_fee_collateral: Decimal = Decimal("0")
    #: Short-leg average fill price at entry (option premium in coin for inverse).
    short_entry_average_price: Decimal = Decimal("0")
    #: Long-leg average fill price at entry (bull put spread).
    long_entry_average_price: Decimal = Decimal("0")
    #: Long-leg average fill price at close (bull put spread).
    long_close_average_price: Decimal | None = None
    #: Index (USD) at entry — used to recover entry premium for legacy rows.
    entry_index_usd: Decimal = Decimal("0")
    #: Net APR at entry (round-trip net premium / position collateral, annualized by entry DTE).
    entry_net_apr: Decimal = Decimal("0")
    #: Book equity (native unit) snapshotted at open — used for entry APR, not live equity.
    entry_book_equity: Decimal = Decimal("0")
    hedge_instrument_name: str = ""
    #: Signed perp base (coin) units this group currently wants hedged. Negative
    #: = short perp (offsetting a long-delta short put), positive = long perp
    #: (offsetting a short-delta short call). Under per-position hedging the
    #: engine reconciles the live perp to the sum of every group's value.
    hedge_size_base: Decimal = Decimal("0")
    #: Active per-position hedge mode: "" (none), "soft" or "hard".
    hedge_mode: str = ""
    #: Consecutive manage cycles the defense trigger has stayed clear while a
    #: per-position hedge is still active. Used to auto-unwind the hedge after
    #: ``recovery_normal_cycles`` clean cycles.
    hedge_recovery_streak: int = 0
    current_debit: Decimal = Decimal("0")
    #: Mark-based buy-to-close cost (fair value, ignoring spread). Used to
    #: evaluate the loss-based defense stop so an IV/spread spike does not
    #: spuriously trip the stop. Falls back to ``current_debit`` when unset.
    mark_debit: Decimal = Decimal("0")
    current_close_fee: Decimal = Decimal("0")
    #: Estimated close fee in collateral book units (open positions).
    current_close_fee_collateral: Decimal = Decimal("0")
    short_delta: Decimal = Decimal("0")
    profit_capture: Decimal = Decimal("0")
    status: str = "open"
    last_action: str = ""
    close_incomplete_streak: int = 0
    #: Consecutive manage cycles the hard / soft / covered-call-ITM defense
    #: condition has held. Used by the confirmation window so a single
    #: snapshot spike does not stop us out at a local extreme.
    hard_defense_streak: int = 0
    soft_defense_streak: int = 0
    itm_defense_streak: int = 0
    closed_timestamp_ms: int | None = None
    close_reason: str = ""
    realized_close_debit: Decimal | None = None
    realized_close_fee: Decimal | None = None
    #: Realized close fee in collateral book units.
    close_fee_collateral: Decimal | None = None
    #: Short-leg average fill price at close (buy-to-close for short options).
    short_close_average_price: Decimal | None = None
    #: Index (USD) at close.
    close_index_usd: Decimal | None = None
    #: Realized PnL in collateral coin (ETH/BTC), from fill prices minus fees.
    realized_pnl_collateral_native: Decimal | None = None
    realized_pnl: Decimal | None = None
    realized_return_on_max_loss: Decimal | None = None
    realized_annualized_return: Decimal | None = None
    #: Book equity (native unit) snapshotted at close.
    close_book_equity: Decimal | None = None
    #: ``realized_pnl / position collateral notional``, annualized by holding days (frozen at close).
    realized_apr_on_equity: Decimal | None = None
    short_label: str = ""
    hedge_label: str = ""
    option_type: str = "put"
    strategy: str = ""
    long_instrument_name: str = ""
    long_strike: Decimal = Decimal("0")
    long_label: str = ""
    covered_underlying_quantity: Decimal = Decimal("0")
    spot_exit_status: str = ""
    spot_exit_amount: Decimal = Decimal("0")
    spot_exit_instrument_name: str = ""
    spot_exit_order_id: str = ""
    spot_exit_reason: str = ""
    profit_sweep_status: str = ""
    profit_sweep_amount: Decimal = Decimal("0")
    profit_sweep_instrument_name: str = ""
    profit_sweep_order_id: str = ""
    profit_sweep_quote_proceeds: Decimal = Decimal("0")
    profit_sweep_quote_proceeds_lifetime: Decimal = Decimal("0")
    profit_sweep_exchange_native: Decimal = Decimal("0")
    profit_sweep_exchange_quote_proceeds: Decimal = Decimal("0")
    profit_sweep_reason: str = ""

    @property
    def dte_days(self) -> Decimal:
        return dte_days(self.expiration_timestamp_ms)

    @property
    def loss_amount(self) -> Decimal:
        return max(self.current_debit - self.entry_credit, Decimal("0"))

    @property
    def loss_pct_of_max_loss(self) -> Decimal:
        return safe_div(self.loss_amount, self.max_loss)

    @property
    def mark_loss_amount(self) -> Decimal:
        """Unrealized loss using mark (fair value) close cost, with ask fallback."""
        debit = self.mark_debit if self.mark_debit > 0 else self.current_debit
        return max(debit - self.entry_credit, Decimal("0"))

    @property
    def mark_loss_pct_of_max_loss(self) -> Decimal:
        return safe_div(self.mark_loss_amount, self.max_loss)

    @property
    def holding_days(self) -> Decimal:
        if self.closed_timestamp_ms is None or self.entry_timestamp_ms <= 0:
            return Decimal("0")
        elapsed_ms = max(self.closed_timestamp_ms - self.entry_timestamp_ms, 0)
        return Decimal(str(elapsed_ms)) / Decimal("86400000")

    def collateral_book(self) -> str:
        return (self.collateral_currency or self.currency or "USDC").upper()

    def is_coin_collateral(self) -> bool:
        return self.collateral_book() in _COIN_COLLATERAL_BOOKS

    @staticmethod
    def _plausible_underlying_index(value: Decimal | None) -> Decimal | None:
        if value is None or value <= _MIN_PLAUSIBLE_UNDERLYING_INDEX_USD:
            return None
        return value

    def is_covered_call_group(self) -> bool:
        """True when this group is a short covered call (a call leg backed by spot).

        Single source of truth shared by the live engine and trade-journal
        backfill: a covered call is always a short *call*, identified by the
        ``covered_call`` strategy, a positive covered underlying quantity, or a
        ``covered_call-`` short label.
        """
        if (self.option_type or "").lower() != "call":
            return False
        return (
            (self.strategy or "") == "covered_call"
            or self.covered_underlying_quantity > 0
            or str(self.short_label or "").startswith("covered_call-")
        )

    def underlying_index_usd_for_apr(self) -> Decimal:
        """Underlying BTC/ETH spot for USDC linear call APR denominators."""
        if self.collateral_book() != "USDC":
            resolved = self._resolved_index_usd(self.entry_index_usd, self.close_index_usd)
            return resolved if resolved is not None else Decimal("0")
        if (self.option_type or "put").lower() != "call":
            return Decimal("0")
        for candidate in (self.entry_index_usd, self.close_index_usd):
            plausible = self._plausible_underlying_index(candidate)
            if plausible is not None:
                return plausible
        if self.short_strike > _MIN_PLAUSIBLE_UNDERLYING_INDEX_USD:
            return self.short_strike
        return Decimal("0")

    def is_phantom_reconcile_close(self, *, open_short_names: set[str]) -> bool:
        return is_phantom_reconcile_close(self, open_short_names=open_short_names)

    @staticmethod
    def _resolved_index_usd(*candidates: Decimal | None) -> Decimal | None:
        for candidate in candidates:
            if candidate is not None and candidate > 0:
                return candidate
        return None

    def _premium_native_from_price(
        self,
        price: Decimal | None,
        *,
        index_usd: Decimal | None,
        gross_usdc: Decimal | None = None,
    ) -> Decimal | None:
        if self.quantity <= 0:
            return None
        if price is not None and price > 0:
            return premium_value_native(premium=price, quantity=self.quantity)
        if gross_usdc is not None and index_usd is not None and index_usd > 0:
            return gross_usdc / index_usd
        return None

    def _premium_price_plausible(self, price: Decimal) -> bool:
        """Reject journal rows that stored USDC/qty instead of coin premium."""
        if price <= 0:
            return False
        book = self.collateral_book()
        if book == "ETH":
            return price < Decimal("3")
        if book == "BTC":
            return price < Decimal("0.25")
        return True

    @staticmethod
    def _journal_row_priority(row: dict[str, Any]) -> int:
        extra = row.get("extra")
        if not isinstance(extra, dict):
            extra = {}
        source_action = str(row.get("source_action") or "")
        if extra.get("source") == "deribit_api" or source_action in {"backfill_api", "backfill_api_instrument"}:
            return 0
        if extra.get("synthetic") or str(row.get("source_action") or "") == "backfill_state":
            return 10
        return 5

    def infer_indices_from_fill_prices(self) -> None:
        """Derive USD indices from coin fill prices + USDC ledger (legacy state)."""
        qty = self.quantity
        if qty <= 0:
            return
        # USDC linear premiums are already in USDC; gross/(premium*qty) ≈ 1 and must not
        # overwrite a valid underlying BTC/ETH index stored in ``entry_index_usd``.
        if not self.is_coin_collateral():
            return
        if self.short_entry_average_price > 0 and self.entry_index_usd <= 0:
            gross = self.entry_credit + (self.entry_fee or Decimal("0"))
            denom = self.short_entry_average_price * qty
            if denom > 0 and gross > 0:
                self.entry_index_usd = gross / denom
        if (
            self.short_close_average_price is not None
            and self.short_close_average_price > 0
            and (self.close_index_usd is None or self.close_index_usd <= 0)
            and self.realized_close_debit is not None
            and self.is_coin_collateral()
        ):
            close_fee = self.realized_close_fee or Decimal("0")
            premium_usdc = max(self.realized_close_debit - close_fee, Decimal("0"))
            denom = self.short_close_average_price * qty
            if denom > 0 and premium_usdc > 0:
                self.close_index_usd = premium_usdc / denom

    def enrich_fill_prices_from_journal(self, executions: list[dict[str, Any]]) -> None:
        """Backfill missing fill prices / indices from ``trade_executions`` rows."""
        if not executions or not self.short_instrument_name:
            return
        target = self.short_instrument_name
        best_open: tuple[int, Decimal] | None = None
        best_close: tuple[int, Decimal] | None = None
        for row in executions:
            if str(row.get("instrument_name") or "") != target:
                continue
            leg = str(row.get("leg") or "short")
            if leg not in {"", "short"}:
                continue
            price = to_decimal(row.get("price"))
            if not self._premium_price_plausible(price):
                continue
            event = str(row.get("event_type") or "").lower()
            rank = self._journal_row_priority(row)
            if event == "open":
                if best_open is None or rank < best_open[0]:
                    best_open = (rank, price)
            elif event == "close":
                if best_close is None or rank < best_close[0]:
                    best_close = (rank, price)
        if best_open is not None and self.short_entry_average_price <= 0:
            self.short_entry_average_price = best_open[1]
        if best_close is not None and (self.short_close_average_price is None or self.short_close_average_price <= 0):
            self.short_close_average_price = best_close[1]
        self.infer_indices_from_fill_prices()

    def _ledger_entry_amount_from_close_index(self) -> Decimal | None:
        """When only the close fill is known, infer open premium from USDC ledger."""
        if self.realized_close_debit is None:
            return None
        self.infer_indices_from_fill_prices()
        idx = self._resolved_index_usd(self.close_index_usd, self.entry_index_usd)
        if idx is None:
            return None
        gross = self.entry_credit + (self.entry_fee or Decimal("0"))
        if gross <= 0:
            return None
        return gross / idx

    def enrich_fill_prices_from_ledger_spot(self, spot_index_usd: Decimal) -> None:
        """Last resort: derive coin premiums from USDC ledger when journal lacks fills."""
        if not self.is_coin_collateral() or spot_index_usd <= 0 or self.quantity <= 0:
            return
        qty = self.quantity
        if self.short_entry_average_price <= 0:
            gross = self.entry_credit + (self.entry_fee or Decimal("0"))
            if gross > 0:
                px = gross / (qty * spot_index_usd)
                if self._premium_price_plausible(px):
                    self.short_entry_average_price = px
        if (
            self.short_close_average_price is None or self.short_close_average_price <= 0
        ) and self.realized_close_debit is not None:
            close_fee = self.realized_close_fee or Decimal("0")
            premium_usdc = max(self.realized_close_debit - close_fee, Decimal("0"))
            if premium_usdc > 0:
                px = premium_usdc / (qty * spot_index_usd)
                if self._premium_price_plausible(px):
                    self.short_close_average_price = px
        self.infer_indices_from_fill_prices()

    def _ledger_exit_amount_from_entry_index(self) -> Decimal | None:
        """When only the open fill is known, infer close premium from USDC ledger."""
        if self.realized_close_debit is None:
            return None
        self.infer_indices_from_fill_prices()
        idx = self._resolved_index_usd(self.entry_index_usd, self.close_index_usd)
        if idx is None:
            return None
        close_fee = self.realized_close_fee or Decimal("0")
        gross = max(self.realized_close_debit - close_fee, Decimal("0"))
        if gross <= 0:
            return None
        return gross / idx

    def entry_amount_native(self, *, index_fallback_usd: Decimal | None = None) -> Decimal | None:
        """Gross option premium received at open (collateral coin)."""
        idx_entry = self._resolved_index_usd(self.entry_index_usd)
        gross_usdc = self.entry_credit + (self.entry_fee or Decimal("0"))
        short = self._premium_native_from_price(
            self.short_entry_average_price if self.short_entry_average_price > 0 else None,
            index_usd=idx_entry,
            gross_usdc=gross_usdc if idx_entry is not None else None,
        )
        if short is None:
            short = self._ledger_entry_amount_from_close_index()
        if short is None:
            return None
        if not self.long_instrument_name:
            return short
        long_px = self.long_entry_average_price if self.long_entry_average_price > 0 else None
        long = self._premium_native_from_price(long_px, index_usd=idx_entry)
        if long is None:
            return short
        return short - long

    def exit_amount_native(self, *, index_fallback_usd: Decimal | None = None) -> Decimal | None:
        """Gross option premium paid at close (collateral coin)."""
        close_debit = self.realized_close_debit
        close_fee = self.realized_close_fee or Decimal("0")
        gross_usdc: Decimal | None = None
        if close_debit is not None:
            gross_usdc = max(close_debit - close_fee, Decimal("0"))
        idx_close = self._resolved_index_usd(self.close_index_usd, self.entry_index_usd)
        short_px = self.short_close_average_price
        short = self._premium_native_from_price(
            short_px if short_px is not None and short_px > 0 else None,
            index_usd=idx_close,
            gross_usdc=gross_usdc,
        )
        if short is None:
            short = self._ledger_exit_amount_from_entry_index()
        if short is None:
            return None
        if not self.long_instrument_name:
            return short
        long_px = self.long_close_average_price
        long = self._premium_native_from_price(
            long_px if long_px is not None and long_px > 0 else None,
            index_usd=idx_close,
        )
        if long is None:
            return short
        return short - long

    def resolved_entry_fee_collateral(self) -> Decimal | None:
        """Entry fee in collateral coin; prefers stored native fee over USDC round-trip."""
        if not self.is_coin_collateral():
            return None
        if self.entry_fee_collateral > 0:
            return self.entry_fee_collateral
        qty = self.quantity
        if qty <= 0:
            return None
        from .fees import inverse_option_fee_native_per_contract

        fee_rate = Decimal("0.0003")
        fee_cap_rate = Decimal("0.125")
        total = Decimal("0")
        if self.short_entry_average_price > 0:
            total += (
                inverse_option_fee_native_per_contract(
                    premium=self.short_entry_average_price,
                    fee_rate=fee_rate,
                    fee_cap_rate=fee_cap_rate,
                )
                * qty
            )
        if self.long_instrument_name and self.long_entry_average_price > 0:
            total += (
                inverse_option_fee_native_per_contract(
                    premium=self.long_entry_average_price,
                    fee_rate=fee_rate,
                    fee_cap_rate=fee_cap_rate,
                )
                * qty
            )
        return total if total > 0 else None

    def resolved_close_fee_collateral(self) -> Decimal | None:
        """Close fee in collateral coin; prefers stored native fee over USDC round-trip."""
        if not self.is_coin_collateral():
            return None
        if self.close_fee_collateral is not None and self.close_fee_collateral > 0:
            return self.close_fee_collateral
        if self.status == "open" and self.current_close_fee_collateral > 0:
            return self.current_close_fee_collateral
        qty = self.quantity
        if qty <= 0:
            return None
        from .fees import inverse_option_fee_native_total

        fee_rate = Decimal("0.0003")
        fee_cap_rate = Decimal("0.125")
        total = Decimal("0")
        short_px = self.resolved_short_close_price()
        if short_px > 0:
            total += inverse_option_fee_native_total(
                premium=short_px,
                quantity=qty,
                fee_rate=fee_rate,
                fee_cap_rate=fee_cap_rate,
            )
        long_px = self.long_close_average_price
        if long_px is not None and long_px > 0 and self.long_instrument_name:
            total += inverse_option_fee_native_total(
                premium=long_px,
                quantity=qty,
                fee_rate=fee_rate,
                fee_cap_rate=fee_cap_rate,
            )
        return total if total > 0 else None

    def _coin_gross_entry_native(self) -> Decimal | None:
        if self.short_entry_average_price <= 0 or self.quantity <= 0:
            return None
        gross = self.short_entry_average_price * self.quantity
        if self.long_instrument_name and self.long_entry_average_price > 0:
            gross -= self.long_entry_average_price * self.quantity
        return gross

    def _coin_gross_exit_native(self) -> Decimal | None:
        short_px = self.resolved_short_close_price()
        if short_px <= 0 or self.quantity <= 0:
            return None
        gross = short_px * self.quantity
        long_px = self.long_close_average_price
        if long_px is not None and long_px > 0 and self.long_instrument_name:
            gross -= long_px * self.quantity
        return gross

    def apply_coin_close_from_native(
        self,
        *,
        short_close_premium: Decimal,
        index_usd: Decimal,
        close_fee_collateral: Decimal | None = None,
    ) -> bool:
        """Set close fill + USDC ledger from coin-native premium (native → USDC once).

        Does not infer coin premium from ``realized_close_debit``; callers must supply
        the per-contract premium in collateral coin.
        """
        if not self.is_coin_collateral() or short_close_premium <= 0 or self.quantity <= 0:
            return False
        from .fees import inverse_option_fee_native_total

        fee_rate = Decimal("0.0003")
        fee_cap_rate = Decimal("0.125")
        if close_fee_collateral is None:
            close_fee_collateral = inverse_option_fee_native_total(
                premium=short_close_premium,
                quantity=self.quantity,
                fee_rate=fee_rate,
                fee_cap_rate=fee_cap_rate,
            )
        if close_fee_collateral is None or close_fee_collateral < 0:
            return False
        if index_usd <= 0:
            idx = self._resolved_index_usd(self.close_index_usd, self.entry_index_usd)
            if idx is None or idx <= 0:
                return False
            index_usd = idx
        gross_native = short_close_premium * self.quantity
        if self.long_instrument_name:
            long_px = self.long_close_average_price
            if long_px is not None and long_px > 0:
                gross_native -= long_px * self.quantity
        new_debit = (gross_native + close_fee_collateral) * index_usd
        new_fee = close_fee_collateral * index_usd
        changed = False
        if self.short_close_average_price != short_close_premium:
            self.short_close_average_price = short_close_premium
            changed = True
        if self.close_index_usd != index_usd:
            self.close_index_usd = index_usd
            changed = True
        if self.close_fee_collateral != close_fee_collateral:
            self.close_fee_collateral = close_fee_collateral
            changed = True
        if self.realized_close_fee != new_fee:
            self.realized_close_fee = new_fee
            changed = True
        if self.realized_close_debit != new_debit:
            self.realized_close_debit = new_debit
            changed = True
        if self.current_close_fee != new_fee:
            self.current_close_fee = new_fee
            changed = True
        if self.current_close_fee_collateral != close_fee_collateral:
            self.current_close_fee_collateral = close_fee_collateral
            changed = True
        if self.current_debit != new_debit:
            self.current_debit = new_debit
            changed = True
        return changed

    def backfill_coin_collateral_ledger(self) -> bool:
        """Recompute coin-native fees and USDC ledger from fill prices (legacy round-trip fix)."""
        if not self.is_coin_collateral():
            return False
        qty = self.quantity
        if qty <= 0:
            return False
        changed = False
        idx_entry = self.entry_index_usd

        gross_entry = self._coin_gross_entry_native()
        entry_fee_native = self.resolved_entry_fee_collateral()
        if gross_entry is not None and entry_fee_native is not None and entry_fee_native > 0:
            if self.entry_fee_collateral != entry_fee_native:
                self.entry_fee_collateral = entry_fee_native
                changed = True
            if idx_entry > 0:
                net_native = gross_entry - entry_fee_native
                new_credit = net_native * idx_entry
                new_fee = entry_fee_native * idx_entry
                if self.entry_credit != new_credit:
                    self.entry_credit = new_credit
                    changed = True
                if self.original_entry_credit != new_credit:
                    self.original_entry_credit = new_credit
                    changed = True
                if self.entry_fee != new_fee:
                    self.entry_fee = new_fee
                    changed = True

        idx_mark = self._resolved_index_usd(self.close_index_usd, idx_entry)
        short_px = self.resolved_short_close_price()
        if short_px > 0 and idx_mark is not None and idx_mark > 0:
            if self.apply_coin_close_from_native(
                short_close_premium=short_px,
                index_usd=idx_mark,
                close_fee_collateral=self.resolved_close_fee_collateral(),
            ):
                changed = True
        return changed

    def fees_native(self, *, index_fallback_usd: Decimal | None = None) -> Decimal | None:
        """Entry + close fees in collateral coin."""
        idx_close = self._resolved_index_usd(self.close_index_usd, index_fallback_usd)
        idx_entry = self._resolved_index_usd(self.entry_index_usd, idx_close, index_fallback_usd)
        total = Decimal("0")
        entry_fee_collateral = self.resolved_entry_fee_collateral()
        if entry_fee_collateral is not None:
            total += entry_fee_collateral
        else:
            entry_fee = self.entry_fee or Decimal("0")
            if entry_fee > 0:
                if idx_entry is None:
                    return None
                total += entry_fee / idx_entry
        close_fee_collateral = self.resolved_close_fee_collateral()
        if close_fee_collateral is not None:
            total += close_fee_collateral
        else:
            close_fee = self.realized_close_fee or Decimal("0")
            if close_fee > 0:
                if idx_close is None:
                    return None
                total += close_fee / idx_close
        return total

    def compute_realized_pnl_native(self, *, index_fallback_usd: Decimal | None = None) -> Decimal | None:
        """Coin collateral: ``entry_amount - exit_amount - fee``."""
        if not self.is_coin_collateral():
            return None
        entry_amount = self.entry_amount_native(index_fallback_usd=index_fallback_usd)
        exit_amount = self.exit_amount_native(index_fallback_usd=index_fallback_usd)
        fees = self.fees_native(index_fallback_usd=index_fallback_usd)
        if entry_amount is None or exit_amount is None or fees is None:
            return None
        return entry_amount - exit_amount - fees

    def resolved_short_entry_price(self) -> Decimal:
        if self.short_entry_average_price > 0:
            return self.short_entry_average_price
        if self.long_instrument_name:
            return Decimal("0")
        idx = self.entry_index_usd
        if idx <= 0 and self.close_index_usd is not None and self.close_index_usd > 0:
            idx = self.close_index_usd
        if idx <= 0 or self.quantity <= 0:
            return Decimal("0")
        gross_usdc = self.entry_credit + self.entry_fee
        return safe_div(gross_usdc, self.quantity * idx)

    def resolved_short_close_price(self) -> Decimal:
        if self.short_close_average_price is not None and self.short_close_average_price > 0:
            return self.short_close_average_price
        if self.is_coin_collateral():
            return Decimal("0")
        idx = self.close_index_usd or self.entry_index_usd
        if idx is None or idx <= 0 or self.quantity <= 0 or self.realized_close_debit is None:
            return Decimal("0")
        fee = self.realized_close_fee or Decimal("0")
        premium_usdc = max(self.realized_close_debit - fee, Decimal("0"))
        return safe_div(premium_usdc, self.quantity * idx)

    def economic_close_debit_usdc(self) -> Decimal | None:
        """USDC close cost including fees.

        ``realized_close_debit`` normally already includes ``realized_close_fee``.
        Legacy rows stored premium-only in ``realized_close_debit`` while keeping
        the fee in a separate column; those rows match ``realized_pnl`` only when
        the fee is added back here.
        """
        if self.realized_close_debit is None:
            return None
        close_debit = self.realized_close_debit
        close_fee = self.realized_close_fee or Decimal("0")
        if close_fee <= 0:
            return close_debit
        if self.realized_pnl is not None:
            entry_net = self.entry_credit
            if abs(self.realized_pnl - (entry_net - close_debit)) <= Decimal("0.000001"):
                return close_debit + close_fee
        return close_debit

    def entry_credit_net_usdc(self) -> Decimal:
        """Entry credit net of entry fees (handles legacy gross ``entry_credit`` rows)."""
        fee = self.entry_fee or Decimal("0")
        if fee <= 0:
            return self.entry_credit
        idx = self.entry_index_usd
        if self.short_entry_average_price > 0 and idx > 0 and self.quantity > 0:
            gross = self.short_entry_average_price * self.quantity * idx
            tol = max(Decimal("0.01"), abs(gross) * Decimal("0.001"))
            if abs(gross - self.entry_credit) <= tol:
                return self.entry_credit - fee
            if abs(gross - (self.entry_credit + fee)) <= tol:
                return self.entry_credit
        return self.entry_credit

    def entry_net_credit_collateral(self) -> Decimal | None:
        """Actual entry credit net of entry fee, in collateral book units."""
        if self.collateral_book() == "USDC":
            net_usdc = self.entry_credit_net_usdc()
            return net_usdc if net_usdc > 0 else None
        gross = self.entry_amount_native()
        fee = self.resolved_entry_fee_collateral()
        if gross is not None and fee is not None:
            net = gross - fee
            return net if net > 0 else None
        net_usdc = self.entry_credit_net_usdc()
        if net_usdc <= 0:
            return None
        idx = self._resolved_index_usd(self.entry_index_usd, self.close_index_usd)
        if idx is None or idx <= 0:
            return None
        return net_usdc / idx

    def entry_net_apr_at_open(self, *, contract_size: Decimal = Decimal("1")) -> Decimal:
        """Entry APR from actual open ledger (net credit / open size, annualized)."""
        from .trade_apr import entry_net_apr_from_actual_open

        net = self.entry_net_credit_collateral()
        if net is None or net <= 0:
            return Decimal("0")
        cs = contract_size if contract_size > 0 else Decimal("1")
        qty = self.quantity if self.quantity > 0 else Decimal("1")
        strategy = self.strategy or "naked_short"
        if strategy != "covered_call" and self.covered_underlying_quantity > 0 and self.option_type == "call":
            strategy = "covered_call"
        if strategy == "covered_call" and self.covered_underlying_quantity <= 0:
            self.covered_underlying_quantity = qty
        book = self.collateral_book()
        if book == "USDC":
            idx = self.underlying_index_usd_for_apr()
        else:
            idx = self.entry_index_usd
            if idx <= 0:
                idx = self.close_index_usd or Decimal("0")
        if book == "USDC" and (self.option_type or "").lower() == "call" and idx <= 0:
            return Decimal("0")
        return entry_net_apr_from_actual_open(
            strategy=strategy,
            collateral_currency=book,
            option_type=self.option_type or "put",
            quantity=qty,
            contract_size=cs,
            strike=self.short_strike,
            index_price_usd=idx,
            estimated_im_collateral=self.estimated_im_collateral,
            covered_underlying_quantity=self.covered_underlying_quantity,
            net_credit_collateral=net,
            entry_timestamp_ms=self.entry_timestamp_ms,
            expiration_timestamp_ms=self.expiration_timestamp_ms,
        )

    def backfill_realized_pnl_usdc(self, *, spot_index_usd: Decimal | None = None) -> None:
        """Recompute fee-inclusive realized PnL in USDC equivalent."""
        if self.status != "closed":
            return
        if self.is_coin_collateral():
            if self.realized_pnl_collateral_native is None:
                self.backfill_realized_pnl_collateral_native(spot_index_usd=spot_index_usd)
            native = self.realized_pnl_collateral_native
            spot = spot_index_usd if spot_index_usd is not None and spot_index_usd > 0 else None
            if native is not None and spot is not None:
                self.realized_pnl = native * spot
            return
        close_total = self.economic_close_debit_usdc()
        if close_total is None:
            return
        self.realized_pnl = self.entry_credit_net_usdc() - close_total

    def compute_coin_profit_native(self, *, allow_ledger_spot_infer: bool = False) -> Decimal | None:
        """Fee-aware coin profit from option premiums (never USDC ÷ index)."""
        if not self.is_coin_collateral():
            return None
        self.backfill_coin_collateral_ledger()
        native = self.compute_realized_pnl_native()
        if native is not None:
            return native
        if not allow_ledger_spot_infer:
            return None
        idx = self._resolved_index_usd(self.close_index_usd, self.entry_index_usd)
        if idx is None or idx <= 0:
            return None
        self.enrich_fill_prices_from_ledger_spot(idx)
        return self.compute_realized_pnl_native()

    def sync_coin_profit_native(self, *, spot_index_usd: Decimal | None = None) -> Decimal | None:
        """Persist ``realized_pnl_collateral_native`` from premiums; derive USDC from native × index."""
        if self.status != "closed" or not self.is_coin_collateral():
            return None
        spot = spot_index_usd if spot_index_usd is not None and spot_index_usd > 0 else None
        native = self.compute_coin_profit_native(allow_ledger_spot_infer=spot is not None)
        if native is None:
            return None
        self.realized_pnl_collateral_native = native
        self.backfill_realized_pnl_usdc(spot_index_usd=spot)
        return native

    def backfill_realized_pnl_collateral_native(
        self,
        *,
        spot_index_usd: Decimal | None = None,
        journal_executions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Coin PnL: ``entry_amount - exit_amount - fee`` (collateral native)."""
        if self.status != "closed" or not self.is_coin_collateral():
            return
        spot = spot_index_usd if spot_index_usd is not None and spot_index_usd > 0 else None
        if journal_executions:
            self.enrich_fill_prices_from_journal(journal_executions)
        self.infer_indices_from_fill_prices()
        native = self.compute_coin_profit_native(
            allow_ledger_spot_infer=spot is not None
            and (
                self.short_entry_average_price <= 0
                or self.short_close_average_price is None
                or self.short_close_average_price <= 0
            )
        )
        if native is None:
            self.realized_pnl_collateral_native = None
            return
        self.realized_pnl_collateral_native = native
        self.backfill_realized_pnl_usdc(spot_index_usd=spot)

    def to_dict(self) -> dict[str, Any]:
        strategy = normalize_strategy_name(self.strategy, default="naked_short")
        payload = {
            "group_id": self.group_id,
            "currency": self.currency,
            "collateral_currency": self.collateral_currency,
            "quantity": self.quantity,
            "entry_timestamp_ms": self.entry_timestamp_ms,
            "expiration_timestamp_ms": self.expiration_timestamp_ms,
            "dte_days": self.dte_days,
            "short_instrument_name": self.short_instrument_name,
            "short_strike": self.short_strike,
            "entry_credit": self.entry_credit,
            "original_entry_credit": self.original_entry_credit,
            "entry_fee": self.entry_fee,
            "entry_fee_collateral": self.entry_fee_collateral,
            "short_entry_average_price": self.short_entry_average_price,
            "long_entry_average_price": self.long_entry_average_price,
            "long_close_average_price": self.long_close_average_price,
            "entry_index_usd": self.entry_index_usd,
            "entry_net_apr": self.entry_net_apr,
            "entry_book_equity": self.entry_book_equity,
            "max_loss": self.max_loss,
            "estimated_im_collateral": self.estimated_im_collateral,
            "regime_at_entry": self.regime_at_entry,
            "hedge_instrument_name": self.hedge_instrument_name,
            "hedge_size_base": self.hedge_size_base,
            "hedge_mode": self.hedge_mode,
            "hedge_recovery_streak": self.hedge_recovery_streak,
            "current_debit": self.current_debit,
            "mark_debit": self.mark_debit,
            "current_close_fee": self.current_close_fee,
            "current_close_fee_collateral": self.current_close_fee_collateral,
            "short_delta": self.short_delta,
            "profit_capture": self.profit_capture,
            "status": self.status,
            "last_action": self.last_action,
            "close_incomplete_streak": self.close_incomplete_streak,
            "hard_defense_streak": self.hard_defense_streak,
            "soft_defense_streak": self.soft_defense_streak,
            "itm_defense_streak": self.itm_defense_streak,
            "closed_timestamp_ms": self.closed_timestamp_ms,
            "close_reason": self.close_reason,
            "realized_close_debit": self.realized_close_debit,
            "realized_close_fee": self.realized_close_fee,
            "close_fee_collateral": self.close_fee_collateral,
            "short_close_average_price": self.short_close_average_price,
            "close_index_usd": self.close_index_usd,
            "realized_pnl_collateral_native": self.realized_pnl_collateral_native,
            "realized_pnl": self.realized_pnl,
            "realized_return_on_max_loss": self.realized_return_on_max_loss,
            "realized_annualized_return": self.realized_annualized_return,
            "close_book_equity": self.close_book_equity,
            "realized_apr_on_equity": self.realized_apr_on_equity,
            "short_label": self.short_label,
            "hedge_label": self.hedge_label,
            "option_type": self.option_type,
            "strategy": strategy,
        }
        if self.long_instrument_name:
            payload["long_instrument_name"] = self.long_instrument_name
            payload["long_strike"] = self.long_strike
            payload["long_label"] = self.long_label
        if self.covered_underlying_quantity > 0:
            payload["covered_underlying_quantity"] = self.covered_underlying_quantity
        if self.spot_exit_status:
            payload["spot_exit_status"] = self.spot_exit_status
        if self.spot_exit_amount > 0:
            payload["spot_exit_amount"] = self.spot_exit_amount
        if self.spot_exit_instrument_name:
            payload["spot_exit_instrument_name"] = self.spot_exit_instrument_name
        if self.spot_exit_order_id:
            payload["spot_exit_order_id"] = self.spot_exit_order_id
        if self.spot_exit_reason:
            payload["spot_exit_reason"] = self.spot_exit_reason
        if self.profit_sweep_status:
            payload["profit_sweep_status"] = self.profit_sweep_status
        if self.profit_sweep_amount > 0:
            payload["profit_sweep_amount"] = self.profit_sweep_amount
        if self.profit_sweep_instrument_name:
            payload["profit_sweep_instrument_name"] = self.profit_sweep_instrument_name
        if self.profit_sweep_order_id:
            payload["profit_sweep_order_id"] = self.profit_sweep_order_id
        if self.profit_sweep_quote_proceeds > 0:
            payload["profit_sweep_quote_proceeds"] = self.profit_sweep_quote_proceeds
        if self.profit_sweep_quote_proceeds_lifetime > 0:
            payload["profit_sweep_quote_proceeds_lifetime"] = self.profit_sweep_quote_proceeds_lifetime
        if self.profit_sweep_exchange_native > 0:
            payload["profit_sweep_exchange_native"] = self.profit_sweep_exchange_native
        if self.profit_sweep_exchange_quote_proceeds > 0:
            payload["profit_sweep_exchange_quote_proceeds"] = self.profit_sweep_exchange_quote_proceeds
        if self.profit_sweep_reason:
            payload["profit_sweep_reason"] = self.profit_sweep_reason
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TradeGroup:
        option_type = str(
            payload.get("option_type") or _infer_option_type(str(payload.get("short_instrument_name") or ""))
        )
        quantity = to_decimal(payload.get("quantity"))
        covered_underlying_quantity = to_decimal(payload.get("covered_underlying_quantity"))
        raw_strategy = normalize_strategy_name(str(payload.get("strategy") or ""), default="")
        if (not raw_strategy or raw_strategy == "naked_short") and _looks_like_covered_call_group(
            payload,
            option_type=option_type,
            covered_underlying_quantity=covered_underlying_quantity,
        ):
            strategy = "covered_call"
            if covered_underlying_quantity <= 0:
                covered_underlying_quantity = quantity
        elif raw_strategy == "covered_call":
            strategy = raw_strategy
            if option_type == "call" and covered_underlying_quantity <= 0:
                covered_underlying_quantity = quantity
        else:
            strategy = raw_strategy or "naked_short"
        return cls(
            group_id=str(payload.get("group_id") or ""),
            currency=str(payload.get("currency") or "").upper(),
            collateral_currency=(
                str(payload.get("collateral_currency") or "").upper()
                or (
                    "USDC"
                    if "_USDC-" in str(payload.get("short_instrument_name") or "")
                    else str(payload.get("currency") or "").upper()
                )
            ),
            quantity=quantity,
            entry_timestamp_ms=int(payload.get("entry_timestamp_ms") or 0),
            expiration_timestamp_ms=int(payload.get("expiration_timestamp_ms") or 0),
            short_instrument_name=str(payload.get("short_instrument_name") or ""),
            short_strike=to_decimal(payload.get("short_strike")),
            entry_credit=to_decimal(payload.get("entry_credit")),
            original_entry_credit=to_decimal(payload.get("original_entry_credit") or payload.get("entry_credit")),
            entry_fee=to_decimal(payload.get("entry_fee")),
            entry_fee_collateral=to_decimal(payload.get("entry_fee_collateral")),
            short_entry_average_price=to_decimal(payload.get("short_entry_average_price")),
            long_entry_average_price=to_decimal(payload.get("long_entry_average_price")),
            long_close_average_price=to_decimal(payload.get("long_close_average_price"))
            if payload.get("long_close_average_price") is not None
            else None,
            entry_index_usd=to_decimal(payload.get("entry_index_usd")),
            entry_net_apr=to_decimal(payload.get("entry_net_apr")),
            entry_book_equity=to_decimal(payload.get("entry_book_equity")),
            max_loss=to_decimal(payload.get("max_loss")),
            estimated_im_collateral=to_decimal(payload.get("estimated_im_collateral")),
            regime_at_entry=str(payload.get("regime_at_entry") or RiskRegime.NORMAL.value),
            hedge_instrument_name=str(payload.get("hedge_instrument_name") or ""),
            hedge_size_base=to_decimal(payload.get("hedge_size_base")),
            hedge_mode=str(payload.get("hedge_mode") or ""),
            hedge_recovery_streak=int(payload.get("hedge_recovery_streak") or 0),
            current_debit=to_decimal(payload.get("current_debit")),
            mark_debit=to_decimal(payload.get("mark_debit")),
            current_close_fee=to_decimal(payload.get("current_close_fee")),
            current_close_fee_collateral=to_decimal(payload.get("current_close_fee_collateral")),
            short_delta=to_decimal(payload.get("short_delta")),
            profit_capture=to_decimal(payload.get("profit_capture")),
            status=str(payload.get("status") or "open"),
            last_action=str(payload.get("last_action") or ""),
            close_incomplete_streak=int(payload.get("close_incomplete_streak") or 0),
            hard_defense_streak=int(payload.get("hard_defense_streak") or 0),
            soft_defense_streak=int(payload.get("soft_defense_streak") or 0),
            itm_defense_streak=int(payload.get("itm_defense_streak") or 0),
            closed_timestamp_ms=int(payload["closed_timestamp_ms"])
            if payload.get("closed_timestamp_ms") is not None
            else None,
            close_reason=str(payload.get("close_reason") or ""),
            realized_close_debit=to_decimal(payload.get("realized_close_debit"))
            if payload.get("realized_close_debit") is not None
            else None,
            realized_close_fee=to_decimal(payload.get("realized_close_fee"))
            if payload.get("realized_close_fee") is not None
            else None,
            close_fee_collateral=to_decimal(payload.get("close_fee_collateral"))
            if payload.get("close_fee_collateral") is not None
            else None,
            short_close_average_price=to_decimal(payload.get("short_close_average_price"))
            if payload.get("short_close_average_price") is not None
            else None,
            close_index_usd=to_decimal(payload.get("close_index_usd"))
            if payload.get("close_index_usd") is not None
            else None,
            realized_pnl_collateral_native=to_decimal(payload.get("realized_pnl_collateral_native"))
            if payload.get("realized_pnl_collateral_native") is not None
            else None,
            realized_pnl=to_decimal(payload.get("realized_pnl")) if payload.get("realized_pnl") is not None else None,
            realized_return_on_max_loss=to_decimal(payload.get("realized_return_on_max_loss"))
            if payload.get("realized_return_on_max_loss") is not None
            else None,
            realized_annualized_return=to_decimal(payload.get("realized_annualized_return"))
            if payload.get("realized_annualized_return") is not None
            else None,
            close_book_equity=to_decimal(payload.get("close_book_equity"))
            if payload.get("close_book_equity") is not None
            else None,
            realized_apr_on_equity=to_decimal(payload.get("realized_apr_on_equity"))
            if payload.get("realized_apr_on_equity") is not None
            else None,
            short_label=str(payload.get("short_label") or ""),
            hedge_label=str(payload.get("hedge_label") or ""),
            option_type=option_type,
            strategy=strategy,
            long_instrument_name=str(payload.get("long_instrument_name") or ""),
            long_strike=to_decimal(payload.get("long_strike")),
            long_label=str(payload.get("long_label") or ""),
            covered_underlying_quantity=covered_underlying_quantity,
            spot_exit_status=str(payload.get("spot_exit_status") or ""),
            spot_exit_amount=to_decimal(payload.get("spot_exit_amount")),
            spot_exit_instrument_name=str(payload.get("spot_exit_instrument_name") or ""),
            spot_exit_order_id=str(payload.get("spot_exit_order_id") or ""),
            spot_exit_reason=str(payload.get("spot_exit_reason") or ""),
            profit_sweep_status=str(payload.get("profit_sweep_status") or ""),
            profit_sweep_amount=to_decimal(payload.get("profit_sweep_amount")),
            profit_sweep_instrument_name=str(payload.get("profit_sweep_instrument_name") or ""),
            profit_sweep_order_id=str(payload.get("profit_sweep_order_id") or ""),
            profit_sweep_quote_proceeds=to_decimal(payload.get("profit_sweep_quote_proceeds")),
            profit_sweep_quote_proceeds_lifetime=to_decimal(payload.get("profit_sweep_quote_proceeds_lifetime")),
            profit_sweep_exchange_native=to_decimal(payload.get("profit_sweep_exchange_native")),
            profit_sweep_exchange_quote_proceeds=to_decimal(payload.get("profit_sweep_exchange_quote_proceeds")),
            profit_sweep_reason=str(payload.get("profit_sweep_reason") or ""),
        )


@dataclass(frozen=True)
class HedgePlan:
    currency: str
    mode: str
    instrument_name: str
    side: str
    delta_change_base: Decimal
    order_amount: Decimal
    target_delta_cap_base: Decimal
    current_delta_base: Decimal
    current_hedge_base: Decimal
    target_hedge_base: Decimal
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "mode": self.mode,
            "instrument_name": self.instrument_name,
            "side": self.side,
            "delta_change_base": self.delta_change_base,
            "order_amount": self.order_amount,
            "target_delta_cap_base": self.target_delta_cap_base,
            "current_delta_base": self.current_delta_base,
            "current_hedge_base": self.current_hedge_base,
            "target_hedge_base": self.target_hedge_base,
            "note": self.note,
        }


@dataclass(frozen=True)
class PortfolioSnapshot:
    total_equity_usdc: Decimal
    day_start_equity_usdc: Decimal
    # Net external cash flow since UTC day-start (deposit/withdraw/transfer),
    # expressed in USDC. Positive = net deposit; negative = net withdrawal.
    #
    # Daily PnL excluding deposits/withdrawals is:
    #   (equity_now - equity_day_start - day_net_flow_usdc)
    day_net_flow_usdc: Decimal
    day_pnl_usdc_ex_flow: Decimal
    day_drawdown_pct: Decimal
    open_max_loss: Decimal
    open_max_loss_pct: Decimal
    initial_margin_ratio: Decimal
    maintenance_margin_ratio: Decimal
    projected_max_profit_run_rate_usdc: Decimal
    projected_max_profit_apr: Decimal
    target_progress_ratio: Decimal
    regime: RiskRegime
    halt_new_entries: bool
    hard_derisk: bool
    cooldown_until_ms: int | None
    cooling_down: bool
    delta_totals_by_currency: dict[str, Decimal] = field(default_factory=dict)
    regime_by_currency: dict[str, RiskRegime] = field(default_factory=dict)
    halt_entry_reasons: tuple[str, ...] = field(default_factory=tuple)
    regime_detail_by_currency: dict[str, tuple[str, ...]] = field(default_factory=dict)
    margin_ratios_by_currency: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)
    # --- Per-book (BTC / ETH / USDC) segregated views ---------------------
    # ``by_book`` dicts are keyed by collateral currency matching ``Book.collateral``.
    # They let the engine gate entries per book so a withdrawal or drawdown in
    # one collateral pool doesn't silently halt the others.
    equity_by_book: dict[str, Decimal] = field(default_factory=dict)
    day_start_equity_by_book: dict[str, Decimal] = field(default_factory=dict)
    day_net_flow_usdc_by_book: dict[str, Decimal] = field(default_factory=dict)
    day_pnl_usdc_ex_flow_by_book: dict[str, Decimal] = field(default_factory=dict)
    day_pnl_usdc_ex_flow_ex_spot: Decimal = Decimal("0")
    day_pnl_usdc_ex_flow_ex_spot_by_book: dict[str, Decimal] = field(default_factory=dict)
    day_drawdown_pct_by_book: dict[str, Decimal] = field(default_factory=dict)
    cooldown_until_ms_by_book: dict[str, int | None] = field(default_factory=dict)
    cooling_down_by_book: dict[str, bool] = field(default_factory=dict)
    hard_derisk_by_book: dict[str, bool] = field(default_factory=dict)
    halt_entries_by_book: dict[str, bool] = field(default_factory=dict)
    halt_entry_reasons_by_book: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Per underlying (BTC / ETH): False means that currency may open new groups.
    halt_new_entries_by_currency: dict[str, bool] = field(default_factory=dict)
    # Legacy portfolio-wide cooldown, open-max-loss cap, or hard-stop on a group.
    portfolio_wide_entry_halt: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_equity_usdc": self.total_equity_usdc,
            "day_start_equity_usdc": self.day_start_equity_usdc,
            "day_net_flow_usdc": self.day_net_flow_usdc,
            "day_pnl_usdc_ex_flow": self.day_pnl_usdc_ex_flow,
            "day_drawdown_pct": self.day_drawdown_pct,
            "open_max_loss": self.open_max_loss,
            "open_max_loss_pct": self.open_max_loss_pct,
            "initial_margin_ratio": self.initial_margin_ratio,
            "maintenance_margin_ratio": self.maintenance_margin_ratio,
            "projected_max_profit_run_rate_usdc": self.projected_max_profit_run_rate_usdc,
            "projected_max_profit_apr": self.projected_max_profit_apr,
            "target_progress_ratio": self.target_progress_ratio,
            "regime": self.regime.value,
            "halt_new_entries": self.halt_new_entries,
            "halt_new_entries_by_currency": dict(self.halt_new_entries_by_currency),
            "portfolio_wide_entry_halt": self.portfolio_wide_entry_halt,
            "hard_derisk": self.hard_derisk,
            "cooldown_until_ms": self.cooldown_until_ms,
            "cooling_down": self.cooling_down,
            "delta_totals_by_currency": self.delta_totals_by_currency,
            "regime_by_currency": {key: value.value for key, value in self.regime_by_currency.items()},
            "halt_entry_reasons": list(self.halt_entry_reasons),
            "regime_detail_by_currency": {key: list(value) for key, value in self.regime_detail_by_currency.items()},
            "margin_ratios_by_currency": {
                key: {"im_ratio": im, "mm_ratio": mm} for key, (im, mm) in self.margin_ratios_by_currency.items()
            },
            "equity_by_book": dict(self.equity_by_book),
            "day_start_equity_by_book": dict(self.day_start_equity_by_book),
            "day_net_flow_usdc_by_book": dict(self.day_net_flow_usdc_by_book),
            "day_pnl_usdc_ex_flow_by_book": dict(self.day_pnl_usdc_ex_flow_by_book),
            "day_pnl_usdc_ex_flow_ex_spot": self.day_pnl_usdc_ex_flow_ex_spot,
            "day_pnl_usdc_ex_flow_ex_spot_by_book": dict(self.day_pnl_usdc_ex_flow_ex_spot_by_book),
            "day_drawdown_pct_by_book": dict(self.day_drawdown_pct_by_book),
            "cooldown_until_ms_by_book": dict(self.cooldown_until_ms_by_book),
            "cooling_down_by_book": dict(self.cooling_down_by_book),
            "hard_derisk_by_book": dict(self.hard_derisk_by_book),
            "halt_entries_by_book": dict(self.halt_entries_by_book),
            "halt_entry_reasons_by_book": {key: list(value) for key, value in self.halt_entry_reasons_by_book.items()},
        }


@dataclass
class StrategyState:
    version: int = 2
    day_key: str = ""
    # Aggregate equity snapshot (kept for reports and backward-compat with v1 state files).
    day_start_equity_usdc: Decimal = Decimal("0")
    last_equity_usdc: Decimal = Decimal("0")
    # Aggregate cooldown (kept so legacy "panic" style triggers still work). New
    # per-book fields below are preferred for drawdown / derisk routing.
    cooldown_until_ms: int | None = None
    # --- Per-book (BTC / ETH / USDC) segregated tracking ------------------
    # Keyed by collateral currency. These let the engine reason about each
    # margin account independently so e.g. an ETH-book withdrawal only moves
    # the ETH book's day_start, not the portfolio aggregate.
    day_start_equity_by_book: dict[str, Decimal] = field(default_factory=dict)
    last_equity_by_book: dict[str, Decimal] = field(default_factory=dict)
    # Same keys, but expressed in each book's collateral unit (BTC / ETH /
    # USDC). Drawdown uses this view so coin price moves do not look like
    # trading losses in inverse-native books.
    day_start_equity_native_by_book: dict[str, Decimal] = field(default_factory=dict)
    last_equity_native_by_book: dict[str, Decimal] = field(default_factory=dict)
    cooldown_until_ms_by_book: dict[str, int | None] = field(default_factory=dict)
    # Net external cash flow per book since ``day_start``, expressed in USDC.
    # Positive = net deposit; negative = net withdrawal. Kept for reporting and
    # compatibility with older state files.
    day_net_flow_usdc_by_book: dict[str, Decimal] = field(default_factory=dict)
    # Native-unit twin of ``day_net_flow_usdc_by_book`` for drawdown on
    # inverse-native books.
    day_net_flow_native_by_book: dict[str, Decimal] = field(default_factory=dict)
    # Last Deribit transaction_log query timestamp (ms) per book, to throttle
    # repeated API calls within the cash-flow refresh interval.
    last_flow_query_ms_by_book: dict[str, int] = field(default_factory=dict)
    # UTC ms when each book's day-start equity was last established. Cash-flow
    # queries use max(UTC midnight, anchor) so deposits already reflected in
    # day-start are not counted again in day_net_flow.
    day_equity_anchor_ms_by_book: dict[str, int] = field(default_factory=dict)
    next_group_id: int = 1
    normal_recovery_counts: dict[str, int] = field(default_factory=dict)
    groups: list[TradeGroup] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "day_key": self.day_key,
            "day_start_equity_usdc": self.day_start_equity_usdc,
            "last_equity_usdc": self.last_equity_usdc,
            "cooldown_until_ms": self.cooldown_until_ms,
            "day_start_equity_by_book": dict(self.day_start_equity_by_book),
            "last_equity_by_book": dict(self.last_equity_by_book),
            "day_start_equity_native_by_book": dict(self.day_start_equity_native_by_book),
            "last_equity_native_by_book": dict(self.last_equity_native_by_book),
            "cooldown_until_ms_by_book": dict(self.cooldown_until_ms_by_book),
            "day_net_flow_usdc_by_book": dict(self.day_net_flow_usdc_by_book),
            "day_net_flow_native_by_book": dict(self.day_net_flow_native_by_book),
            "last_flow_query_ms_by_book": dict(self.last_flow_query_ms_by_book),
            "day_equity_anchor_ms_by_book": dict(self.day_equity_anchor_ms_by_book),
            "next_group_id": self.next_group_id,
            "normal_recovery_counts": self.normal_recovery_counts,
            "groups": [group.to_dict() for group in self.groups],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StrategyState:
        return cls(
            version=int(payload.get("version") or 1),
            day_key=str(payload.get("day_key") or ""),
            day_start_equity_usdc=to_decimal(payload.get("day_start_equity_usdc")),
            last_equity_usdc=to_decimal(payload.get("last_equity_usdc")),
            cooldown_until_ms=int(payload["cooldown_until_ms"])
            if payload.get("cooldown_until_ms") is not None
            else None,
            day_start_equity_by_book={
                str(k).upper(): to_decimal(v) for k, v in (payload.get("day_start_equity_by_book") or {}).items()
            },
            last_equity_by_book={
                str(k).upper(): to_decimal(v) for k, v in (payload.get("last_equity_by_book") or {}).items()
            },
            day_start_equity_native_by_book={
                str(k).upper(): to_decimal(v) for k, v in (payload.get("day_start_equity_native_by_book") or {}).items()
            },
            last_equity_native_by_book={
                str(k).upper(): to_decimal(v) for k, v in (payload.get("last_equity_native_by_book") or {}).items()
            },
            cooldown_until_ms_by_book={
                str(k).upper(): (int(v) if v is not None else None)
                for k, v in (payload.get("cooldown_until_ms_by_book") or {}).items()
            },
            day_net_flow_usdc_by_book={
                str(k).upper(): to_decimal(v) for k, v in (payload.get("day_net_flow_usdc_by_book") or {}).items()
            },
            day_net_flow_native_by_book={
                str(k).upper(): to_decimal(v) for k, v in (payload.get("day_net_flow_native_by_book") or {}).items()
            },
            last_flow_query_ms_by_book={
                str(k).upper(): int(v)
                for k, v in (payload.get("last_flow_query_ms_by_book") or {}).items()
                if v is not None
            },
            day_equity_anchor_ms_by_book={
                str(k).upper(): int(v)
                for k, v in (payload.get("day_equity_anchor_ms_by_book") or {}).items()
                if v is not None
            },
            next_group_id=int(payload.get("next_group_id") or 1),
            normal_recovery_counts={
                str(k).upper(): int(v) for k, v in (payload.get("normal_recovery_counts") or {}).items()
            },
            groups=[TradeGroup.from_dict(item) for item in payload.get("groups") or []],
        )
