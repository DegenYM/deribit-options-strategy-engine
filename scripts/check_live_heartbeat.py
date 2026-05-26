#!/usr/bin/env python3
"""Check live bot heartbeat files and alert when stale or missing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alert when live bot heartbeats are stale or missing.")
    parser.add_argument("--investor", help="Only check this investor id")
    parser.add_argument(
        "--stale-seconds",
        type=float,
        help="Age threshold in seconds (default: LIVE_HEARTBEAT_STALE_SECONDS or 600)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable lines")
    parser.add_argument("--dry-run", action="store_true", help="Report stale heartbeats without sending Telegram")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    sys.path.insert(0, str(repo_root))
    from deribit_demo.live_heartbeat import find_stale_heartbeats, stale_seconds_from_environ
    from deribit_demo.telegram_alerts import bootstrap_telegram_env, format_alert_message, send_telegram_alert

    stale_seconds = args.stale_seconds if args.stale_seconds is not None else stale_seconds_from_environ()
    stale_rows = find_stale_heartbeats(repo_root, stale_seconds=stale_seconds, investor_id=args.investor)

    if args.json:
        import json

        payload = {
            "stale_seconds": stale_seconds,
            "stale_count": len(stale_rows),
            "stale": [
                {
                    "investor_id": row.investor_id,
                    "slug": row.slug,
                    "path": str(row.path),
                    "reason": row.reason,
                    "age_seconds": row.age_seconds,
                    "last_regime": row.record.regime if row.record else None,
                    "last_error": row.record.last_error if row.record else None,
                }
                for row in stale_rows
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif stale_rows:
        for row in stale_rows:
            age = "n/a" if row.age_seconds is None else f"{row.age_seconds:.0f}s"
            print(f"STALE investor={row.investor_id} slug={row.slug} reason={row.reason} age={age} path={row.path}")
    else:
        print(f"OK: all live heartbeats fresh within {stale_seconds:.0f}s")

    if not stale_rows:
        return 0

    if args.dry_run:
        return 1

    bootstrap_telegram_env(repo_root)
    sent_any = False
    for row in stale_rows:
        age_text = "missing heartbeat file" if row.reason == "missing" else f"last beat {row.age_seconds:.0f}s ago"
        body_lines = [
            f"Threshold: {stale_seconds:.0f}s",
            age_text,
            f"Path: {row.path}",
        ]
        if row.record and row.record.last_error:
            body_lines.append(f"Last error: {row.record.last_error}")
        message = format_alert_message(
            title="Live bot heartbeat stale",
            body="\n".join(body_lines),
            level="critical",
            investor_id=row.investor_id,
            slug=row.slug,
            extra={
                "reason": row.reason,
                "regime": row.record.regime if row.record else None,
            },
        )
        sent = send_telegram_alert(
            message,
            event_key=f"heartbeat_stale:{row.investor_id}:{row.slug}",
            level="critical",
        )
        sent_any = sent_any or sent

    return 1 if stale_rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
