from __future__ import annotations

from typing import Any

from ..engine import DeribitOptionTrialBot, ExchangePrefetch
from .helpers import _has_private_creds, _live_api_identity
from .types import DashboardAccount, _TtlCache


def _bot_for_account(account: DashboardAccount, *, require_private: bool) -> DeribitOptionTrialBot:
    import deribit_engine.frontend_server as pkg

    cfg = pkg.load_config(account.env_file, require_private=require_private)
    client = pkg.DeribitClient(cfg)
    return DeribitOptionTrialBot(cfg, client)


def _exchange_prefetch_for_account(
    account: DashboardAccount,
    *,
    cache: _TtlCache,
) -> ExchangePrefetch | None:
    if not _has_private_creds(account.config):
        return None
    key = _live_api_identity(account)

    def _fetch() -> ExchangePrefetch:
        import deribit_engine.frontend_server as pkg

        return pkg._bot_for_account(account, require_private=True).fetch_exchange_prefetch()

    return cache.get_or_set(key, _fetch)


def _prefetch_all_accounts(
    accounts: list[DashboardAccount],
    *,
    cache: _TtlCache,
) -> dict[str, ExchangePrefetch | None]:
    """One prefetch per unique Deribit API identity (shared across strategy rows)."""
    prefetches: dict[str, ExchangePrefetch | None] = {}
    for account in accounts:
        if not _has_private_creds(account.config):
            continue
        key = _live_api_identity(account)
        if key not in prefetches:
            prefetches[key] = _exchange_prefetch_for_account(account, cache=cache)
    return prefetches


def _force_refresh_prefetch_all(
    accounts: list[DashboardAccount],
    *,
    cache: _TtlCache,
) -> dict[str, ExchangePrefetch | None]:
    """Fetch a fresh Deribit prefetch per API identity and seed ``cache``.

    Unlike :func:`_prefetch_all_accounts` this bypasses the TTL and always hits the
    exchange, so a background warmer can keep live marks fresh ahead of expiry.
    """
    import deribit_engine.frontend_server as pkg

    prefetches: dict[str, ExchangePrefetch | None] = {}
    for account in accounts:
        if not _has_private_creds(account.config):
            continue
        key = _live_api_identity(account)
        if key in prefetches:
            continue
        prefetch = pkg._bot_for_account(account, require_private=True).fetch_exchange_prefetch()
        cache.seed(key, prefetch)
        prefetches[key] = prefetch
    return prefetches


def _status_payload_for_account(
    account: DashboardAccount,
    *,
    exchange_prefetch_cache: _TtlCache,
    prefetches: dict[str, ExchangePrefetch | None] | None = None,
) -> dict[str, Any]:
    import deribit_engine.frontend_server as pkg

    bot = pkg._bot_for_account(account, require_private=True)
    if prefetches is not None:
        prefetch = prefetches.get(_live_api_identity(account))
    else:
        prefetch = _exchange_prefetch_for_account(account, cache=exchange_prefetch_cache)
    if prefetch is not None:
        return bot.status_with_exchange_prefetch(prefetch, dashboard_display=True)
    return bot.status()
