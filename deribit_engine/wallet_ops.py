"""Spot trade and internal transfer helpers for fee collection workflows."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .client import DeribitClient
from .config import BotConfig, load_config
from .env_layout import find_repo_root, load_investor_manifest, resolve_investor_env_path
from .exceptions import ConfigurationError, ExchangeError
from .models import AccountSummary, OptionInstrument
from .utils import align_option_order_amount, format_decimal, to_decimal

DEFAULT_FEE_SUBACCOUNT_NAME = "fee_acc"
SPOT_BASE_CURRENCIES = frozenset({"BTC", "ETH"})
TRANSFER_CURRENCIES = frozenset({"BTC", "ETH", "USDC", "USDT"})


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
    raw_name = values.get("FEE_SUBACCOUNT_NAME")
    subaccount_name = (
        str(raw_name).strip() if raw_name is not None and str(raw_name).strip() else DEFAULT_FEE_SUBACCOUNT_NAME
    )
    return FeeSubaccountConfig(subaccount_id=subaccount_id, subaccount_name=subaccount_name)


def spot_instrument_name(base_currency: str, quote_currency: str) -> str:
    base = base_currency.upper()
    quote = quote_currency.upper()
    if base not in SPOT_BASE_CURRENCIES:
        raise ConfigurationError(f"Unsupported spot base currency {base!r}; expected one of: BTC, ETH")
    if quote not in {"USDC", "USDT"}:
        raise ConfigurationError(f"Unsupported spot quote currency {quote!r}; expected USDC or USDT")
    return f"{base}_{quote}"


def _lookup_spot_instrument(client: DeribitClient, instrument_name: str, base_currency: str) -> OptionInstrument:
    for lookup_currency in ("USDC", base_currency.upper()):
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


def _resolve_amount(
    *,
    amount_raw: str | None,
    available: Decimal,
    contract_size: Decimal,
    min_trade_amount: Decimal,
) -> Decimal:
    if amount_raw is None or not str(amount_raw).strip():
        raise ConfigurationError("--amount is required (or use --all to sell available balance)")
    token = str(amount_raw).strip().lower()
    if token == "all":
        target = available
    else:
        target = to_decimal(amount_raw)
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


def _match_subaccount_row(row: dict[str, Any], *, name: str) -> bool:
    needle = name.strip().lower()
    if not needle:
        return False
    for key in ("username", "system_name"):
        value = str(row.get(key) or "").strip().lower()
        if value == needle or needle in value:
            return True
    return False


def resolve_fee_subaccount_id(
    client: DeribitClient,
    *,
    fee_config: FeeSubaccountConfig,
    destination_id: int | None = None,
) -> tuple[int, str]:
    if destination_id is not None:
        return int(destination_id), f"id:{destination_id}"
    if fee_config.subaccount_id is not None:
        return fee_config.subaccount_id, f"id:{fee_config.subaccount_id}"

    try:
        rows = client.get_subaccounts(with_portfolio=False)
    except ExchangeError as exc:
        raise ConfigurationError(
            "Cannot resolve fee subaccount id via private/get_subaccounts. "
            "Set FEE_SUBACCOUNT_ID in config/investors/<id>/.env.investor "
            f"(Deribit subaccount name default: {DEFAULT_FEE_SUBACCOUNT_NAME!r}). "
            f"API error: {exc}"
        ) from exc

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
    known = (
        ", ".join(
            sorted(
                {
                    str(row.get("username") or row.get("system_name") or row.get("id"))
                    for row in rows
                    if str(row.get("type") or "").lower() == "subaccount"
                }
            )
        )
        or "(none visible)"
    )
    raise ConfigurationError(
        f"Fee subaccount {fee_config.subaccount_name!r} not found via get_subaccounts. "
        f"Visible subaccounts: {known}. Set FEE_SUBACCOUNT_ID in .env.investor."
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
    sell_all: bool = False,
    live: bool = False,
    label: str | None = None,
) -> dict[str, Any]:
    if config.is_fee_collection_account:
        raise ConfigurationError("trade-spot must run on a strategy sub-account, not ACCOUNT_ROLE=fee")

    base = from_currency.upper()
    quote = to_currency.upper()
    resolved_instrument = (instrument_name or spot_instrument_name(base, quote)).strip()
    instrument = _lookup_spot_instrument(client, resolved_instrument, base)
    summary = _summary_for_currency(client, base)
    available = Decimal("0")
    if summary is not None:
        available = max(summary.available_funds, summary.available_withdrawal_funds, summary.balance)

    amount_token = "all" if sell_all else amount
    aligned_amount = _resolve_amount(
        amount_raw=amount_token,
        available=available,
        contract_size=instrument.contract_size,
        min_trade_amount=instrument.min_trade_amount,
    )

    payload: dict[str, Any] = {
        "action": "trade_spot" if live else "trade_spot_preview",
        "live": live,
        "direction": "sell",
        "from_currency": base,
        "to_currency": quote,
        "instrument_name": resolved_instrument,
        "amount": format_decimal(aligned_amount, 8),
        "available": format_decimal(available, 8),
        "order_type": order_type,
    }
    if not live:
        return payload

    order_label = label or f"{config.order_label_prefix}-spot-sell"
    response = client.place_sell_order(
        instrument_name=resolved_instrument,
        amount=aligned_amount,
        label=order_label,
        order_type=order_type,
    )
    payload["response"] = response
    order = response.get("order") if isinstance(response, dict) else None
    if isinstance(order, dict):
        payload["order_id"] = order.get("order_id")
        payload["order_state"] = order.get("order_state")
        payload["average_price"] = order.get("average_price")
    return payload


def internal_transfer(
    config: BotConfig,
    client: DeribitClient,
    *,
    investor_dir: Path,
    currency: str,
    amount: str,
    destination_id: int | None = None,
    live: bool = False,
    nonce: str | None = None,
) -> dict[str, Any]:
    if config.is_fee_collection_account:
        raise ConfigurationError("internal-transfer must run on a strategy sub-account, not ACCOUNT_ROLE=fee")

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
    )

    payload: dict[str, Any] = {
        "action": "internal_transfer" if live else "internal_transfer_preview",
        "live": live,
        "currency": ccy,
        "amount": format_decimal(transfer_amount, 8),
        "available": format_decimal(available, 8),
        "destination_subaccount_id": dest_id,
        "destination_subaccount": dest_label,
        "fee_subaccount_name": fee_config.subaccount_name,
    }
    if not live:
        return payload

    response = client.submit_transfer_between_subaccounts(
        currency=ccy,
        amount=transfer_amount,
        destination=dest_id,
        nonce=nonce,
    )
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
            currency=kwargs["currency"],
            amount=kwargs["amount"],
            destination_id=kwargs.get("destination_id"),
            live=live,
            nonce=kwargs.get("nonce"),
        )

    raise ConfigurationError(f"Unknown wallet command: {command}")
