"""Shared macOS launchd helpers for investor LaunchAgents."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Literal

LaunchdAction = Literal["start", "stop", "restart", "status"]


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def investor_launchd_log_dir(service: str, investor_id: str) -> Path:
    """LaunchAgent stdout/stderr under ~/Library/Logs (avoids macOS Desktop TCC)."""
    return Path.home() / "Library" / "Logs" / "deribit" / service / investor_id


def investor_live_launchd_log_paths(investor_id: str) -> tuple[Path, Path]:
    log_dir = investor_launchd_log_dir("live", investor_id)
    return log_dir / "supervisor.log", log_dir / "supervisor.err.log"


def investor_frontend_launchd_log_paths(investor_id: str) -> tuple[Path, Path]:
    log_dir = investor_launchd_log_dir("frontend", investor_id)
    return log_dir / "frontend.log", log_dir / "frontend.err.log"


def ensure_investor_launchd_log_dir(service: str, investor_id: str) -> Path:
    log_dir = investor_launchd_log_dir(service, investor_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def launchd_gui_domain() -> str:
    return f"gui/{os.getuid()}"


def launchd_job_path(label: str) -> str:
    return f"{launchd_gui_domain()}/{label}"


def _run_launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or "") + (result.stderr or "")


def is_launchd_loaded(label: str) -> bool:
    result = _run_launchctl("print", launchd_job_path(label))
    return result.returncode == 0


def bootstrap_plist(plist_path: Path) -> tuple[bool, str]:
    domain = launchd_gui_domain()
    result = _run_launchctl("bootstrap", domain, str(plist_path))
    if result.returncode == 0:
        return True, "bootstrapped"
    output = _combined_output(result).strip()
    if "already" in output.lower() or "input/output error" in output.lower():
        return True, "already loaded"
    legacy = _run_launchctl("load", str(plist_path))
    if legacy.returncode == 0:
        return True, "loaded (legacy)"
    legacy_out = _combined_output(legacy).strip()
    if "already" in legacy_out.lower():
        return True, "already loaded"
    detail = output or legacy_out or f"exit {result.returncode}"
    return False, detail


def bootout_plist(plist_path: Path, label: str) -> tuple[bool, str]:
    domain = launchd_gui_domain()
    result = _run_launchctl("bootout", domain, str(plist_path))
    if result.returncode == 0:
        return True, "stopped"
    output = _combined_output(result).strip()
    if not is_launchd_loaded(label):
        return True, "not loaded"
    legacy = _run_launchctl("unload", str(plist_path))
    if legacy.returncode == 0:
        return True, "unloaded (legacy)"
    if not is_launchd_loaded(label):
        return True, "not loaded"
    detail = output or _combined_output(legacy).strip() or f"exit {result.returncode}"
    return False, detail


def reload_plist(plist_path: Path, label: str) -> tuple[bool, str]:
    if is_launchd_loaded(label):
        ok, msg = bootout_plist(plist_path, label)
        if not ok:
            return False, f"reload bootout failed: {msg}"
    return bootstrap_plist(plist_path)


def list_pids_listening_on_tcp_port(port: int) -> list[int]:
    """Return PIDs listening on ``port`` (empty when none or lookup fails)."""
    if port <= 0:
        return []
    result = subprocess.run(
        ["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return sorted(set(pids))


def terminate_tcp_listeners(port: int | None, *, grace_sec: float = 1.0) -> tuple[list[int], str]:
    """Stop orphan listeners on ``port`` after launchd bootout (SIGTERM, then SIGKILL)."""
    if port is None or port <= 0:
        return [], "no port"
    pids = list_pids_listening_on_tcp_port(port)
    if not pids:
        return [], "no listeners"
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(max(grace_sec, 0.0))
    for pid in list_pids_listening_on_tcp_port(port):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    remaining = list_pids_listening_on_tcp_port(port)
    if remaining:
        return pids, f"terminated {pids}; still listening: {remaining}"
    return pids, f"terminated {pids}"


def force_reload_plist(
    plist_path: Path,
    label: str,
    *,
    listen_port: int | None = None,
) -> tuple[bool, str]:
    """Boot out launchd job, kill stale listeners on ``listen_port``, then bootstrap."""
    steps: list[str] = []
    if is_launchd_loaded(label):
        ok, msg = bootout_plist(plist_path, label)
        steps.append(f"bootout: {msg}")
        if not ok:
            return False, "; ".join(steps)
    killed, kill_msg = terminate_tcp_listeners(listen_port)
    if killed:
        steps.append(f"kill: {kill_msg}")
    ok, msg = bootstrap_plist(plist_path)
    steps.append(f"bootstrap: {msg}")
    if not ok:
        return False, "; ".join(steps)
    prefix = "force reloaded" if killed else "reloaded"
    return True, f"{prefix} ({'; '.join(steps)})"


def kickstart_launchd(label: str) -> tuple[bool, str]:
    result = _run_launchctl("kickstart", "-k", launchd_job_path(label))
    if result.returncode == 0:
        return True, "restarted"
    detail = _combined_output(result).strip() or f"exit {result.returncode}"
    return False, detail


def install_plist_file(src: Path, dest: Path) -> tuple[Path, bool]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_bytes = src.read_bytes()
    changed = not dest.is_file() or dest.read_bytes() != src_bytes
    if changed:
        dest.write_bytes(src_bytes)
    return dest, changed
