#!/usr/bin/env python3
"""Check investor frontend health endpoints and alert when unavailable."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HEALTH_ENDPOINT = "/api/health"
DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_LOCAL_HOST = "127.0.0.1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repo_on_path(repo_root: Path) -> None:
    root = str(repo_root)
    if root not in sys.path:
        sys.path.insert(0, root)


@dataclass(frozen=True)
class FrontendTarget:
    investor_id: str
    display_name: str
    hostname: str | None
    frontend_port: int | None
    url: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "investor_id": self.investor_id,
            "display_name": self.display_name,
            "hostname": self.hostname,
            "frontend_port": self.frontend_port,
            "url": self.url,
        }


@dataclass(frozen=True)
class FrontendCheckResult:
    target: FrontendTarget
    ok: bool
    status_code: int | None
    elapsed_ms: float | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.target.to_dict(),
            "ok": self.ok,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


def _target_url(frontend_port: int | None, *, local_host: str) -> str | None:
    if frontend_port is None:
        return None
    return f"http://{local_host}:{frontend_port}{HEALTH_ENDPOINT}"


def load_frontend_targets(
    repo_root: Path,
    *,
    investor_id: str | None = None,
    local_host: str = DEFAULT_LOCAL_HOST,
) -> tuple[FrontendTarget, ...]:
    _ensure_repo_on_path(repo_root)
    from deribit_engine.exceptions import ConfigurationError
    from deribit_engine.investor_registry import load_platform_registry, validate_investor_id

    registry = load_platform_registry(repo_root=repo_root)
    if investor_id:
        normalized = validate_investor_id(investor_id)
        entry = registry.entry_for(normalized)
        if entry is None:
            raise ConfigurationError(f"Investor {normalized!r} not found in registry.toml")
        entries = (entry,) if entry.frontend_enabled else ()
    else:
        entries = tuple(entry for entry in registry.investors if entry.frontend_enabled)

    return tuple(
        FrontendTarget(
            investor_id=entry.investor_id,
            display_name=entry.display_name,
            hostname=entry.hostname,
            frontend_port=entry.frontend_port,
            url=_target_url(entry.frontend_port, local_host=local_host),
        )
        for entry in entries
    )


def check_frontend(target: FrontendTarget, *, timeout_seconds: float) -> FrontendCheckResult:
    if target.url is None:
        return FrontendCheckResult(
            target=target,
            ok=False,
            status_code=None,
            elapsed_ms=None,
            error="missing frontend_port",
        )

    request = urllib.request.Request(
        target.url,
        headers={"User-Agent": "deribit-frontend-uptime/1.0"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", response.getcode()))
            response.read(512)
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        return FrontendCheckResult(
            target=target,
            ok=False,
            status_code=int(exc.code),
            elapsed_ms=elapsed_ms,
            error=f"HTTP {exc.code}",
        )
    except (OSError, ValueError) as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        reason = getattr(exc, "reason", None)
        error = str(reason or exc)
        return FrontendCheckResult(
            target=target,
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            error=error,
        )

    elapsed_ms = (time.monotonic() - started) * 1000
    ok = 200 <= status_code < 300
    return FrontendCheckResult(
        target=target,
        ok=ok,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        error=None if ok else f"HTTP {status_code}",
    )


def check_frontends(targets: tuple[FrontendTarget, ...], *, timeout_seconds: float) -> list[FrontendCheckResult]:
    return [check_frontend(target, timeout_seconds=timeout_seconds) for target in targets]


def _format_elapsed(elapsed_ms: float | None) -> str:
    return "n/a" if elapsed_ms is None else f"{elapsed_ms:.0f}ms"


def _print_human(results: list[FrontendCheckResult]) -> None:
    if not results:
        print("No frontend_enabled investors found in registry.toml")
        return
    for result in results:
        target = result.target
        if result.ok:
            print(
                "OK "
                f"investor={target.investor_id} "
                f"url={target.url} "
                f"status={result.status_code} "
                f"latency={_format_elapsed(result.elapsed_ms)}"
            )
        else:
            print(
                "FAIL "
                f"investor={target.investor_id} "
                f"url={target.url or 'n/a'} "
                f"status={result.status_code or 'n/a'} "
                f"latency={_format_elapsed(result.elapsed_ms)} "
                f"error={result.error}"
            )


def _print_json(results: list[FrontendCheckResult], *, timeout_seconds: float) -> None:
    failures = [result for result in results if not result.ok]
    payload = {
        "endpoint": HEALTH_ENDPOINT,
        "timeout_seconds": timeout_seconds,
        "ok_count": len(results) - len(failures),
        "failure_count": len(failures),
        "checks": [result.to_dict() for result in results],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def send_failure_alerts(repo_root: Path, failures: list[FrontendCheckResult]) -> bool:
    _ensure_repo_on_path(repo_root)
    from deribit_engine.telegram_alerts import bootstrap_telegram_env, format_alert_message, send_telegram_alert

    bootstrap_telegram_env(repo_root)
    sent_any = False
    for result in failures:
        target = result.target
        body_lines = [
            f"Endpoint: {HEALTH_ENDPOINT}",
            f"URL: {target.url or 'n/a'}",
            f"HTTP status: {result.status_code or 'n/a'}",
            f"Error: {result.error or 'unknown'}",
        ]
        if target.hostname:
            body_lines.append(f"Registry hostname: {target.hostname}")
        if target.frontend_port is not None:
            body_lines.append(f"Registry port: {target.frontend_port}")
        message = format_alert_message(
            title="Frontend uptime check failed",
            body="\n".join(body_lines),
            level="critical",
            investor_id=target.investor_id,
            extra={"latency_ms": _format_elapsed(result.elapsed_ms)},
        )
        sent = send_telegram_alert(
            message,
            event_key=f"frontend_uptime:{target.investor_id}",
            level="critical",
        )
        sent_any = sent_any or sent
    return sent_any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alert when investor frontend /api/health checks fail.")
    parser.add_argument("--investor", help="Only check this investor id")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable lines")
    parser.add_argument("--dry-run", action="store_true", help="Report failures without sending Telegram")
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    timeout_seconds = max(float(args.timeout), 0.1)

    try:
        targets = load_frontend_targets(repo_root, investor_id=args.investor)
    except Exception as exc:  # noqa: BLE001 - CLI should render config errors cleanly.
        print(str(exc), file=sys.stderr)
        return 2

    results = check_frontends(targets, timeout_seconds=timeout_seconds)
    if args.json:
        _print_json(results, timeout_seconds=timeout_seconds)
    else:
        _print_human(results)

    failures = [result for result in results if not result.ok]
    if not results:
        return 2
    if not failures:
        return 0
    if args.dry_run:
        return 1
    send_failure_alerts(repo_root, failures)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
