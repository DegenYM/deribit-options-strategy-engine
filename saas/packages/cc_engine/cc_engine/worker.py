"""Tenant-agnostic Covered Call worker: ping / run / pause / panic."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from deribit_engine.client import DeribitClient
from deribit_engine.config import load_config_from_values
from deribit_engine.engine import DeribitOptionTrialBot

from .catalog import merged_catalog_env
from .settings import CoveredCallSettings
from .snapshot import load_worker_snapshot

LOGGER = logging.getLogger("cc_engine.worker")
_STOP = False


def _install_signal_handlers() -> None:
    def _handle(signum: int, _frame: Any) -> None:
        global _STOP
        LOGGER.info("received signal %s; stopping after current cycle", signum)
        _STOP = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def settings_to_env(settings: CoveredCallSettings) -> dict[str, str]:
    values = merged_catalog_env(settings.risk_tier)
    collaterals = list(settings.coins)
    if settings.profit_sweep and "USDT" not in collaterals:
        collaterals.append("USDT")
    values["TRADED_COLLATERALS"] = ",".join(collaterals)
    values["MANAGED_CURRENCIES"] = ",".join(settings.coins)
    values["SCAN_UNDERLYINGS"] = ",".join(settings.coins)
    values["COVERED_CALL_PROFIT_SWEEP_ENABLED"] = "true" if settings.profit_sweep else "false"
    values["DERIBIT_CLIENT_ID"] = settings.client_id
    values["DERIBIT_CLIENT_SECRET"] = settings.client_secret
    values["STATE_FILE"] = str(settings.state_file)
    values["ORDER_LABEL_PREFIX"] = f"cc{settings.tenant_id.replace('-', '')[:10]}"
    return values


def apply_telegram_env(settings: CoveredCallSettings) -> None:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        os.environ["TELEGRAM_ALERTS_ENABLED"] = "true"
        os.environ["TELEGRAM_BOT_TOKEN"] = settings.telegram_bot_token
        os.environ["TELEGRAM_CHAT_ID"] = settings.telegram_chat_id
    else:
        os.environ["TELEGRAM_ALERTS_ENABLED"] = "false"


def build_bot(settings: CoveredCallSettings, *, require_private: bool = True) -> DeribitOptionTrialBot:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    apply_telegram_env(settings)
    config = load_config_from_values(settings_to_env(settings), require_private=require_private)
    if config.option_strategy != "covered_call":
        raise RuntimeError(f"SaaS worker refused strategy {config.option_strategy!r}")
    return DeribitOptionTrialBot(config, DeribitClient(config))


class CoveredCallWorker:
    def __init__(self, settings: CoveredCallSettings):
        self.settings = settings
        self._bot: DeribitOptionTrialBot | None = None

    def _ensure_bot(self, *, require_private: bool = True) -> DeribitOptionTrialBot:
        if self._bot is None:
            self._bot = build_bot(self.settings, require_private=require_private)
        return self._bot

    def ping(self) -> dict[str, Any]:
        bot = self._ensure_bot(require_private=bool(self.settings.client_id and self.settings.client_secret))
        result = bot.ping()
        return {
            "ok": True,
            "tenant_id": self.settings.tenant_id,
            "strategy": "covered_call",
            "risk_tier": self.settings.risk_tier,
            "coins": list(self.settings.coins),
            "exchange": result,
        }

    def run_forever(self) -> dict[str, Any]:
        global _STOP
        _STOP = False
        _install_signal_handlers()
        bot = self._ensure_bot(require_private=True)
        live = self.settings.live
        LOGGER.info(
            "starting covered-call worker tenant=%s live=%s tier=%s coins=%s",
            self.settings.tenant_id,
            live,
            self.settings.risk_tier,
            ",".join(self.settings.coins),
        )
        # cycles=0 is the engine's infinite loop; SIGTERM sets _STOP but the engine
        # loop does not check it, so the supervisor's SIGTERM is the pause mechanism.
        # For in-process tests, call run_cycles() instead.
        return bot.run(live=live, cycles=0, currencies=self.settings.coins)

    def run_cycles(self, cycles: int = 1) -> dict[str, Any]:
        bot = self._ensure_bot(require_private=True)
        return bot.run(live=self.settings.live, cycles=cycles, currencies=self.settings.coins)

    def pause(self) -> dict[str, Any]:
        """Supervisor-facing: persist a pause marker. Process exit is the real pause."""
        marker = self.settings.state_dir / "paused.json"
        marker.write_text(
            json.dumps({"tenant_id": self.settings.tenant_id, "paused": True}, indent=2),
            encoding="utf-8",
        )
        return {"ok": True, "action": "pause", "tenant_id": self.settings.tenant_id}

    def panic_close(self) -> dict[str, Any]:
        bot = self._ensure_bot(require_private=True)
        result = bot.panic_close(live=self.settings.live)
        self.pause()
        return {"ok": True, "tenant_id": self.settings.tenant_id, **result}

    def snapshot(self) -> dict[str, Any]:
        return load_worker_snapshot(self.settings)


def _settings_from_args(args: argparse.Namespace) -> CoveredCallSettings:
    coins = tuple(part.strip().upper() for part in str(args.coins).split(",") if part.strip())
    return CoveredCallSettings(
        tenant_id=args.tenant_id,
        risk_tier=args.risk_tier,
        coins=coins,
        profit_sweep=bool(args.profit_sweep),
        live=bool(args.live),
        state_dir=Path(args.state_dir),
        client_id=args.client_id or os.environ.get("DERIBIT_CLIENT_ID", ""),
        client_secret=args.client_secret or os.environ.get("DERIBIT_CLIENT_SECRET", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-engine")
    parser.add_argument("command", choices=("ping", "run", "panic", "snapshot", "pause"))
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--risk-tier", default="medium")
    parser.add_argument("--coins", default="BTC")
    parser.add_argument("--profit-sweep", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--client-id", default="")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--cycles", type=int, default=0, help="run N cycles then exit; 0 = forever")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = CoveredCallWorker(_settings_from_args(args))
    if args.command == "ping":
        print(json.dumps(worker.ping(), indent=2))
        return 0
    if args.command == "snapshot":
        print(json.dumps(worker.snapshot(), indent=2))
        return 0
    if args.command == "pause":
        print(json.dumps(worker.pause(), indent=2))
        return 0
    if args.command == "panic":
        print(json.dumps(worker.panic_close(), indent=2))
        return 0
    if args.cycles and args.cycles > 0:
        print(json.dumps(worker.run_cycles(args.cycles), default=str))
        return 0
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
