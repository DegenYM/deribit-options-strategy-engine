#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_INVESTOR_ID = "youming"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_files_for_investor(repo_root: Path, investor_id: str) -> list[Path]:
    sys.path.insert(0, str(repo_root))
    try:
        from deribit_demo.env_layout import load_investor_manifest

        return list(
            load_investor_manifest(investor_id, repo_root=repo_root).account_env_files(
                require_creds=True,
                require_live=True,
            )
        )
    finally:
        if sys.path and sys.path[0] == str(repo_root):
            sys.path.pop(0)


def _resolve_existing_env_files(repo_root: Path, raw_env_files: list[str]) -> list[Path]:
    env_files: list[Path] = []
    missing: list[str] = []
    for raw in raw_env_files:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = repo_root / path
        if not path.exists():
            missing.append(raw)
            continue
        env_files.append(path)
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing env file(s): {joined}")
    if not env_files:
        raise SystemExit("No env files provided.")
    return env_files


def _safe_log_name(env_file: Path, repo_root: Path) -> str:
    sys.path.insert(0, str(repo_root))
    try:
        from deribit_demo.env_layout import account_slug_from_env_path

        slug = account_slug_from_env_path(env_file)
        if slug:
            return slug
    finally:
        if sys.path and sys.path[0] == str(repo_root):
            sys.path.pop(0)
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in env_file.name.strip("."))


def _build_command(args: argparse.Namespace, repo_root: Path, env_file: Path) -> list[str]:
    bot_path = Path(args.bot).expanduser()
    if not bot_path.is_absolute():
        bot_path = repo_root / bot_path
    command = [
        sys.executable,
        str(bot_path),
        "--env-file",
        str(env_file),
        "run",
        "--cycles",
        str(args.cycles),
        "--live",
    ]
    if args.currencies:
        command.extend(["--currencies", args.currencies])
    if args.json:
        command.append("--json")
    return command


def _spawn_profile(
    *,
    args: argparse.Namespace,
    repo_root: Path,
    env_file: Path,
    log_file: Path,
    started_at: str | None = None,
) -> subprocess.Popen[bytes]:
    command = _build_command(args, repo_root, env_file)
    stamp = started_at or datetime.now(tz=UTC).isoformat()
    with log_file.open("ab", buffering=0) as log:
        log.write(f"\n--- started {stamp} ---\n".encode())
        log.write(("command: " + " ".join(command) + "\n").encode())
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return process


def _terminate_process(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.2)

    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            process.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run multiple Deribit live bot profiles at once.",
    )
    parser.add_argument(
        "env_files",
        nargs="*",
        default=None,
        help="Account env files. Omit with --investor to use accounts.toml.",
    )
    parser.add_argument(
        "--investor",
        metavar="ID",
        default=None,
        help=f"Load enabled accounts from config/investors/<ID>/accounts.toml (default id: {DEFAULT_INVESTOR_ID})",
    )
    parser.add_argument("--cycles", type=int, default=0, help="Cycles per profile; 0 means forever.")
    parser.add_argument("--currencies", help="Comma-separated currencies passed to each run, e.g. BTC,ETH.")
    parser.add_argument("--bot", default="./bot", help="Bot entrypoint path.")
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Directory for per-profile logs (default: logs/live/<investor_id> when using --investor).",
    )
    parser.add_argument("--json", action="store_true", help="Pass --json to each bot process.")
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Keep other profiles running if one profile exits.",
    )
    parser.add_argument(
        "--restart-failed",
        action="store_true",
        help="Restart profiles that exit with a non-zero code (implies --keep-going).",
    )
    parser.add_argument(
        "--restart-delay-seconds",
        type=float,
        default=15.0,
        help="Wait before restarting a failed profile when --restart-failed is set.",
    )
    parser.add_argument(
        "--grace-seconds",
        type=float,
        default=10.0,
        help="Seconds to wait before force-killing processes on shutdown.",
    )
    args = parser.parse_args(argv)
    if args.restart_failed:
        args.keep_going = True

    repo_root = _repo_root()
    sys.path.insert(0, str(repo_root))
    try:
        from deribit_demo.telegram_alerts import bootstrap_telegram_env

        bootstrap_telegram_env(repo_root)
    finally:
        if sys.path and sys.path[0] == str(repo_root):
            sys.path.pop(0)

    def _notify_live_supervisor(title: str, *, body: str, event_key: str, level: str = "warning") -> None:
        try:
            from deribit_demo.telegram_alerts import format_alert_message, send_telegram_alert

            investor_id = args.investor or DEFAULT_INVESTOR_ID
            message = format_alert_message(
                title=title,
                body=body,
                level=level,
                investor_id=investor_id if args.investor or not args.env_files else None,
            )
            send_telegram_alert(message, event_key=event_key, level=level)
        except Exception as exc:
            print(f"telegram alert skipped: {exc}", flush=True)

    if args.env_files:
        env_files = _resolve_existing_env_files(repo_root, args.env_files)
    else:
        investor_id = args.investor or DEFAULT_INVESTOR_ID
        try:
            env_files = _env_files_for_investor(repo_root, investor_id)
        except Exception as exc:
            if args.investor:
                raise SystemExit(str(exc)) from exc
            env_files = []
        if env_files:
            missing = [path for path in env_files if not path.is_file()]
            if missing:
                joined = ", ".join(str(path) for path in missing)
                raise SystemExit(f"Missing account env file(s) for investor {investor_id!r}: {joined}")
        else:
            raise SystemExit(
                f"No live-enabled accounts for investor {investor_id!r}; "
                f"check enabled/live_enabled and API creds in "
                f"{repo_root / 'config/investors' / investor_id / 'accounts.toml'}"
            )
    if args.log_dir:
        log_dir = Path(args.log_dir).expanduser()
        if not log_dir.is_absolute():
            log_dir = repo_root / log_dir
    else:
        sys.path.insert(0, str(repo_root))
        try:
            from deribit_demo.env_layout import (
                investor_live_log_dir,
                load_investor_manifest,
                resolve_investor_scope,
            )

            scoped_investor: str | None = None
            if args.investor or (not args.env_files):
                manifest = load_investor_manifest(args.investor or DEFAULT_INVESTOR_ID, repo_root=repo_root)
                scoped_investor = manifest.investor_id
            elif env_files:
                scoped_investor = resolve_investor_scope(env_files, repo_root=repo_root)
            if scoped_investor:
                log_dir = investor_live_log_dir(repo_root, scoped_investor)
            else:
                log_dir = repo_root / "logs" / "live"
        finally:
            if sys.path and sys.path[0] == str(repo_root):
                sys.path.pop(0)
    log_dir.mkdir(parents=True, exist_ok=True)

    processes: dict[subprocess.Popen[bytes], tuple[Path, Path]] = {}
    shutting_down = False

    def request_shutdown(signum: int, _frame: object) -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        print(f"\nReceived signal {signum}; stopping live profiles...", flush=True)

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    started_at = datetime.now(tz=UTC).isoformat()
    for env_file in env_files:
        log_file = log_dir / f"{_safe_log_name(env_file, repo_root)}.log"
        process = _spawn_profile(
            args=args,
            repo_root=repo_root,
            env_file=env_file,
            log_file=log_file,
            started_at=started_at,
        )
        processes[process] = (env_file, log_file)
        print(f"started {env_file.name} pid={process.pid} log={log_file}", flush=True)

    exit_code = 0
    try:
        while processes:
            for process, (env_file, log_file) in list(processes.items()):
                code = process.poll()
                if code is None:
                    continue
                del processes[process]
                print(f"{env_file.name} exited code={code} log={log_file}", flush=True)
                if code != 0:
                    _notify_live_supervisor(
                        "Live bot exited",
                        body=f"profile={env_file.name}\nexit_code={code}\nlog={log_file}",
                        event_key=f"bot_exit:{env_file.name}",
                        level="critical",
                    )
                if code != 0 and exit_code == 0:
                    exit_code = code
                if args.restart_failed and not shutting_down and code not in (0, None):
                    delay = max(0.0, float(args.restart_delay_seconds))
                    if delay:
                        print(
                            f"restarting {env_file.name} in {delay:.0f}s after exit code={code}",
                            flush=True,
                        )
                        time.sleep(delay)
                    if shutting_down:
                        continue
                    process = _spawn_profile(
                        args=args,
                        repo_root=repo_root,
                        env_file=env_file,
                        log_file=log_file,
                    )
                    processes[process] = (env_file, log_file)
                    print(f"restarted {env_file.name} pid={process.pid} log={log_file}", flush=True)
                    _notify_live_supervisor(
                        "Live bot restarted",
                        body=f"profile={env_file.name}\nlog={log_file}",
                        event_key=f"bot_restart:{env_file.name}",
                        level="warning",
                    )
                elif not args.keep_going:
                    shutting_down = True

            if shutting_down:
                break
            time.sleep(1)
    finally:
        for process, (env_file, _log_file) in list(processes.items()):
            print(f"stopping {env_file.name} pid={process.pid}", flush=True)
            _terminate_process(process, args.grace_seconds)
        for process in list(processes):
            code = process.poll()
            if code not in (0, None) and exit_code == 0:
                exit_code = code

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
