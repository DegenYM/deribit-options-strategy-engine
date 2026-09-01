"""Admin-console trading actions (preview first; live requires confirm=LIVE)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import assert_trading_account, has_private_creds_for_env, load_config
from ..env_layout import InvestorAccountSpec, InvestorManifest, load_investor_manifest
from ..exceptions import ConfigurationError
from ..investor_registry import validate_investor_id
from ..models import StrategyState, TradeGroup
from ..spot_exit_ops import spot_exit_realized_usdt
from ..spot_restore_ops import (
    _spot_restore_order_is_open,
    execute_spot_restore_for_group,
    list_spot_restore_candidates,
    mark_spot_restore_operator_cancelled,
    spot_restore_realized_usdt,
    unrestored_spot_exit_native,
)
from ..state import StrategyStateStore
from ..utils import format_decimal, to_decimal

LIVE_CONFIRM = "LIVE"
RESTORE_REASON = "emergency_spot_restore"


def _same_group_id(left: str, right: str) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if a == b:
        return True
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return False


def load_manifest(investor_id: str, *, repo_root: Path) -> InvestorManifest:
    return load_investor_manifest(validate_investor_id(investor_id), repo_root=repo_root)


def _load_state(account: InvestorAccountSpec) -> StrategyState:
    config = load_config(account.env_path, require_private=False)
    return StrategyStateStore(config.state_file).load()


def _build_bot(account: InvestorAccountSpec):
    from ..client import DeribitClient
    from ..engine import DeribitOptionTrialBot

    if not has_private_creds_for_env(account.env_path):
        raise ConfigurationError(f"account {account.slug!r} is missing Deribit API credentials")
    config = load_config(account.env_path, require_private=True)
    assert_trading_account(config)
    return DeribitOptionTrialBot(config, DeribitClient(config))


def _require_live_confirm(*, live: bool, confirm: str | None) -> None:
    if not live:
        return
    if str(confirm or "").strip() != LIVE_CONFIRM:
        raise ConfigurationError(f"live actions require confirm={LIVE_CONFIRM!r}")


def _account_row(account: InvestorAccountSpec) -> dict[str, Any]:
    return {
        "slug": account.slug,
        "strategy": account.strategy,
        "display_name": account.display_name or account.slug,
        "has_creds": has_private_creds_for_env(account.env_path),
    }


def _open_group_row(account: InvestorAccountSpec, group: TradeGroup) -> dict[str, Any]:
    return {
        "account": account.slug,
        "strategy": account.strategy,
        "group_id": group.group_id,
        "currency": group.currency,
        "status": group.status,
        "quantity": format_decimal(to_decimal(group.quantity), 8),
        "short_instrument_name": group.short_instrument_name,
        "long_instrument_name": group.long_instrument_name or None,
    }


def list_investor_targets(investor_id: str, *, repo_root: Path) -> dict[str, Any]:
    manifest = load_manifest(investor_id, repo_root=repo_root)
    accounts = [_account_row(account) for account in manifest.operational_accounts()]
    open_groups: list[dict[str, Any]] = []
    restore_candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for account in manifest.operational_accounts():
        try:
            state = _load_state(account)
        except Exception as exc:  # noqa: BLE001
            errors.append({"account": account.slug, "error": str(exc)})
            continue
        for group in state.groups:
            if str(group.status or "") != "closed":
                open_groups.append(_open_group_row(account, group))
        for row in list_spot_restore_candidates(state.groups):
            if to_decimal(row.unrestored_amount) <= 0:
                continue
            payload = row.to_dict()
            payload["account"] = account.slug
            payload["strategy"] = account.strategy
            restore_candidates.append(payload)
    return {
        "investor_id": manifest.investor_id,
        "accounts": accounts,
        "open_groups": open_groups,
        "restore_candidates": restore_candidates,
        "errors": errors,
    }


def _resolve_account(manifest: InvestorManifest, slug: str | None) -> InvestorAccountSpec:
    wanted = str(slug or "").strip()
    if not wanted:
        raise ConfigurationError("account is required")
    for account in manifest.operational_accounts():
        if account.slug == wanted:
            return account
    known = ", ".join(account.slug for account in manifest.operational_accounts()) or "(none)"
    raise ConfigurationError(f"unknown or non-operational account {wanted!r}; known: {known}")


def _find_group(
    manifest: InvestorManifest,
    *,
    group_id: str,
    account_slug: str | None,
) -> tuple[InvestorAccountSpec, TradeGroup]:
    wanted = str(group_id or "").strip()
    if not wanted:
        raise ConfigurationError("group_id is required")
    hinted = str(account_slug or "").strip()
    resolved = None
    if hinted:
        try:
            resolved = _resolve_account(manifest, hinted)
        except ConfigurationError:
            resolved = next(
                (
                    account
                    for account in manifest.operational_accounts()
                    if account.slug == hinted or (account.display_name or "").lower() == hinted.lower()
                ),
                None,
            )
    accounts = (resolved,) if resolved else manifest.operational_accounts()
    matches: list[tuple[InvestorAccountSpec, TradeGroup]] = []
    for account in accounts:
        try:
            state = _load_state(account)
        except Exception:  # noqa: BLE001
            continue
        for group in state.groups:
            if _same_group_id(group.group_id, wanted):
                matches.append((account, group))
                break
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ConfigurationError(f"group {wanted!r} not found")
    raise ConfigurationError(f"group {wanted!r} exists on multiple accounts; pass account")


def _cancel_resting_restore(bot: Any, group: TradeGroup) -> dict[str, Any] | None:
    order_id = str(group.spot_restore_order_id or "").strip()
    if not order_id:
        return None
    if not _spot_restore_order_is_open(bot.client, order_id):
        return None
    response = bot.client.cancel_order(order_id)
    mark_spot_restore_operator_cancelled(group)
    return {"cancelled_order_id": order_id, "response": response}


def _group_economics(group: TradeGroup) -> dict[str, Any]:
    entry = to_decimal(group.entry_credit)
    debit = to_decimal(group.current_debit)
    fee = to_decimal(group.current_close_fee)
    fee_native = to_decimal(group.current_close_fee_collateral)
    pnl = entry - debit
    book = str(group.collateral_currency or group.currency or "").upper()
    idx = to_decimal(group.entry_index_usd)
    pnl_native = format_decimal(pnl / idx, 8) if book in {"BTC", "ETH"} and idx > 0 else None
    entry_px = to_decimal(group.short_entry_average_price)
    long_px = to_decimal(group.long_entry_average_price)
    return {
        "collateral": book,
        "entry_price": format_decimal(entry_px, 8) if entry_px > 0 else None,
        "long_entry_price": format_decimal(long_px, 8) if long_px > 0 else None,
        "entry_credit_usdc": format_decimal(entry, 4),
        "est_close_cost_usdc": format_decimal(debit, 4),
        "est_close_fee_usdc": format_decimal(fee, 4),
        "est_close_fee_native": format_decimal(fee_native, 8) if fee_native > 0 else None,
        "est_pnl_usdc": format_decimal(pnl, 4),
        "est_pnl_native": pnl_native,
    }


def _close_plan(group: TradeGroup) -> dict[str, Any]:
    return {
        "action": "close-position",
        "order_type": "market",
        "currency": group.currency,
        "quantity": format_decimal(to_decimal(group.quantity), 8),
        "short_instrument_name": group.short_instrument_name,
        "long_instrument_name": group.long_instrument_name or None,
        "status": group.status,
        **_group_economics(group),
    }


def _restore_plan(group: TradeGroup) -> dict[str, Any]:
    unrestored = unrestored_spot_exit_native(group)
    proceeds = spot_exit_realized_usdt(group)
    spent = spot_restore_realized_usdt(group)
    remaining = proceeds - spent
    breakeven = remaining / unrestored if unrestored > 0 and remaining > 0 else None
    return {
        "action": "spot-restore",
        "order_type": "market",
        "restore_reason": RESTORE_REASON,
        "currency": group.currency,
        "unrestored_amount": format_decimal(unrestored, 8),
        "instrument_name": group.short_instrument_name,
        "spot_restore_status": group.spot_restore_status or None,
        "will_cancel_resting_order_id": group.spot_restore_order_id or None,
        "remaining_proceeds_usdt": format_decimal(remaining, 4),
        "breakeven_price": format_decimal(breakeven, 4) if breakeven is not None else None,
        "est_close_price": None,
        "est_pnl_usdc": None,
    }


def run_close_position(
    investor_id: str,
    *,
    repo_root: Path,
    account: str | None,
    group_id: str,
    live: bool,
    confirm: str | None,
) -> dict[str, Any]:
    _require_live_confirm(live=live, confirm=confirm)
    manifest = load_manifest(investor_id, repo_root=repo_root)
    spec, group = _find_group(manifest, group_id=group_id, account_slug=account)
    if str(group.status or "") == "closed":
        return {
            "ok": True,
            "live": live,
            "kind": "close_position",
            "account": spec.slug,
            "group_id": group.group_id,
            "result": {
                "action": "close-position",
                "skipped": [{"group_id": group.group_id, "reason": "already_closed"}],
            },
        }
    if not live:
        return {
            "ok": True,
            "live": False,
            "kind": "close_position",
            "account": spec.slug,
            "group_id": group.group_id,
            "plan": _close_plan(group),
        }
    bot = _build_bot(spec)
    result = bot.close_positions(group_ids=[group.group_id], live=True, order_type="market")
    return {
        "ok": True,
        "live": True,
        "kind": "close_position",
        "account": spec.slug,
        "group_id": group.group_id,
        "result": result,
    }


def run_panic_close(
    investor_id: str,
    *,
    repo_root: Path,
    account: str | None,
    live: bool,
    confirm: str | None,
) -> dict[str, Any]:
    _require_live_confirm(live=live, confirm=confirm)
    manifest = load_manifest(investor_id, repo_root=repo_root)
    specs = (_resolve_account(manifest, account),) if account else manifest.operational_accounts()
    if not specs:
        raise ConfigurationError("no operational accounts")
    if not live:
        accounts: list[dict[str, Any]] = []
        for spec in specs:
            try:
                state = _load_state(spec)
            except Exception as exc:  # noqa: BLE001
                accounts.append({"account": spec.slug, "error": str(exc)})
                continue
            accounts.append(
                {
                    "account": spec.slug,
                    "strategy": spec.strategy,
                    "open_groups": [
                        {
                            "group_id": group.group_id,
                            "short_instrument_name": group.short_instrument_name,
                            "quantity": format_decimal(to_decimal(group.quantity), 8),
                            **_group_economics(group),
                        }
                        for group in state.groups
                        if str(group.status or "") != "closed"
                    ],
                }
            )
        return {
            "ok": True,
            "live": False,
            "kind": "panic_close",
            "accounts": [spec.slug for spec in specs],
            "plan": {
                "action": "panic-close",
                "will": [
                    "cancel resting orders",
                    "market-close all open groups and perps",
                    "write cooldown",
                ],
                "accounts": accounts,
            },
        }
    results = []
    for spec in specs:
        bot = _build_bot(spec)
        results.append({"account": spec.slug, "strategy": spec.strategy, **bot.panic_close(live=True)})
    return {
        "ok": True,
        "live": True,
        "kind": "panic_close",
        "accounts": [spec.slug for spec in specs],
        "results": results,
    }


def run_spot_restore(
    investor_id: str,
    *,
    repo_root: Path,
    account: str | None,
    group_id: str,
    live: bool,
    confirm: str | None,
) -> dict[str, Any]:
    _require_live_confirm(live=live, confirm=confirm)
    manifest = load_manifest(investor_id, repo_root=repo_root)
    spec, listed = _find_group(manifest, group_id=group_id, account_slug=account)
    if unrestored_spot_exit_native(listed) <= 0:
        return {
            "ok": True,
            "live": live,
            "kind": "spot_restore",
            "account": spec.slug,
            "group_id": listed.group_id,
            "result": {"action": "spot_restore_skipped", "reason": "nothing_to_restore"},
        }
    if not live:
        return {
            "ok": True,
            "live": False,
            "kind": "spot_restore",
            "account": spec.slug,
            "group_id": listed.group_id,
            "plan": _restore_plan(listed),
        }
    bot = _build_bot(spec)
    context = bot._load_runtime(live=live)
    group = next(
        (item for item in context.state.groups if _same_group_id(item.group_id, listed.group_id)),
        listed,
    )
    cancelled = None
    if live:
        cancelled = _cancel_resting_restore(bot, group)
    result = execute_spot_restore_for_group(
        bot,
        group,
        live=live,
        order_type="market",
        restore_reason=RESTORE_REASON,
        park_resting=False,
    )
    if live:
        if str(result.get("action") or "").startswith("spot_restore") and "skipped" not in str(
            result.get("action") or ""
        ):
            bot._persist_trade_journal_actions([result])
        bot.state_store.save(context.state)
    return {
        "ok": True,
        "live": live,
        "kind": "spot_restore",
        "account": spec.slug,
        "group_id": group.group_id,
        "cancelled_resting": cancelled,
        "result": result,
    }
