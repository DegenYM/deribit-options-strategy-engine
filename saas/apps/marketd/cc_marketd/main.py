"""Shared public market snapshot for all tenants (one Deribit public poller)."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cc_engine.catalog import merged_catalog_env
from deribit_engine.client import DeribitClient
from deribit_engine.config import load_config_from_values
from deribit_engine.market_snapshot_store import MarketSnapshotStore

LOGGER = logging.getLogger("cc_marketd")


def public_client() -> DeribitClient:
    values = merged_catalog_env("medium")
    values.pop("DERIBIT_CLIENT_ID", None)
    values.pop("DERIBIT_CLIENT_SECRET", None)
    config = load_config_from_values(values, require_private=False)
    return DeribitClient(config)


def fetch_snapshot(client: DeribitClient | None = None) -> dict[str, Any]:
    client = client or public_client()
    btc = client.get_index_price("btc_usd")
    eth = client.get_index_price("eth_usd")
    return {
        "ts": datetime.now(tz=UTC).isoformat(),
        "btc_usd": btc.get("index_price") if isinstance(btc, dict) else btc,
        "eth_usd": eth.get("index_price") if isinstance(eth, dict) else eth,
        "source": "deribit_public",
        "note": "Shared market daemon — workers use private keys only for account/orders.",
    }


def write_snapshot(payload: dict[str, Any], data_dir: Path) -> Path:
    market_dir = data_dir / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    latest = market_dir / "latest.json"
    latest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    store = MarketSnapshotStore(market_dir / "market.db")
    store.append_from_spot_payload(
        {
            "BTC": payload.get("btc_usd") or 0,
            "ETH": payload.get("eth_usd") or 0,
        }
    )
    return latest


def loop_forever(poll_seconds: float = 30.0) -> None:
    from cc_saas.config import settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = public_client()
    LOGGER.info("marketd polling Deribit public index every %ss", poll_seconds)
    while True:
        try:
            payload = fetch_snapshot(client)
            write_snapshot(payload, settings.data_dir)
            LOGGER.info("wrote market snapshot btc=%s eth=%s", payload.get("btc_usd"), payload.get("eth_usd"))
        except Exception:
            LOGGER.exception("marketd tick failed")
        time.sleep(poll_seconds)


def main() -> None:
    loop_forever(float(os.environ.get("MARKETD_POLL_SECONDS", "30")))


if __name__ == "__main__":
    main()
