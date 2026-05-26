#!/usr/bin/env python3
"""Minimal dashboard HTTP server for Playwright smoke tests."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _build_app(env_file: Path):
    import deribit_engine.frontend_server as frontend_server
    from tests.conftest import make_config

    cfg = make_config(env_file.parent, state_file=env_file.parent / "e2e.state.json")
    fake_status = {"portfolio": {"total_equity_usdc": "1000"}, "trade_groups": []}
    fake_groups = {"open": [], "closed": [], "underlying_index_usd": {}}
    fake_summary = {"summary": {"realized_pnl_usdc": "50"}, "recent_closed_trades": []}

    frontend_server.load_config = lambda _path, require_private=False: cfg  # type: ignore[method-assign]
    frontend_server._aggregate_status = lambda *_a, **_k: fake_status  # type: ignore[method-assign]
    frontend_server._aggregate_groups = lambda *_a, **_k: fake_groups  # type: ignore[method-assign]
    frontend_server._aggregate_realized_summary = lambda *_a, **_k: fake_summary  # type: ignore[method-assign]
    frontend_server._latest_ledger_snapshot = lambda *_a, **_k: None  # type: ignore[method-assign]

    return frontend_server.create_app(
        env_file=env_file,
        account_env_files=(env_file,),
        enable_scheduler=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dashboard for Playwright e2e tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="deribit-e2e-"))
    env_file = tmp / ".env.e2e"
    env_file.write_text(
        "\n".join(
            [
                "DERIBIT_ENV=testnet",
                "DERIBIT_CLIENT_ID=e2e",
                "DERIBIT_CLIENT_SECRET=e2e",
                "",
            ]
        ),
        encoding="utf-8",
    )

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("uvicorn not installed; pip install -r requirements.txt") from exc

    app = _build_app(env_file)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
