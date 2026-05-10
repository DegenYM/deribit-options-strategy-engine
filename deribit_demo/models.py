from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from .utils import dte_days, parse_option_name, safe_div, to_decimal


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
    def from_api(cls, payload: dict[str, Any]) -> "OptionInstrument":
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
    def from_api(cls, payload: dict[str, Any]) -> "OrderBookSnapshot":
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

    @property
    def spread_ratio(self) -> Decimal:
        midpoint = (self.best_bid_price + self.best_ask_price) / Decimal("2")
        if midpoint <= 0 or self.best_ask_price < self.best_bid_price:
            return Decimal("1")
        return (self.best_ask_price - self.best_bid_price) / midpoint

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
    def from_api(cls, payload: dict[str, Any]) -> "AccountSummary":
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
    def from_api(cls, payload: dict[str, Any]) -> "TransactionEntry":
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
    def from_api(cls, payload: dict[str, Any]) -> "OpenOrder":
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
            creation_timestamp_ms=int(payload["creation_timestamp"]) if payload.get("creation_timestamp") is not None else None,
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
    def from_api(cls, payload: dict[str, Any]) -> "Position":
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
    hedge_instrument_name: str = ""
    hedge_size_base: Decimal = Decimal("0")
    current_debit: Decimal = Decimal("0")
    current_close_fee: Decimal = Decimal("0")
    short_delta: Decimal = Decimal("0")
    profit_capture: Decimal = Decimal("0")
    status: str = "open"
    last_action: str = ""
    closed_timestamp_ms: int | None = None
    close_reason: str = ""
    realized_close_debit: Decimal | None = None
    realized_close_fee: Decimal | None = None
    realized_pnl: Decimal | None = None
    realized_return_on_max_loss: Decimal | None = None
    realized_annualized_return: Decimal | None = None
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
    def holding_days(self) -> Decimal:
        if self.closed_timestamp_ms is None or self.entry_timestamp_ms <= 0:
            return Decimal("0")
        elapsed_ms = max(self.closed_timestamp_ms - self.entry_timestamp_ms, 0)
        return Decimal(str(elapsed_ms)) / Decimal("86400000")

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
            "max_loss": self.max_loss,
            "estimated_im_collateral": self.estimated_im_collateral,
            "regime_at_entry": self.regime_at_entry,
            "hedge_instrument_name": self.hedge_instrument_name,
            "hedge_size_base": self.hedge_size_base,
            "current_debit": self.current_debit,
            "current_close_fee": self.current_close_fee,
            "short_delta": self.short_delta,
            "profit_capture": self.profit_capture,
            "status": self.status,
            "last_action": self.last_action,
            "closed_timestamp_ms": self.closed_timestamp_ms,
            "close_reason": self.close_reason,
            "realized_close_debit": self.realized_close_debit,
            "realized_close_fee": self.realized_close_fee,
            "realized_pnl": self.realized_pnl,
            "realized_return_on_max_loss": self.realized_return_on_max_loss,
            "realized_annualized_return": self.realized_annualized_return,
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
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TradeGroup":
        option_type = str(payload.get("option_type") or _infer_option_type(
            str(payload.get("short_instrument_name") or "")
        ))
        quantity = to_decimal(payload.get("quantity"))
        covered_underlying_quantity = to_decimal(payload.get("covered_underlying_quantity"))
        raw_strategy = normalize_strategy_name(str(payload.get("strategy") or ""), default="")
        if (
            (not raw_strategy or raw_strategy == "naked_short")
            and _looks_like_covered_call_group(
                payload,
                option_type=option_type,
                covered_underlying_quantity=covered_underlying_quantity,
            )
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
                or ("USDC" if "_USDC-" in str(payload.get("short_instrument_name") or "") else str(payload.get("currency") or "").upper())
            ),
            quantity=quantity,
            entry_timestamp_ms=int(payload.get("entry_timestamp_ms") or 0),
            expiration_timestamp_ms=int(payload.get("expiration_timestamp_ms") or 0),
            short_instrument_name=str(payload.get("short_instrument_name") or ""),
            short_strike=to_decimal(payload.get("short_strike")),
            entry_credit=to_decimal(payload.get("entry_credit")),
            original_entry_credit=to_decimal(payload.get("original_entry_credit") or payload.get("entry_credit")),
            entry_fee=to_decimal(payload.get("entry_fee")),
            max_loss=to_decimal(payload.get("max_loss")),
            estimated_im_collateral=to_decimal(payload.get("estimated_im_collateral")),
            regime_at_entry=str(payload.get("regime_at_entry") or RiskRegime.NORMAL.value),
            hedge_instrument_name=str(payload.get("hedge_instrument_name") or ""),
            hedge_size_base=to_decimal(payload.get("hedge_size_base")),
            current_debit=to_decimal(payload.get("current_debit")),
            current_close_fee=to_decimal(payload.get("current_close_fee")),
            short_delta=to_decimal(payload.get("short_delta")),
            profit_capture=to_decimal(payload.get("profit_capture")),
            status=str(payload.get("status") or "open"),
            last_action=str(payload.get("last_action") or ""),
            closed_timestamp_ms=int(payload["closed_timestamp_ms"]) if payload.get("closed_timestamp_ms") is not None else None,
            close_reason=str(payload.get("close_reason") or ""),
            realized_close_debit=to_decimal(payload.get("realized_close_debit")) if payload.get("realized_close_debit") is not None else None,
            realized_close_fee=to_decimal(payload.get("realized_close_fee")) if payload.get("realized_close_fee") is not None else None,
            realized_pnl=to_decimal(payload.get("realized_pnl")) if payload.get("realized_pnl") is not None else None,
            realized_return_on_max_loss=to_decimal(payload.get("realized_return_on_max_loss"))
            if payload.get("realized_return_on_max_loss") is not None
            else None,
            realized_annualized_return=to_decimal(payload.get("realized_annualized_return"))
            if payload.get("realized_annualized_return") is not None
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
            "hard_derisk": self.hard_derisk,
            "cooldown_until_ms": self.cooldown_until_ms,
            "cooling_down": self.cooling_down,
            "delta_totals_by_currency": self.delta_totals_by_currency,
            "regime_by_currency": {key: value.value for key, value in self.regime_by_currency.items()},
            "halt_entry_reasons": list(self.halt_entry_reasons),
            "regime_detail_by_currency": {key: list(value) for key, value in self.regime_detail_by_currency.items()},
            "margin_ratios_by_currency": {
                key: {"im_ratio": im, "mm_ratio": mm}
                for key, (im, mm) in self.margin_ratios_by_currency.items()
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
            "halt_entry_reasons_by_book": {
                key: list(value) for key, value in self.halt_entry_reasons_by_book.items()
            },
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
            "next_group_id": self.next_group_id,
            "normal_recovery_counts": self.normal_recovery_counts,
            "groups": [group.to_dict() for group in self.groups],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StrategyState":
        return cls(
            version=int(payload.get("version") or 1),
            day_key=str(payload.get("day_key") or ""),
            day_start_equity_usdc=to_decimal(payload.get("day_start_equity_usdc")),
            last_equity_usdc=to_decimal(payload.get("last_equity_usdc")),
            cooldown_until_ms=int(payload["cooldown_until_ms"]) if payload.get("cooldown_until_ms") is not None else None,
            day_start_equity_by_book={
                str(k).upper(): to_decimal(v)
                for k, v in (payload.get("day_start_equity_by_book") or {}).items()
            },
            last_equity_by_book={
                str(k).upper(): to_decimal(v)
                for k, v in (payload.get("last_equity_by_book") or {}).items()
            },
            day_start_equity_native_by_book={
                str(k).upper(): to_decimal(v)
                for k, v in (payload.get("day_start_equity_native_by_book") or {}).items()
            },
            last_equity_native_by_book={
                str(k).upper(): to_decimal(v)
                for k, v in (payload.get("last_equity_native_by_book") or {}).items()
            },
            cooldown_until_ms_by_book={
                str(k).upper(): (int(v) if v is not None else None)
                for k, v in (payload.get("cooldown_until_ms_by_book") or {}).items()
            },
            day_net_flow_usdc_by_book={
                str(k).upper(): to_decimal(v)
                for k, v in (payload.get("day_net_flow_usdc_by_book") or {}).items()
            },
            day_net_flow_native_by_book={
                str(k).upper(): to_decimal(v)
                for k, v in (payload.get("day_net_flow_native_by_book") or {}).items()
            },
            last_flow_query_ms_by_book={
                str(k).upper(): int(v)
                for k, v in (payload.get("last_flow_query_ms_by_book") or {}).items()
                if v is not None
            },
            next_group_id=int(payload.get("next_group_id") or 1),
            normal_recovery_counts={str(k).upper(): int(v) for k, v in (payload.get("normal_recovery_counts") or {}).items()},
            groups=[TradeGroup.from_dict(item) for item in payload.get("groups") or []],
        )
