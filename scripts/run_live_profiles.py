#!/usr/bin/env python3
"""Run several Deribit live bot profiles under one supervisor.

Per profile the supervisor:

- spawns ``./bot --env-file <env> run --live`` with stdout/stderr redirected to
  ``logs/live/<investor>/<slug>.log``,
- restarts non-zero exits (``--restart-failed``) with *non-blocking* scheduling
  and per-profile exponential backoff (``--restart-delay-seconds`` base,
  ``--restart-max-delay-seconds`` cap, reset once the child has stayed up
  ``--restart-stable-seconds``),
- rotates the per-profile log by size at spawn boundaries (``--log-max-bytes`` /
  ``--log-backups``). The child owns the file descriptor while it runs, so a
  long-lived child's log is only rotated when it (re)starts — see the docs,
- watches the bot's heartbeat file; when it is older than
  ``--heartbeat-stale-seconds`` (and the child has been up at least that long)
  the child is SIGTERM'd, SIGKILL'd after ``--heartbeat-kill-grace-seconds``,
  and rescheduled with backoff (``--no-heartbeat-restart`` disables).

All timing goes through an injectable clock so the scheduling logic is unit
tested with fake processes (``tests/test_run_live_profiles.py``).
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

DEFAULT_INVESTOR_ID = "youming"
DEFAULT_RESTART_DELAY_SECONDS = 15.0
DEFAULT_RESTART_MAX_DELAY_SECONDS = 600.0
DEFAULT_RESTART_STABLE_SECONDS = 900.0
DEFAULT_LOG_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 5
DEFAULT_HEARTBEAT_KILL_GRACE_SECONDS = 30.0
HEARTBEAT_STALE_EVENT_KEY = "live_heartbeat_stale_restart"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_files_for_investor(repo_root: Path, investor_id: str) -> list[Path]:
    sys.path.insert(0, str(repo_root))
    try:
        from deribit_engine.env_layout import load_investor_manifest

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
        from deribit_engine.env_layout import account_slug_from_env_path

        slug = account_slug_from_env_path(env_file)
        if slug:
            return slug
    finally:
        if sys.path and sys.path[0] == str(repo_root):
            sys.path.pop(0)
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in env_file.name.strip("."))


def _heartbeat_path_for_env(repo_root: Path, env_file: Path) -> Path | None:
    """Heartbeat file the bot for ``env_file`` writes, or ``None`` when unresolvable.

    Mirrors the bot: ``STATE_FILE`` from the merged env layers (relative paths are
    resolved against ``repo_root``, the child's cwd), falling back to the
    canonical ``.state/investors/<id>/<slug>.json``. Returning ``None`` disables
    heartbeat supervision for that profile — never guess a path and risk
    killing a healthy bot.
    """
    sys.path.insert(0, str(repo_root))
    try:
        from deribit_engine.env_layout import (
            account_slug_from_env_path,
            default_state_file,
            investor_id_from_account_env,
        )
        from deribit_engine.live_heartbeat import heartbeat_path_for_state

        raw_state: str | None = None
        try:
            from deribit_engine.config import load_env_values

            raw_state = (load_env_values(env_file).get("STATE_FILE") or "").strip() or None
        except Exception:  # noqa: BLE001 — incomplete env layers must not break supervision.
            raw_state = None
        if raw_state:
            state_path = Path(raw_state).expanduser()
        else:
            slug = account_slug_from_env_path(env_file)
            investor_id = investor_id_from_account_env(env_file, repo_root=repo_root)
            if not slug or not investor_id:
                return None
            state_path = default_state_file(investor_id, slug)
        if not state_path.is_absolute():
            state_path = repo_root / state_path
        return heartbeat_path_for_state(state_path)
    except Exception:  # noqa: BLE001
        return None
    finally:
        if sys.path and sys.path[0] == str(repo_root):
            sys.path.pop(0)


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


# ---- log rotation --------------------------------------------------------------


def rotate_log_if_needed(log_file: Path, *, max_bytes: int, backups: int) -> bool:
    """Size-based rotation ``x.log -> x.log.1 -> ... -> x.log.N`` (oldest dropped).

    Only safe while no child holds the file open, so the supervisor calls it
    right before each spawn. Returns True when a rotation happened.
    """
    if max_bytes <= 0 or backups <= 0:
        return False
    try:
        size = log_file.stat().st_size
    except FileNotFoundError:
        return False
    if size < max_bytes:
        return False
    oldest = log_file.with_name(f"{log_file.name}.{backups}")
    try:
        oldest.unlink()
    except FileNotFoundError:
        pass
    for index in range(backups - 1, 0, -1):
        src = log_file.with_name(f"{log_file.name}.{index}")
        if src.exists():
            os.replace(src, log_file.with_name(f"{log_file.name}.{index + 1}"))
    os.replace(log_file, log_file.with_name(f"{log_file.name}.1"))
    return True


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


def _terminate_process(process: Any, grace_seconds: float) -> None:
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
        # Reap so ``poll`` reports the SIGKILL exit instead of None.
        try:
            process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


# ---- supervisor core (pure scheduling; injectable clock / process factory) ------


class _ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...


@dataclass
class SupervisorSettings:
    restart_failed: bool = False
    keep_going: bool = False
    restart_delay_seconds: float = DEFAULT_RESTART_DELAY_SECONDS
    restart_max_delay_seconds: float = DEFAULT_RESTART_MAX_DELAY_SECONDS
    restart_stable_seconds: float = DEFAULT_RESTART_STABLE_SECONDS
    log_max_bytes: int = DEFAULT_LOG_MAX_BYTES
    log_backups: int = DEFAULT_LOG_BACKUPS
    heartbeat_restart: bool = True
    heartbeat_stale_seconds: float = 600.0
    heartbeat_kill_grace_seconds: float = DEFAULT_HEARTBEAT_KILL_GRACE_SECONDS
    grace_seconds: float = 10.0


@dataclass
class ProfileRuntime:
    env_file: Path
    log_file: Path
    heartbeat_path: Path | None = None
    process: Any = None
    started_monotonic: float | None = None
    restart_at: float | None = None
    consecutive_failures: int = 0
    restart_count: int = 0
    stable_reset_done: bool = False
    heartbeat_missing_warned: bool = False
    last_exit_code: int | None = None
    finished: bool = False

    @property
    def name(self) -> str:
        return self.env_file.name

    @property
    def running(self) -> bool:
        return self.process is not None

    @property
    def pending_restart(self) -> bool:
        return self.process is None and self.restart_at is not None and not self.finished

    @property
    def active(self) -> bool:
        return self.running or self.pending_restart


def compute_restart_delay(consecutive_failures: int, *, base: float, max_delay: float) -> float:
    """``min(base * 2**(failures-1), max_delay)``; the first failure waits ``base``."""
    base = max(0.0, float(base))
    max_delay = max(base, float(max_delay))
    exponent = max(int(consecutive_failures) - 1, 0)
    try:
        delay = base * (2.0**exponent)
    except OverflowError:
        return max_delay
    return min(delay, max_delay)


class LiveProfileSupervisor:
    def __init__(
        self,
        profiles: list[ProfileRuntime],
        settings: SupervisorSettings,
        *,
        spawn: Callable[[ProfileRuntime], _ProcessLike],
        terminate: Callable[[Any, float], None],
        notify: Callable[..., None],
        clock: Callable[[], float] = time.monotonic,
        now_ms: Callable[[], int] | None = None,
        read_heartbeat: Callable[[Path], Any] | None = None,
        log: Callable[[str], None] = lambda line: print(line, flush=True),
    ) -> None:
        self.profiles = profiles
        self.settings = settings
        self._spawn = spawn
        self._terminate = terminate
        self._notify = notify
        self._clock = clock
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._read_heartbeat = read_heartbeat
        self._log = log
        self.shutting_down = False
        self.exit_code = 0

    # -- lifecycle -----------------------------------------------------------------

    def start_all(self) -> None:
        for profile in self.profiles:
            self._start(profile, reason="started")

    def _start(self, profile: ProfileRuntime, *, reason: str) -> None:
        if rotate_log_if_needed(
            profile.log_file,
            max_bytes=self.settings.log_max_bytes,
            backups=self.settings.log_backups,
        ):
            self._log(f"rotated log {profile.log_file} (>{self.settings.log_max_bytes} bytes)")
        process = self._spawn(profile)
        profile.process = process
        profile.started_monotonic = self._clock()
        profile.restart_at = None
        profile.stable_reset_done = False
        profile.heartbeat_missing_warned = False
        self._log(f"{reason} {profile.name} pid={process.pid} log={profile.log_file}")

    def request_shutdown(self) -> None:
        self.shutting_down = True

    def shutdown(self) -> None:
        for profile in self.profiles:
            if profile.process is None:
                continue
            self._log(f"stopping {profile.name} pid={profile.process.pid}")
            self._terminate(profile.process, self.settings.grace_seconds)
            code = profile.process.poll()
            if code not in (0, None) and self.exit_code == 0:
                self.exit_code = int(code)
            profile.process = None
            profile.restart_at = None

    @property
    def active(self) -> bool:
        return any(profile.active for profile in self.profiles)

    # -- one scheduler tick ----------------------------------------------------------

    def tick(self) -> bool:
        """Poll every profile once. Returns False when the loop should stop."""
        now = self._clock()
        for profile in self.profiles:
            if profile.process is not None:
                self._check_running(profile, now)
            if profile.pending_restart and not self.shutting_down and profile.restart_at is not None:
                if now >= profile.restart_at:
                    profile.restart_count += 1
                    self._start(profile, reason="restarted")
                    self._notify(
                        "Live bot restarted",
                        body=(
                            f"profile={profile.name}\n"
                            f"attempt={profile.consecutive_failures}\n"
                            f"restarts_total={profile.restart_count}\n"
                            f"log={profile.log_file}"
                        ),
                        event_key=f"bot_restart:{profile.name}",
                        level="warning",
                    )
        if self.shutting_down:
            return False
        return self.active

    def _check_running(self, profile: ProfileRuntime, now: float) -> None:
        process = profile.process
        assert process is not None
        code = process.poll()
        if code is None:
            uptime = now - (profile.started_monotonic or now)
            if (
                not profile.stable_reset_done
                and profile.consecutive_failures
                and uptime >= self.settings.restart_stable_seconds
            ):
                self._log(
                    f"{profile.name} stable for {uptime:.0f}s; resetting backoff "
                    f"(was {profile.consecutive_failures} consecutive failures)"
                )
                profile.consecutive_failures = 0
                profile.stable_reset_done = True
            if self._heartbeat_is_stale(profile, uptime):
                self._restart_for_stale_heartbeat(profile)
            return
        self._handle_exit(profile, int(code), now)

    def _handle_exit(self, profile: ProfileRuntime, code: int, now: float) -> None:
        profile.process = None
        profile.last_exit_code = code
        self._log(f"{profile.name} exited code={code} log={profile.log_file}")
        if code != 0 and self.exit_code == 0:
            self.exit_code = code
        will_restart = self.settings.restart_failed and not self.shutting_down and code != 0
        delay = 0.0
        if will_restart:
            profile.consecutive_failures += 1
            delay = compute_restart_delay(
                profile.consecutive_failures,
                base=self.settings.restart_delay_seconds,
                max_delay=self.settings.restart_max_delay_seconds,
            )
            profile.restart_at = now + delay
            self._log(
                f"restarting {profile.name} in {delay:.0f}s after exit code={code} "
                f"(attempt {profile.consecutive_failures})"
            )
        else:
            profile.finished = True
            profile.restart_at = None
        if code != 0:
            body = f"profile={profile.name}\nexit_code={code}\nlog={profile.log_file}"
            if will_restart:
                body += f"\nattempt={profile.consecutive_failures}\nnext_restart_in={delay:.0f}s"
            else:
                body += "\nrestart=no"
            self._notify(
                "Live bot exited",
                body=body,
                event_key=f"bot_exit:{profile.name}",
                level="critical",
            )
        if not will_restart and not self.settings.keep_going:
            self.shutting_down = True

    # -- heartbeat ----------------------------------------------------------------------

    def _heartbeat_is_stale(self, profile: ProfileRuntime, uptime: float) -> bool:
        if not self.settings.heartbeat_restart or not self.settings.restart_failed:
            return False
        if profile.heartbeat_path is None or self._read_heartbeat is None:
            return False
        threshold = float(self.settings.heartbeat_stale_seconds)
        if threshold <= 0 or uptime < threshold:
            return False
        record = self._read_heartbeat(profile.heartbeat_path)
        if record is None or not getattr(record, "ts_ms", 0):
            # A missing file after a full threshold is suspicious but may also be a
            # STATE_FILE mismatch; warn once instead of killing a possibly healthy bot.
            if not profile.heartbeat_missing_warned:
                self._log(
                    f"{profile.name} heartbeat missing at {profile.heartbeat_path} after {uptime:.0f}s uptime; "
                    "not restarting (only an expired heartbeat triggers restart)"
                )
                profile.heartbeat_missing_warned = True
            return False
        age_seconds = (self._now_ms() - int(record.ts_ms)) / 1000.0
        return age_seconds > threshold

    def _restart_for_stale_heartbeat(self, profile: ProfileRuntime) -> None:
        process = profile.process
        assert process is not None
        record = self._read_heartbeat(profile.heartbeat_path) if self._read_heartbeat else None
        age = (self._now_ms() - int(record.ts_ms)) / 1000.0 if record is not None else float("nan")
        self._log(
            f"{profile.name} heartbeat stale ({age:.0f}s > {self.settings.heartbeat_stale_seconds:.0f}s); "
            f"terminating pid={process.pid}"
        )
        self._terminate(process, self.settings.heartbeat_kill_grace_seconds)
        code = process.poll()
        if code is None:
            # Could not confirm death; keep tracking rather than double-spawn.
            self._log(f"{profile.name} pid={process.pid} still alive after SIGKILL; will retry next tick")
            return
        profile.process = None
        profile.last_exit_code = int(code)
        profile.consecutive_failures += 1
        delay = compute_restart_delay(
            profile.consecutive_failures,
            base=self.settings.restart_delay_seconds,
            max_delay=self.settings.restart_max_delay_seconds,
        )
        profile.restart_at = self._clock() + delay
        self._log(
            f"restarting {profile.name} in {delay:.0f}s after stale heartbeat (attempt {profile.consecutive_failures})"
        )
        body_lines = [
            f"profile={profile.name}",
            f"heartbeat_age={age:.0f}s",
            f"threshold={self.settings.heartbeat_stale_seconds:.0f}s",
            f"exit_code={code}",
            f"attempt={profile.consecutive_failures}",
            f"next_restart_in={delay:.0f}s",
            f"heartbeat={profile.heartbeat_path}",
            f"log={profile.log_file}",
        ]
        last_error = getattr(record, "last_error", None)
        if last_error:
            body_lines.append(f"last_error={last_error}")
        self._notify(
            "Live bot heartbeat stale; restarting",
            body="\n".join(body_lines),
            event_key=f"{HEARTBEAT_STALE_EVENT_KEY}:{profile.name}",
            level="critical",
        )

    # -- blocking loop -----------------------------------------------------------------

    def run(self, *, sleep: Callable[[float], None] = time.sleep, tick_seconds: float = 1.0) -> int:
        self.start_all()
        try:
            while True:
                if not self.tick():
                    break
                sleep(tick_seconds)
        finally:
            self.shutdown()
        return self.exit_code


# ---- CLI -----------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
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
        default=DEFAULT_RESTART_DELAY_SECONDS,
        help="Base delay before restarting a failed profile (doubles per consecutive failure).",
    )
    parser.add_argument(
        "--restart-max-delay-seconds",
        type=float,
        default=DEFAULT_RESTART_MAX_DELAY_SECONDS,
        help="Upper bound for the exponential restart backoff.",
    )
    parser.add_argument(
        "--restart-stable-seconds",
        type=float,
        default=DEFAULT_RESTART_STABLE_SECONDS,
        help="Reset a profile's backoff once its child has stayed up this long.",
    )
    parser.add_argument(
        "--log-max-bytes",
        type=int,
        default=DEFAULT_LOG_MAX_BYTES,
        help="Rotate a profile log (at spawn time) once it exceeds this size; 0 disables.",
    )
    parser.add_argument(
        "--log-backups",
        type=int,
        default=DEFAULT_LOG_BACKUPS,
        help="How many rotated logs (<slug>.log.1..N) to keep.",
    )
    parser.add_argument(
        "--heartbeat-stale-seconds",
        type=float,
        default=None,
        help="Restart a child whose heartbeat is older than this (default: LIVE_HEARTBEAT_STALE_SECONDS or 600).",
    )
    parser.add_argument(
        "--heartbeat-kill-grace-seconds",
        type=float,
        default=DEFAULT_HEARTBEAT_KILL_GRACE_SECONDS,
        help="After SIGTERM for a stale heartbeat, wait this long before SIGKILL.",
    )
    parser.add_argument(
        "--no-heartbeat-restart",
        action="store_true",
        help="Do not restart children on stale heartbeat (monitoring stays with check_live_heartbeat.py).",
    )
    parser.add_argument(
        "--grace-seconds",
        type=float,
        default=10.0,
        help="Seconds to wait before force-killing processes on shutdown.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.restart_failed:
        args.keep_going = True

    repo_root = _repo_root()
    sys.path.insert(0, str(repo_root))
    try:
        from deribit_engine.live_heartbeat import read_live_heartbeat, stale_seconds_from_environ
        from deribit_engine.telegram_alerts import bootstrap_telegram_env

        bootstrap_telegram_env(repo_root)
    finally:
        if sys.path and sys.path[0] == str(repo_root):
            sys.path.pop(0)

    def _notify_live_supervisor(title: str, *, body: str, event_key: str, level: str = "warning") -> None:
        try:
            from deribit_engine.telegram_alerts import format_alert_message, send_telegram_alert

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
            from deribit_engine.env_layout import (
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

    heartbeat_stale = (
        float(args.heartbeat_stale_seconds)
        if args.heartbeat_stale_seconds is not None
        else stale_seconds_from_environ()
    )
    settings = SupervisorSettings(
        restart_failed=bool(args.restart_failed),
        keep_going=bool(args.keep_going),
        restart_delay_seconds=max(0.0, float(args.restart_delay_seconds)),
        restart_max_delay_seconds=max(0.0, float(args.restart_max_delay_seconds)),
        restart_stable_seconds=max(0.0, float(args.restart_stable_seconds)),
        log_max_bytes=max(0, int(args.log_max_bytes)),
        log_backups=max(0, int(args.log_backups)),
        heartbeat_restart=not args.no_heartbeat_restart,
        heartbeat_stale_seconds=heartbeat_stale,
        heartbeat_kill_grace_seconds=max(0.0, float(args.heartbeat_kill_grace_seconds)),
        grace_seconds=max(0.0, float(args.grace_seconds)),
    )

    profiles: list[ProfileRuntime] = []
    for env_file in env_files:
        log_file = log_dir / f"{_safe_log_name(env_file, repo_root)}.log"
        heartbeat_path = _heartbeat_path_for_env(repo_root, env_file) if settings.heartbeat_restart else None
        if settings.heartbeat_restart and heartbeat_path is None:
            print(f"heartbeat supervision disabled for {env_file.name}: cannot resolve STATE_FILE", flush=True)
        profiles.append(ProfileRuntime(env_file=env_file, log_file=log_file, heartbeat_path=heartbeat_path))

    started_at = datetime.now(tz=UTC).isoformat()

    def _spawn(profile: ProfileRuntime) -> subprocess.Popen[bytes]:
        return _spawn_profile(
            args=args,
            repo_root=repo_root,
            env_file=profile.env_file,
            log_file=profile.log_file,
            started_at=started_at if profile.restart_count == 0 else None,
        )

    supervisor = LiveProfileSupervisor(
        profiles,
        settings,
        spawn=_spawn,
        terminate=_terminate_process,
        notify=_notify_live_supervisor,
        read_heartbeat=read_live_heartbeat,
    )

    def request_shutdown(signum: int, _frame: object) -> None:
        if supervisor.shutting_down:
            return
        supervisor.request_shutdown()
        print(f"\nReceived signal {signum}; stopping live profiles...", flush=True)

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    if settings.heartbeat_restart and settings.restart_failed:
        print(
            f"heartbeat supervision on: stale>{settings.heartbeat_stale_seconds:.0f}s → SIGTERM, "
            f"SIGKILL after {settings.heartbeat_kill_grace_seconds:.0f}s, then backoff restart",
            flush=True,
        )
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
