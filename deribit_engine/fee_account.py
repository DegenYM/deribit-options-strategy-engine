"""Read-only Deribit wallet balance for investor fee-collection sub-accounts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from .client import DeribitClient
from .config import has_private_creds_for_env, load_config
from .env_layout import fee_account_env_path, find_repo_root, load_investor_manifest
from .exceptions import ConfigurationError, ExchangeError
from .models import AccountSummary


def _summary_dict(summary: AccountSummary) -> dict[str, str]:
    return {
        "balance": str(summary.balance),
        "equity": str(summary.equity),
        "available_funds": str(summary.available_funds),
        "available_withdrawal_funds": str(summary.available_withdrawal_funds),
        "equity_usd": str(summary.total_equity_usd),
    }


def _fetch_account_summaries(client: DeribitClient) -> list[dict[str, Any]]:
    try:
        return client.get_account_summaries(extended=True)
    except ExchangeError as exc:
        text = str(exc)
        if "account:read" in text or "code=13021" in text:
            raise ConfigurationError(
                "Fee account API key is missing Account=read scope (Deribit account:read). "
                "Recreate the key on the fee sub-account with Account=read, Wallet=none, "
                "Trade=none. See docs/investor-onboarding-zh-TW.md section 6.2."
            ) from exc
        raise


def fetch_fee_account_balance(
    investor: str | Path,
    *,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """Fetch live balances for the investor's fee-collection sub-account."""
    root = find_repo_root(repo_root or Path.cwd())
    if root is None:
        raise RuntimeError("Cannot locate repository root (missing deribit_engine/)")
    manifest = load_investor_manifest(investor, repo_root=root)
    fee_env = fee_account_env_path(manifest.root)
    if not fee_env.is_file():
        raise ConfigurationError(f"Fee account env not found: {fee_env}")
    if not has_private_creds_for_env(fee_env):
        raise ConfigurationError(f"Fee account env missing DERIBIT_CLIENT_ID/SECRET: {fee_env}")
    config = load_config(fee_env, require_private=True)
    if not config.is_fee_collection_account:
        raise ConfigurationError(f"Fee env must set ACCOUNT_ROLE=fee: {fee_env}")

    client = DeribitClient(config)
    rows = _fetch_account_summaries(client)
    books: dict[str, dict[str, str]] = {}
    total_equity_usdc = Decimal("0")
    for item in rows:
        summary = AccountSummary.from_api(item)
        if not summary.currency:
            continue
        books[summary.currency] = _summary_dict(summary)
        total_equity_usdc += summary.total_equity_usd

    return {
        "investor_id": manifest.investor_id,
        "env": config.env,
        "fee_env": str(fee_env),
        "books": books,
        "total_equity_usdc": str(total_equity_usdc),
    }
