"""Scheduling / backoff / rotation / heartbeat logic of scripts/run_live_profiles.py."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_live_profiles.py"
_spec = importlib.util.spec_from_file_location("run_live_profiles", _SCRIPT)
assert _spec and _spec.loader
rlp = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_live_profiles", rlp)
_spec.loader.exec_module(rlp)


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def now_ms(self) -> int:
        return int(self.t * 1000)


class FakeProcess:
    _next_pid = 100

    def __init__(self) -> None:
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self.code: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.code

    def exit(self, code: int) -> None:
        self.code = code


@dataclass
class Harness:
    clock: FakeClock
    supervisor: rlp.LiveProfileSupervisor
    profiles: list[rlp.ProfileRuntime]
    spawned: list[FakeProcess] = field(default_factory=list)
    notifications: list[dict] = field(default_factory=list)
    terminated: list[tuple[int, float]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    heartbeats: dict[Path, object] = field(default_factory=dict)

    @property
    def current(self) -> FakeProcess:
        return self.spawned[-1]


def _harness(tmp_path: Path, *, profiles: int = 1, **settings_overrides) -> Harness:
    clock = FakeClock()
    runtime_profiles = [
        rlp.ProfileRuntime(
            env_file=tmp_path / f".env.p{i}",
            log_file=tmp_path / f"p{i}.log",
            heartbeat_path=tmp_path / f"p{i}.heartbeat.json",
        )
        for i in range(profiles)
    ]
    settings = rlp.SupervisorSettings(
        restart_failed=True,
        keep_going=True,
        restart_delay_seconds=10.0,
        restart_max_delay_seconds=100.0,
        restart_stable_seconds=300.0,
        heartbeat_stale_seconds=60.0,
        heartbeat_kill_grace_seconds=5.0,
        log_max_bytes=0,
    )
    for key, value in settings_overrides.items():
        setattr(settings, key, value)
    h = Harness(clock=clock, supervisor=None, profiles=runtime_profiles)  # type: ignore[arg-type]

    def spawn(profile):
        proc = FakeProcess()
        h.spawned.append(proc)
        return proc

    def terminate(process, grace):
        h.terminated.append((process.pid, grace))
        process.terminated = True
        process.code = -15

    def notify(title, *, body, event_key, level="warning"):
        h.notifications.append({"title": title, "body": body, "event_key": event_key, "level": level})

    def read_heartbeat(path):
        return h.heartbeats.get(path)

    h.supervisor = rlp.LiveProfileSupervisor(
        runtime_profiles,
        settings,
        spawn=spawn,
        terminate=terminate,
        notify=notify,
        clock=clock,
        now_ms=clock.now_ms,
        read_heartbeat=read_heartbeat,
        log=h.logs.append,
    )
    return h


# ---- backoff math ---------------------------------------------------------------


def test_compute_restart_delay_doubles_and_caps() -> None:
    delays = [rlp.compute_restart_delay(n, base=15, max_delay=600) for n in range(1, 9)]
    assert delays == [15, 30, 60, 120, 240, 480, 600, 600]
    assert rlp.compute_restart_delay(0, base=15, max_delay=600) == 15
    assert rlp.compute_restart_delay(5, base=0, max_delay=600) == 0
    assert rlp.compute_restart_delay(3, base=50, max_delay=10) == 50  # cap never below base
    assert rlp.compute_restart_delay(10_000, base=15, max_delay=600) == 600


# ---- non-blocking restart scheduling -------------------------------------------------


def test_restart_is_scheduled_not_blocking_and_uses_backoff(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    sup = h.supervisor
    sup.start_all()
    assert len(h.spawned) == 1
    p = h.profiles[0]

    h.current.exit(3)
    assert sup.tick() is True  # still active (pending restart)
    assert p.process is None and p.pending_restart
    assert p.consecutive_failures == 1
    assert p.restart_at == pytest.approx(h.clock.t + 10)
    assert len(h.spawned) == 1, "must not restart synchronously"
    exit_alert = h.notifications[-1]
    assert exit_alert["event_key"] == "bot_exit:.env.p0"
    assert "attempt=1" in exit_alert["body"] and "next_restart_in=10s" in exit_alert["body"]

    h.clock.advance(9)
    sup.tick()
    assert len(h.spawned) == 1
    h.clock.advance(1.5)
    sup.tick()
    assert len(h.spawned) == 2
    assert h.notifications[-1]["event_key"] == "bot_restart:.env.p0"
    assert "attempt=1" in h.notifications[-1]["body"]

    # Second failure → 20s, third → 40s, ..., capped at 100.
    expected = [20, 40, 80, 100, 100]
    for want in expected:
        h.current.exit(1)
        sup.tick()
        assert p.restart_at - h.clock.t == pytest.approx(want)
        h.clock.advance(want)
        sup.tick()
        assert p.process is not None
    assert p.consecutive_failures == 6
    assert sup.exit_code == 3  # first non-zero code is remembered


def test_backoff_resets_after_stable_uptime(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    sup = h.supervisor
    sup.start_all()
    p = h.profiles[0]
    for _ in range(3):
        h.current.exit(1)
        sup.tick()
        h.clock.advance(1_000)
        sup.tick()
    assert p.consecutive_failures == 3

    h.clock.advance(299)
    sup.tick()
    assert p.consecutive_failures == 3
    h.clock.advance(2)
    sup.tick()
    assert p.consecutive_failures == 0
    assert any("resetting backoff" in line for line in h.logs)

    h.current.exit(1)
    sup.tick()
    assert p.restart_at - h.clock.t == pytest.approx(10)  # back to base


def test_other_profiles_keep_ticking_while_one_waits(tmp_path: Path) -> None:
    h = _harness(tmp_path, profiles=2)
    sup = h.supervisor
    sup.start_all()
    first, second = h.spawned
    first.exit(2)
    sup.tick()
    # Second profile exits while first is waiting: it gets its own schedule.
    h.clock.advance(3)
    second.exit(5)
    sup.tick()
    assert h.profiles[0].restart_at == pytest.approx(1_010)
    assert h.profiles[1].restart_at == pytest.approx(1_013)
    h.clock.advance(7.1)
    sup.tick()
    assert h.profiles[0].process is not None and h.profiles[1].process is None
    h.clock.advance(3)
    sup.tick()
    assert h.profiles[1].process is not None


def test_zero_exit_finishes_profile_without_restart(tmp_path: Path) -> None:
    h = _harness(tmp_path, profiles=2)
    sup = h.supervisor
    sup.start_all()
    h.spawned[0].exit(0)
    assert sup.tick() is True
    assert h.profiles[0].finished and not h.profiles[0].active
    assert h.notifications == []  # clean exit → no alert
    h.spawned[1].exit(0)
    assert sup.tick() is False  # nothing active → stop
    assert sup.exit_code == 0


def test_without_keep_going_any_exit_stops_supervisor(tmp_path: Path) -> None:
    h = _harness(tmp_path, profiles=2, restart_failed=False, keep_going=False)
    sup = h.supervisor
    sup.start_all()
    h.spawned[0].exit(4)
    assert sup.tick() is False
    assert sup.shutting_down is True
    assert "restart=no" in h.notifications[-1]["body"]
    sup.shutdown()
    assert [pid for pid, _ in h.terminated] == [h.spawned[1].pid]
    assert sup.exit_code == 4


def test_shutdown_request_cancels_pending_restart(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    sup = h.supervisor
    sup.start_all()
    h.current.exit(1)
    sup.tick()
    sup.request_shutdown()
    h.clock.advance(100)
    assert sup.tick() is False
    assert len(h.spawned) == 1
    sup.shutdown()
    assert h.terminated == []


def test_run_loop_uses_injected_sleep(tmp_path: Path) -> None:
    h = _harness(tmp_path, restart_failed=False, keep_going=False)
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        h.clock.advance(seconds)
        if len(sleeps) == 3:
            h.current.exit(0)

    assert h.supervisor.run(sleep=sleep, tick_seconds=1.0) == 0
    assert sleeps == [1.0, 1.0, 1.0]


# ---- heartbeat ------------------------------------------------------------------------


def _beat(ts_ms: int, last_error: str | None = None):
    return SimpleNamespace(ts_ms=ts_ms, last_error=last_error, cycle=1, regime="normal")


def test_stale_heartbeat_terminates_and_schedules_restart(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    sup = h.supervisor
    sup.start_all()
    p = h.profiles[0]
    h.heartbeats[p.heartbeat_path] = _beat(h.clock.now_ms())

    # Before the child has been up for a full threshold, staleness is ignored.
    h.clock.advance(59)
    sup.tick()
    assert p.process is not None and h.terminated == []

    # Heartbeat now 61s old and uptime 61s → SIGTERM (grace 5s), restart with backoff.
    h.clock.advance(2)
    sup.tick()
    assert h.terminated == [(h.spawned[0].pid, 5.0)]
    assert p.process is None and p.pending_restart
    assert p.consecutive_failures == 1
    assert p.restart_at == pytest.approx(h.clock.t + 10)
    alert = h.notifications[-1]
    assert alert["event_key"] == "live_heartbeat_stale_restart:.env.p0"
    assert alert["level"] == "critical"
    assert "heartbeat_age=61s" in alert["body"] and "next_restart_in=10s" in alert["body"]

    h.clock.advance(10)
    sup.tick()
    assert len(h.spawned) == 2
    # Fresh child: old heartbeat still on disk, but uptime < threshold → no kill.
    h.clock.advance(30)
    sup.tick()
    assert len(h.terminated) == 1


def test_fresh_heartbeat_is_not_restarted(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    sup = h.supervisor
    sup.start_all()
    p = h.profiles[0]
    for _ in range(10):
        h.clock.advance(30)
        h.heartbeats[p.heartbeat_path] = _beat(h.clock.now_ms())
        sup.tick()
    assert h.terminated == [] and len(h.spawned) == 1


def test_missing_heartbeat_only_warns(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    sup = h.supervisor
    sup.start_all()
    h.clock.advance(120)
    sup.tick()
    sup.tick()
    assert h.terminated == []
    warnings = [line for line in h.logs if "heartbeat missing" in line]
    assert len(warnings) == 1


def test_heartbeat_restart_disabled_by_flag_or_without_restart_failed(tmp_path: Path) -> None:
    for overrides in ({"heartbeat_restart": False}, {"restart_failed": False}):
        h = _harness(tmp_path, **overrides)
        sup = h.supervisor
        sup.start_all()
        p = h.profiles[0]
        h.heartbeats[p.heartbeat_path] = _beat(h.clock.now_ms() - 10_000_000)
        h.clock.advance(500)
        sup.tick()
        assert h.terminated == []
        assert p.process is not None


def test_profile_without_heartbeat_path_is_never_killed(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    h.profiles[0].heartbeat_path = None
    sup = h.supervisor
    sup.start_all()
    h.clock.advance(5_000)
    sup.tick()
    assert h.terminated == []


# ---- log rotation --------------------------------------------------------------------


def test_rotate_log_if_needed_shifts_backups(tmp_path: Path) -> None:
    log = tmp_path / "naked.log"
    log.write_bytes(b"x" * 100)
    assert rlp.rotate_log_if_needed(log, max_bytes=1_000, backups=2) is False
    assert rlp.rotate_log_if_needed(log, max_bytes=100, backups=2) is True
    assert not log.exists()
    assert (tmp_path / "naked.log.1").read_bytes() == b"x" * 100

    log.write_bytes(b"y" * 100)
    assert rlp.rotate_log_if_needed(log, max_bytes=100, backups=2) is True
    assert (tmp_path / "naked.log.1").read_bytes() == b"y" * 100
    assert (tmp_path / "naked.log.2").read_bytes() == b"x" * 100

    log.write_bytes(b"z" * 100)
    assert rlp.rotate_log_if_needed(log, max_bytes=100, backups=2) is True
    assert (tmp_path / "naked.log.1").read_bytes() == b"z" * 100
    assert (tmp_path / "naked.log.2").read_bytes() == b"y" * 100
    assert not (tmp_path / "naked.log.3").exists()  # oldest dropped
    assert rlp.rotate_log_if_needed(tmp_path / "missing.log", max_bytes=1, backups=1) is False
    assert rlp.rotate_log_if_needed(log, max_bytes=0, backups=2) is False


def test_supervisor_rotates_at_spawn_boundaries(tmp_path: Path) -> None:
    h = _harness(tmp_path, log_max_bytes=10, log_backups=3)
    log = h.profiles[0].log_file
    log.write_bytes(b"a" * 20)
    sup = h.supervisor
    sup.start_all()
    assert (tmp_path / "p0.log.1").read_bytes() == b"a" * 20
    assert any("rotated log" in line for line in h.logs)
    # Child "writes" while running; not rotated mid-run.
    log.write_bytes(b"b" * 30)
    h.clock.advance(5)
    sup.tick()
    assert log.read_bytes() == b"b" * 30
    # Rotates again at restart.
    h.current.exit(1)
    sup.tick()
    h.clock.advance(10)
    sup.tick()
    assert (tmp_path / "p0.log.1").read_bytes() == b"b" * 30
    assert (tmp_path / "p0.log.2").read_bytes() == b"a" * 20


# ---- CLI parsing -----------------------------------------------------------------------


def test_parser_defaults_and_new_flags() -> None:
    args = rlp._build_parser().parse_args(["--investor", "jack", "--restart-failed"])
    assert args.restart_delay_seconds == 15.0
    assert args.restart_max_delay_seconds == 600.0
    assert args.restart_stable_seconds == 900.0
    assert args.log_max_bytes == 50 * 1024 * 1024
    assert args.log_backups == 5
    assert args.heartbeat_stale_seconds is None
    assert args.heartbeat_kill_grace_seconds == 30.0
    assert args.no_heartbeat_restart is False
    args = rlp._build_parser().parse_args(
        ["--no-heartbeat-restart", "--heartbeat-stale-seconds", "900", "--restart-max-delay-seconds", "120"]
    )
    assert args.no_heartbeat_restart is True
    assert args.heartbeat_stale_seconds == 900.0
    assert args.restart_max_delay_seconds == 120.0


def test_heartbeat_path_for_env_uses_state_file_and_default(tmp_path: Path) -> None:
    (tmp_path / "deribit_engine").mkdir()
    (tmp_path / "config" / "shared" / "strategies").mkdir(parents=True)
    investor_dir = tmp_path / "config" / "investors" / "alice"
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir(parents=True)
    (investor_dir / "accounts.toml").write_text(
        '[investor]\nid = "alice"\ndisplay_name = "Alice"\n\n[[accounts]]\nslug = "naked"\nstrategy = "naked_short"\n',
        encoding="utf-8",
    )
    env_default = accounts_dir / ".env.naked"
    env_default.write_text("DERIBIT_CLIENT_ID=a\nDERIBIT_CLIENT_SECRET=b\n", encoding="utf-8")
    assert rlp._heartbeat_path_for_env(tmp_path, env_default) == (
        tmp_path / ".state" / "investors" / "alice" / "naked.heartbeat.json"
    )

    (investor_dir / "accounts.toml").write_text(
        '[investor]\nid = "alice"\ndisplay_name = "Alice"\n\n[[accounts]]\nslug = "naked"\nstrategy = "naked_short"\n'
        '\n[[accounts]]\nslug = "cc"\nstrategy = "covered_call"\n',
        encoding="utf-8",
    )
    env_custom = accounts_dir / ".env.cc"
    env_custom.write_text("STATE_FILE=custom/dir/cc_state.json\n", encoding="utf-8")
    assert rlp._heartbeat_path_for_env(tmp_path, env_custom) == (
        tmp_path / "custom" / "dir" / "cc_state.heartbeat.json"
    )

    assert rlp._heartbeat_path_for_env(tmp_path, tmp_path / "loose.env") is None
