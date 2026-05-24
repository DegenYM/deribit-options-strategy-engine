"""Shared macOS launchd helpers for investor LaunchAgents."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal

LaunchdAction = Literal["start", "stop", "restart", "status"]


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


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
