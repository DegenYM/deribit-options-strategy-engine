"""Spot trade and internal transfer helpers for fee collection workflows."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .client import DeribitClient
from .config import BotConfig, has_private_creds_for_env, load_config
from .env_layout import (
    fee_account_env_path,
    find_repo_root,
    load_investor_manifest,
    main_account_env_path,
    resolve_investor_env_path,
)
from .exceptions import ConfigurationError, ExchangeError
from .models import AccountSummary, OptionInstrument, OrderBookSnapshot
from .utils import align_option_order_amount, floor_to_step, format_decimal, to_decimal

DEFAULT_FEE_SUBACCOUNT_NAME = "fee_acc"
SPOT_BASE_CURRENCIES = frozenset({"BTC", "ETH"})
SPOT_QUOTE_CURRENCIES = frozenset({"USDC", "USDT", "USDE"})
TRANSFER_CURRENCIES = frozenset({"BTC", "ETH", "USDC", "USDT", "USDE"})


@dataclass(frozen=True)
class FeeSubaccountConfig:
    subaccount_id: int | None
    subaccount_name: str


def load_fee_subaccount_config(investor_dir: Path) -> FeeSubaccountConfig:
    env_path = resolve_investor_env_path(investor_dir)
    values: dict[str, str | None] = dict(dotenv_values(env_path)) if env_path is not None else {}
    raw_id = values.get("FEE_SUBACCOUNT_ID")
    subaccount_id = None
    if raw_id is not None and str(raw_id).strip():
        subaccount_id = int(str(raw_id).strip())
    elif subaccount_id is None:
        fee_env = fee_account_env_path(investor_dir)
        if fee_env.is_file():
            fee_values = dotenv_values(fee_env)
            fee_raw_id = fee_values.get("FEE_SUBACCOUNT_ID")
            if fee_raw_id is not None and str(fee_raw_id).strip():
                subaccount_id = int(str(fee_raw_id).strip())
    raw_name = values.get("FEE_SUBACCOUNT_NAME")
    if raw_name is None or not str(raw_name).strip():
        fee_env = fee_account_env_path(investor_dir)
        if fee_env.is_file():
            raw_name = dotenv_values(fee_env).get("FEE_SUBACCOUNT_NAME")
    subaccount_name = (
        str(raw_name).strip() if raw_name is not None and str(raw_name).strip() else DEFAULT_FEE_SUBACCOUNT_NAME
    )
    return FeeSubaccountConfig(subaccount_id=subaccount_id, subaccount_name=subaccount_name)


def _subaccount_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("type") or "").lower() == "subaccount"]


def _resolve_fee_subaccount_id_via_fee_env(
    investor_dir: Path,
    *,
    fee_config: FeeSubaccountConfig,
) -> tuple[int, str] | None:
    """Resolve destination id using the fee sub-account's own API key.

    Strategy sub-account keys often cannot see other sub-accounts in
    ``private/get_subaccounts``; the fee wallet key can read its own id.
    """
    fee_env = fee_account_env_path(investor_dir)
    if not fee_env.is_file() or not has_private_creds_for_env(fee_env):
        return None
    fee_cfg = load_config(fee_env, require_private=True)
    if not fee_cfg.is_fee_collection_account:
        return None
    try:
        rows = DeribitClient(fee_cfg).get_subaccounts(with_portfolio=False)
    except ExchangeError:
        return None

    matches = [row for row in _subaccount_rows(rows) if _match_subaccount_row(row, name=fee_config.subaccount_name)]
    if len(matches) == 1:
        row = matches[0]
        return int(row["id"]), str(row.get("username") or row.get("system_name") or fee_config.subaccount_name)

    subs = _subaccount_rows(rows)
    if len(subs) == 1:
        row = subs[0]
        return int(row["id"]), str(row.get("username") or row.get("system_name") or fee_config.subaccount_name)
    return None


def spot_instrument_name(base_currency: str, quote_currency: str) -> str:
    base = base_currency.upper()
    quote = quote_currency.upper()
    if base not in SPOT_BASE_CURRENCIES:
        raise ConfigurationError(f"Unsupported spot base currency {base!r}; expected one of: BTC, ETH")
    if quote not in SPOT_QUOTE_CURRENCIES:
        raise ConfigurationError(f"Unsupported spot quote currency {quote!r}; expected USDC, USDT, or USDE")
    return f"{base}_{quote}"


def resolve_spot_trade_side(from_currency: str, to_currency: str) -> tuple[str, str, str]:
    """Return ``(direction, base_currency, quote_currency)`` for a spot pair."""
    source = from_currency.upper()
    target = to_currency.upper()
    if source in SPOT_BASE_CURRENCIES and target in SPOT_QUOTE_CURRENCIES:
        return "sell", source, target
    if source in SPOT_QUOTE_CURRENCIES and target in SPOT_BASE_CURRENCIES:
        return "buy", target, source
    raise ConfigurationError(
        "trade-spot requires a BTC/ETH ↔ USDC/USDT pair, e.g. "
        "--from-currency BTC --to USDC (sell) or --from-currency USDC --to BTC (buy)"
    )


def _lookup_spot_instrument(client: DeribitClient, instrument_name: str, base_currency: str) -> OptionInstrument:
    for lookup_currency in ("USDT", "USDC", base_currency.upper()):
        try:
            rows = client.get_instruments(lookup_currency, kind="spot", expired=False)
        except ExchangeError:
            continue
        for row in rows:
            instrument = OptionInstrument.from_api(row)
            if instrument.instrument_name == instrument_name:
                return instrument
    raise ConfigurationError(f"Spot instrument not found on Deribit: {instrument_name}")


def _summary_for_currency(client: DeribitClient, currency: str) -> AccountSummary | None:
    for item in client.get_account_summaries(extended=True):
        summary = AccountSummary.from_api(item)
        if summary.currency == currency.upper():
            return summary
    return None


def _resolve_sell_base_amount(
    *,
    amount_raw: str | None,
    available: Decimal,
    contract_size: Decimal,
    min_trade_amount: Decimal,
    use_all: bool,
) -> Decimal:
    if amount_raw is None or not str(amount_raw).strip():
        if not use_all:
            raise ConfigurationError("--amount is required (or use --all to trade full available balance)")
        amount_raw = "all"
    token = str(amount_raw).strip().lower()
    target = available if token == "all" else to_decimal(amount_raw)
    if target <= 0:
        raise ConfigurationError("Amount must be positive")
    aligned = align_option_order_amount(target, contract_size, min_trade_amount)
    if aligned <= 0:
        raise ConfigurationError(
            f"Amount {format_decimal(target, 8)} below minimum trade size "
            f"(min={format_decimal(min_trade_amount, 8)}, step={format_decimal(contract_size or min_trade_amount, 8)})"
        )
    if aligned > available:
        raise ConfigurationError(
            f"Requested amount {format_decimal(aligned, 8)} exceeds available {format_decimal(available, 8)}"
        )
    return aligned


def _resolve_buy_base_amount(
    *,
    quote_spend_raw: str | None,
    available_quote: Decimal,
    trade_price: Decimal,
    contract_size: Decimal,
    min_trade_amount: Decimal,
    use_all: bool,
) -> tuple[Decimal, Decimal]:
    if quote_spend_raw is None or not str(quote_spend_raw).strip():
        if not use_all:
            raise ConfigurationError("--amount is required (or use --all to trade full available balance)")
        quote_spend_raw = "all"
    token = str(quote_spend_raw).strip().lower()
    quote_spend = available_quote if token == "all" else to_decimal(quote_spend_raw)
    if quote_spend <= 0:
        raise ConfigurationError("Amount must be positive")
    if quote_spend > available_quote:
        raise ConfigurationError(
            f"Requested spend {format_decimal(quote_spend, 8)} exceeds available {format_decimal(available_quote, 8)}"
        )
    if trade_price <= 0:
        raise ConfigurationError("Cannot size spot buy without a valid trade price (empty order book?)")
    raw_base = quote_spend / trade_price
    aligned_base = align_option_order_amount(raw_base, contract_size, min_trade_amount)
    if aligned_base <= 0:
        raise ConfigurationError(
            f"Spend {format_decimal(quote_spend, 8)} below minimum notional at price "
            f"{format_decimal(trade_price, 4)} "
            f"(min base={format_decimal(min_trade_amount, 8)})"
        )
    actual_spend = aligned_base * trade_price
    if actual_spend > available_quote:
        raise ConfigurationError(
            f"Aligned buy size {format_decimal(aligned_base, 8)} requires "
            f"{format_decimal(actual_spend, 8)} quote, exceeds available "
            f"{format_decimal(available_quote, 8)}"
        )
    return aligned_base, actual_spend


def _align_spot_limit_price(price: Decimal, instrument: OptionInstrument) -> Decimal:
    tick = instrument.tick_size
    if tick <= 0:
        return price
    return floor_to_step(price, tick)


def _spot_trade_price_quote(
    client: DeribitClient,
    instrument_name: str,
    *,
    direction: str,
    order_type: str,
    instrument: OptionInstrument,
    limit_price: Decimal | None = None,
) -> tuple[Decimal, str, OrderBookSnapshot]:
    book = OrderBookSnapshot.from_api(client.get_order_book(instrument_name, depth=1))
    is_buy = direction == "buy"

    if order_type == "market":
        if is_buy and book.best_ask_price > 0:
            return book.best_ask_price, "best_ask", book
        if not is_buy and book.best_bid_price > 0:
            return book.best_bid_price, "best_bid", book
        if book.mark_price > 0:
            return book.mark_price, "mark", book
        if book.index_price > 0:
            return book.index_price, "index", book
        return Decimal("0"), "unavailable", book

    if limit_price is not None and limit_price > 0:
        return _align_spot_limit_price(limit_price, instrument), "explicit", book
    if is_buy and book.best_bid_price > 0:
        return _align_spot_limit_price(book.best_bid_price, instrument), "best_bid", book
    if not is_buy and book.best_ask_price > 0:
        return _align_spot_limit_price(book.best_ask_price, instrument), "best_ask", book
    if book.mark_price > 0:
        return _align_spot_limit_price(book.mark_price, instrument), "mark", book
    if book.index_price > 0:
        return _align_spot_limit_price(book.index_price, instrument), "index", book
    return Decimal("0"), "unavailable", book


def _spot_reference_mark_price(book: OrderBookSnapshot) -> Decimal:
    """Reference USD price for spot slippage checks (mark preferred)."""
    if book.mark_price > 0:
        return book.mark_price
    if book.index_price > 0:
        return book.index_price
    if book.best_bid_price > 0 and book.best_ask_price > 0:
        return (book.best_bid_price + book.best_ask_price) / Decimal("2")
    if book.best_bid_price > 0:
        return book.best_bid_price
    if book.best_ask_price > 0:
        return book.best_ask_price
    return Decimal("0")


def resolve_protected_spot_order(
    *,
    direction: str,
    book: OrderBookSnapshot,
    instrument: OptionInstrument,
    order_type: str,
    max_slippage_pct: Decimal,
) -> tuple[str, Decimal | None, Decimal | None, str | None]:
    """Map a market spot order to a limit IOC when ``max_slippage_pct`` > 0.

    Returns ``(effective_order_type, limit_price, reference_mark, skip_reason)``.
    For sells the limit floor is ``mark * (1 - max_slippage_pct)``; skip when
    ``best_bid`` is below that floor.
    """
    if order_type != "market" or max_slippage_pct <= 0:
        return order_type, None, None, None

    mark = _spot_reference_mark_price(book)
    if mark <= 0:
        return order_type, None, None, "mark_price_unavailable"

    is_buy = direction.lower() == "buy"
    if is_buy:
        cap = _align_spot_limit_price(mark * (Decimal("1") + max_slippage_pct), instrument)
        if book.best_ask_price > 0 and book.best_ask_price > cap:
            return order_type, None, mark, "slippage_exceeded"
        return "limit", cap, mark, None

    floor = _align_spot_limit_price(mark * (Decimal("1") - max_slippage_pct), instrument)
    if book.best_bid_price > 0 and book.best_bid_price < floor:
        return order_type, None, mark, "slippage_exceeded"
    return "limit", floor, mark, None


def place_protected_spot_order(
    client: DeribitClient,
    *,
    instrument: OptionInstrument,
    instrument_name: str,
    direction: str,
    amount: Decimal,
    label: str,
    order_type: str,
    max_slippage_pct: Decimal,
    live: bool,
) -> dict[str, Any]:
    """Place a spot order; market requests honor ``max_slippage_pct`` vs mark."""
    book = OrderBookSnapshot.from_api(client.get_order_book(instrument_name, depth=1))
    effective_type, limit_px, reference_mark, skip_reason = resolve_protected_spot_order(
        direction=direction,
        book=book,
        instrument=instrument,
        order_type=order_type,
        max_slippage_pct=max_slippage_pct,
    )
    payload: dict[str, Any] = {
        "instrument_name": instrument_name,
        "direction": direction,
        "amount": format_decimal(amount, 8),
        "requested_order_type": order_type,
        "order_type": effective_type,
        "max_slippage_pct": format_decimal(max_slippage_pct, 8),
        "live": live,
    }
    if reference_mark is not None and reference_mark > 0:
        payload["reference_mark_price"] = format_decimal(reference_mark, 4)
    if limit_px is not None and limit_px > 0:
        payload["slippage_limit_price"] = format_decimal(limit_px, 4)
    if book.best_bid_price > 0:
        payload["best_bid_price"] = format_decimal(book.best_bid_price, 4)
    if book.best_ask_price > 0:
        payload["best_ask_price"] = format_decimal(book.best_ask_price, 4)

    if skip_reason:
        payload["skipped"] = True
        payload["reason"] = skip_reason
        return payload
    if not live:
        payload["preview"] = True
        return payload

    order_kwargs: dict[str, Any] = {
        "instrument_name": instrument_name,
        "amount": amount,
        "label": label,
        "order_type": effective_type,
    }
    if effective_type == "limit":
        if limit_px is None or limit_px <= 0:
            payload["skipped"] = True
            payload["reason"] = "limit_price_unavailable"
            return payload
        order_kwargs["price"] = limit_px
        order_kwargs["time_in_force"] = "immediate_or_cancel"

    place_order = client.place_buy_order if direction.lower() == "buy" else client.place_sell_order
    response = place_order(**order_kwargs)
    payload["response"] = response
    order = response.get("order") if isinstance(response, dict) else None
    if isinstance(order, dict):
        payload["order_id"] = order.get("order_id")
        payload["order_state"] = order.get("order_state")
        fill_price = to_decimal(order.get("average_price"))
        if fill_price > 0:
            payload["average_price"] = format_decimal(fill_price, 4)
    return payload


def _attach_trade_price_fields(
    payload: dict[str, Any],
    *,
    direction: str,
    trade_price: Decimal,
    price_source: str,
    quote_currency: str,
    base_amount: Decimal,
    from_amount: Decimal | None = None,
    book: OrderBookSnapshot | None = None,
) -> None:
    if trade_price <= 0:
        payload["trade_price"] = None
        payload["trade_price_source"] = price_source
        return
    payload["trade_price"] = format_decimal(trade_price, 4)
    payload["trade_price_source"] = price_source
    payload["quote_currency"] = quote_currency
    if direction == "sell":
        payload["estimated_quote_proceeds"] = format_decimal(base_amount * trade_price, 4)
        payload["estimated_to_amount"] = format_decimal(base_amount * trade_price, 8)
    else:
        spend = from_amount if from_amount is not None else base_amount * trade_price
        payload["estimated_spend"] = format_decimal(spend, 4)
        payload["estimated_to_amount"] = format_decimal(base_amount, 8)
        payload["estimated_quote_proceeds"] = format_decimal(spend, 4)
    if book is not None:
        if book.best_bid_price > 0:
            payload["best_bid_price"] = format_decimal(book.best_bid_price, 4)
        if book.best_ask_price > 0:
            payload["best_ask_price"] = format_decimal(book.best_ask_price, 4)
        if book.mark_price > 0:
            payload["mark_price"] = format_decimal(book.mark_price, 4)


def _match_subaccount_row(row: dict[str, Any], *, name: str) -> bool:
    needle = name.strip().lower()
    if not needle:
        return False
    for key in ("username", "system_name"):
        value = str(row.get(key) or "").strip().lower()
        if value == needle or needle in value:
            return True
    return False


def resolve_source_subaccount_id(
    client: DeribitClient,
    *,
    strategy_env: Path | None = None,
) -> tuple[int, str]:
    if strategy_env is not None and strategy_env.is_file():
        raw_id = dotenv_values(strategy_env).get("SUBACCOUNT_ID")
        if raw_id is not None and str(raw_id).strip():
            sub_id = int(str(raw_id).strip())
            return sub_id, f"id:{sub_id}"

    try:
        rows = client.get_subaccounts(with_portfolio=False)
    except ExchangeError as exc:
        raise ConfigurationError(
            "Cannot resolve strategy subaccount id via private/get_subaccounts. "
            "Set SUBACCOUNT_ID in the strategy account env, or ensure account:read scope. "
            f"API error: {exc}"
        ) from exc

    subs = _subaccount_rows(rows)
    if len(subs) == 1:
        row = subs[0]
        return int(row["id"]), str(row.get("username") or row.get("system_name") or row["id"])
    if len(subs) > 1:
        names = ", ".join(str(row.get("username") or row.get("system_name") or row.get("id")) for row in subs)
        raise ConfigurationError(
            f"Multiple strategy subaccounts visible ({names}). Set SUBACCOUNT_ID in the strategy account env file."
        )
    raise ConfigurationError(
        "Cannot resolve strategy subaccount id (no subaccount visible on this API key). "
        "Set SUBACCOUNT_ID in the strategy account env file."
    )


def _main_transfer_client(investor_dir: Path) -> DeribitClient | None:
    main_env = main_account_env_path(investor_dir)
    if not main_env.is_file() or not has_private_creds_for_env(main_env):
        return None
    main_cfg = load_config(main_env, require_private=True)
    if not main_cfg.is_main_account:
        raise ConfigurationError(f"Main transfer env must set ACCOUNT_ROLE=main: {main_env}")
    return DeribitClient(main_cfg)


def _transfer_not_allowed_help(exc: ExchangeError) -> ConfigurationError:
    return ConfigurationError(
        "Deribit rejected the transfer (12100 transfer_not_allowed). "
        "Subaccount-to-subaccount transfers must use a **main account** API key with "
        "wallet:read_write at config/investors/<id>/accounts/.env.main "
        "(create the key on the Deribit main account, not on a subaccount). "
        "The bot will call submit_transfer_between_subaccounts with explicit source and "
        "destination subaccount ids. "
        f"Original error: {exc}"
    )


def resolve_fee_subaccount_id(
    client: DeribitClient,
    *,
    fee_config: FeeSubaccountConfig,
    destination_id: int | None = None,
    investor_dir: Path | None = None,
) -> tuple[int, str]:
    if destination_id is not None:
        return int(destination_id), f"id:{destination_id}"
    if fee_config.subaccount_id is not None:
        return fee_config.subaccount_id, f"id:{fee_config.subaccount_id}"

    try:
        rows = client.get_subaccounts(with_portfolio=False)
    except ExchangeError as exc:
        rows = []
        strategy_api_error = exc
    else:
        strategy_api_error = None

    matches = [row for row in rows if _match_subaccount_row(row, name=fee_config.subaccount_name)]
    if len(matches) == 1:
        row = matches[0]
        return int(row["id"]), str(row.get("username") or row.get("system_name") or fee_config.subaccount_name)
    if len(matches) > 1:
        names = ", ".join(str(row.get("username") or row.get("system_name") or row.get("id")) for row in matches)
        raise ConfigurationError(
            f"Multiple subaccounts match FEE_SUBACCOUNT_NAME={fee_config.subaccount_name!r}: {names}. "
            "Set FEE_SUBACCOUNT_ID explicitly."
        )

    if investor_dir is not None:
        via_fee = _resolve_fee_subaccount_id_via_fee_env(investor_dir, fee_config=fee_config)
        if via_fee is not None:
            return via_fee

    if strategy_api_error is not None:
        raise ConfigurationError(
            "Cannot resolve fee subaccount id via private/get_subaccounts. "
            "Set FEE_SUBACCOUNT_ID in config/investors/<id>/.env.investor "
            f"(Deribit subaccount name default: {DEFAULT_FEE_SUBACCOUNT_NAME!r}), "
            "or ensure accounts/.env.fee has valid API credentials. "
            f"API error: {strategy_api_error}"
        ) from strategy_api_error

    known = (
        ", ".join(
            sorted(
                {str(row.get("username") or row.get("system_name") or row.get("id")) for row in _subaccount_rows(rows)}
            )
        )
        or "(none visible from strategy sub-account API)"
    )
    raise ConfigurationError(
        f"Fee subaccount {fee_config.subaccount_name!r} not visible from strategy sub-account API "
        f"(visible: {known}). "
        "Fix: set FEE_SUBACCOUNT_ID in .env.investor, pass --destination-id, "
        "or configure accounts/.env.fee so the bot can read the fee wallet id."
    )


def trade_spot(
    config: BotConfig,
    client: DeribitClient,
    *,
    from_currency: str,
    amount: str | None,
    to_currency: str = "USDC",
    instrument_name: str | None = None,
    order_type: str = "market",
    limit_price: str | None = None,
    sell_all: bool = False,
    live: bool = False,
    label: str | None = None,
) -> dict[str, Any]:
    if config.is_fee_collection_account:
        raise ConfigurationError("trade-spot must run on a strategy sub-account, not ACCOUNT_ROLE=fee")

    direction, base, quote = resolve_spot_trade_side(from_currency, to_currency)
    resolved_instrument = (instrument_name or spot_instrument_name(base, quote)).strip()
    instrument = _lookup_spot_instrument(client, resolved_instrument, base)
    explicit_limit = to_decimal(limit_price) if limit_price is not None and str(limit_price).strip() else None

    trade_price, price_source, book = _spot_trade_price_quote(
        client,
        resolved_instrument,
        direction=direction,
        order_type=order_type,
        instrument=instrument,
        limit_price=explicit_limit,
    )

    from_ccy = from_currency.upper()
    to_ccy = to_currency.upper()
    from_summary = _summary_for_currency(client, from_ccy)
    from_available = Decimal("0")
    if from_summary is not None:
        from_available = max(
            from_summary.available_funds,
            from_summary.available_withdrawal_funds,
            from_summary.balance,
        )

    from_amount: Decimal | None = None
    requested_from_amount: Decimal | None = None
    if direction == "sell":
        order_amount = _resolve_sell_base_amount(
            amount_raw=amount,
            available=from_available,
            contract_size=instrument.contract_size,
            min_trade_amount=instrument.min_trade_amount,
            use_all=sell_all,
        )
        from_amount = order_amount
        requested_from_amount = order_amount
    else:
        if sell_all or (amount is not None and str(amount).strip().lower() == "all"):
            requested_from_amount = from_available
        elif amount is not None and str(amount).strip():
            requested_from_amount = to_decimal(amount)
        order_amount, from_amount = _resolve_buy_base_amount(
            quote_spend_raw=amount,
            available_quote=from_available,
            trade_price=trade_price,
            contract_size=instrument.contract_size,
            min_trade_amount=instrument.min_trade_amount,
            use_all=sell_all,
        )

    payload: dict[str, Any] = {
        "action": "trade_spot" if live else "trade_spot_preview",
        "live": live,
        "direction": direction,
        "from_currency": from_ccy,
        "to_currency": to_ccy,
        "base_currency": base,
        "quote_currency": quote,
        "instrument_name": resolved_instrument,
        "amount": format_decimal(order_amount, 8),
        "from_amount": format_decimal(requested_from_amount or from_amount, 8),
        "available": format_decimal(from_available, 8),
        "order_type": order_type,
    }
    _attach_trade_price_fields(
        payload,
        direction=direction,
        trade_price=trade_price,
        price_source=price_source,
        quote_currency=quote,
        base_amount=order_amount,
        from_amount=from_amount,
        book=book,
    )
    if order_type == "limit" and trade_price > 0:
        payload["limit_price"] = format_decimal(trade_price, 4)
    if order_type == "market" and config.covered_call_spot_max_slippage_pct > 0:
        effective_type, limit_px, reference_mark, skip_reason = resolve_protected_spot_order(
            direction=direction,
            book=book,
            instrument=instrument,
            order_type=order_type,
            max_slippage_pct=config.covered_call_spot_max_slippage_pct,
        )
        payload["max_slippage_pct"] = format_decimal(config.covered_call_spot_max_slippage_pct, 8)
        payload["effective_order_type"] = effective_type
        if reference_mark is not None and reference_mark > 0:
            payload["reference_mark_price"] = format_decimal(reference_mark, 4)
        if limit_px is not None and limit_px > 0:
            payload["slippage_limit_price"] = format_decimal(limit_px, 4)
        if skip_reason:
            payload["would_skip"] = skip_reason
    if not live:
        return payload

    order_label = label or f"{config.order_label_prefix}-spot-{direction}"
    protected = place_protected_spot_order(
        client,
        instrument=instrument,
        instrument_name=resolved_instrument,
        direction=direction,
        amount=order_amount,
        label=order_label,
        order_type=order_type,
        max_slippage_pct=config.covered_call_spot_max_slippage_pct,
        live=True,
    )
    if protected.get("skipped"):
        payload["action"] = "trade_spot_skipped"
        payload["reason"] = protected.get("reason")
        payload["reference_mark_price"] = protected.get("reference_mark_price")
        payload["slippage_limit_price"] = protected.get("slippage_limit_price")
        payload["order_type"] = protected.get("order_type", order_type)
        return payload

    response = protected.get("response")
    payload["order_type"] = protected.get("order_type", order_type)
    if protected.get("requested_order_type") == "market" and payload["order_type"] == "limit":
        payload["order_type_note"] = "market_with_mark_slippage_cap"
    if protected.get("reference_mark_price"):
        payload["reference_mark_price"] = protected.get("reference_mark_price")
    if protected.get("slippage_limit_price"):
        payload["slippage_limit_price"] = protected.get("slippage_limit_price")
    payload["response"] = response
    order = response.get("order") if isinstance(response, dict) else None
    if isinstance(order, dict):
        payload["order_id"] = order.get("order_id")
        payload["order_state"] = order.get("order_state")
        payload["average_price"] = order.get("average_price")
        fill_price = to_decimal(order.get("average_price"))
        if fill_price > 0:
            _attach_trade_price_fields(
                payload,
                direction=direction,
                trade_price=fill_price,
                price_source="average_fill",
                quote_currency=quote,
                base_amount=order_amount,
                from_amount=from_amount,
            )
    return payload


def internal_transfer(
    config: BotConfig,
    client: DeribitClient,
    *,
    investor_dir: Path,
    strategy_env: Path | None = None,
    currency: str,
    amount: str,
    destination_id: int | None = None,
    live: bool = False,
    nonce: str | None = None,
) -> dict[str, Any]:
    if config.is_fee_collection_account or config.is_main_account:
        raise ConfigurationError("internal-transfer must run on a strategy sub-account, not fee/main env")

    ccy = currency.upper()
    if ccy not in TRANSFER_CURRENCIES:
        raise ConfigurationError(
            f"Unsupported transfer currency {ccy!r}; expected one of: {', '.join(sorted(TRANSFER_CURRENCIES))}"
        )

    transfer_amount = to_decimal(amount)
    if transfer_amount <= 0:
        raise ConfigurationError("--amount must be positive")

    summary = _summary_for_currency(client, ccy)
    available = Decimal("0")
    if summary is not None:
        available = max(summary.available_withdrawal_funds, summary.available_funds, summary.balance)
    if transfer_amount > available:
        raise ConfigurationError(
            f"Requested transfer {format_decimal(transfer_amount, 8)} {ccy} exceeds available "
            f"{format_decimal(available, 8)} {ccy}"
        )

    fee_config = load_fee_subaccount_config(investor_dir)
    dest_id, dest_label = resolve_fee_subaccount_id(
        client,
        fee_config=fee_config,
        destination_id=destination_id,
        investor_dir=investor_dir,
    )
    source_id, source_label = resolve_source_subaccount_id(client, strategy_env=strategy_env)
    main_client = _main_transfer_client(investor_dir)
    transfer_via = "main_account_api" if main_client is not None else "strategy_subaccount_api"

    payload: dict[str, Any] = {
        "action": "internal_transfer" if live else "internal_transfer_preview",
        "live": live,
        "currency": ccy,
        "amount": format_decimal(transfer_amount, 8),
        "available": format_decimal(available, 8),
        "source_subaccount_id": source_id,
        "source_subaccount": source_label,
        "destination_subaccount_id": dest_id,
        "destination_subaccount": dest_label,
        "fee_subaccount_name": fee_config.subaccount_name,
        "transfer_via": transfer_via,
    }
    if main_client is None:
        payload["transfer_note"] = (
            "No accounts/.env.main credentials found; live transfer may fail with 12100. "
            "Deribit requires main-account API auth for subaccount-to-subaccount transfers."
        )
    if not live:
        return payload

    transfer_client = main_client or client
    transfer_kwargs: dict[str, Any] = {
        "currency": ccy,
        "amount": transfer_amount,
        "destination": dest_id,
        "nonce": nonce,
    }
    if main_client is not None:
        transfer_kwargs["source"] = source_id
    try:
        response = transfer_client.submit_transfer_between_subaccounts(**transfer_kwargs)
    except ExchangeError as exc:
        text = str(exc)
        if "12100" in text or "transfer_not_allowed" in text:
            raise _transfer_not_allowed_help(exc) from exc
        raise
    payload["response"] = response
    if isinstance(response, dict):
        payload["transfer_id"] = response.get("id")
        payload["transfer_state"] = response.get("state")
        payload["other_side"] = response.get("other_side")
    return payload


def run_wallet_command(
    *,
    command: str,
    investor: str | None,
    env_file: str,
    repo_root: Path | None,
    live: bool,
    json_output: bool,
    **kwargs: Any,
) -> dict[str, Any]:
    root = find_repo_root(repo_root or Path.cwd())
    if root is None:
        raise SystemExit("Cannot locate repository root")

    config = load_config(env_file, require_private=True)
    if config.is_fee_collection_account:
        raise ConfigurationError(f"{command} must use a strategy sub-account from accounts.toml, not ACCOUNT_ROLE=fee")

    investor_dir: Path | None = None
    if investor:
        manifest = load_investor_manifest(investor, repo_root=root)
        investor_dir = manifest.root

    client = DeribitClient(config)

    if command == "trade-spot":
        return trade_spot(
            config,
            client,
            from_currency=kwargs["from_currency"],
            amount=kwargs.get("amount"),
            to_currency=kwargs.get("to_currency") or "USDC",
            instrument_name=kwargs.get("instrument_name"),
            order_type=kwargs.get("order_type") or "market",
            limit_price=kwargs.get("limit_price"),
            sell_all=bool(kwargs.get("sell_all")),
            live=live,
            label=kwargs.get("label"),
        )

    if command == "internal-transfer":
        if investor_dir is None:
            raise ConfigurationError("internal-transfer requires --investor <ID> to resolve fee subaccount")
        return internal_transfer(
            config,
            client,
            investor_dir=investor_dir,
            strategy_env=Path(env_file),
            currency=kwargs["currency"],
            amount=kwargs["amount"],
            destination_id=kwargs.get("destination_id"),
            live=live,
            nonce=kwargs.get("nonce"),
        )

    raise ConfigurationError(f"Unknown wallet command: {command}")
