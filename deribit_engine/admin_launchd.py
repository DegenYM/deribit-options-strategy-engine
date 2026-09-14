"""Manage the local admin console LaunchAgent (macOS launchd)."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .admin_server.app import DEFAULT_ADMIN_PORT
from .env_layout import find_repo_root
from .exceptions import ConfigurationError
from .investor_launchd_common import (
    LaunchdAction,
    bootout_plist,
    bootstrap_plist,
    force_reload_plist,
    install_plist_file,
    is_launchd_loaded,
    kickstart_launchd,
    launch_agents_dir,
    reload_plist,
)
from .investor_ops import _render_template_file
from .investor_registry import load_platform_registry, resolve_effective_repo_root

ADMIN_LAUNCHD_LABEL = "com.deribit.admin"
ADMIN_HEALTH_URL = f"http://127.0.0.1:{DEFAULT_ADMIN_PORT}/api/admin/health"


@dataclass(frozen=True)
class AdminLaunchdResult:
    label: str
    action: LaunchdAction
    ok: bool
    state: str
    message: str
    health_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "action": self.action,
            "ok": self.ok,
            "state": self.state,
            "message": self.message,
            "health_ok": self.health_ok,
        }


def admin_plist_filename() -> str:
    return f"{ADMIN_LAUNCHD_LABEL}.plist"


def generated_admin_plist_path(repo_root: Path) -> Path:
    return repo_root / "config/platform/generated/launchd" / admin_plist_filename()


def installed_admin_plist_path() -> Path:
    return launch_agents_dir() / admin_plist_filename()


def admin_log_paths() -> tuple[Path, Path]:
    log_dir = Path.home() / "Library" / "Logs" / "deribit" / "admin"
    return log_dir / "admin.log", log_dir / "admin.err.log"


def render_admin_plist(*, repo_root: Path, python_bin: str, port: int = DEFAULT_ADMIN_PORT) -> Path:
    stdout_path, stderr_path = admin_log_paths()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    replacements = {
        "__LABEL__": ADMIN_LAUNCHD_LABEL,
        "__REPO_ROOT__": str(repo_root),
        "__PYTHON_BIN__": python_bin,
        "__ADMIN_PORT__": str(int(port)),
        "__STDOUT_PATH__": str(stdout_path),
        "__STDERR_PATH__": str(stderr_path),
    }
    template_path = repo_root / "config/launchd/com.deribit.admin.plist.template"
    text = _render_template_file(template_path, replacements)
    out_path = generated_admin_plist_path(repo_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def install_admin_plist(*, repo_root: Path, python_bin: str, port: int = DEFAULT_ADMIN_PORT) -> tuple[Path, bool]:
    src = render_admin_plist(repo_root=repo_root, python_bin=python_bin, port=port)
    return install_plist_file(src, installed_admin_plist_path())


def probe_admin_health(*, timeout_sec: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(ADMIN_HEALTH_URL, timeout=timeout_sec) as response:
            return 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def wait_for_admin_health(
    *,
    max_wait_sec: float = 15.0,
    poll_interval_sec: float = 0.5,
    request_timeout_sec: float = 2.0,
) -> bool:
    deadline = time.monotonic() + max_wait_sec
    while time.monotonic() < deadline:
        if probe_admin_health(timeout_sec=request_timeout_sec):
            return True
        time.sleep(poll_interval_sec)
    return False


def _finalize_result(
    *,
    action: LaunchdAction,
    launchd_ok: bool,
    launchd_message: str,
    check_health: bool,
) -> AdminLaunchdResult:
    if not launchd_ok:
        return AdminLaunchdResult(
            label=ADMIN_LAUNCHD_LABEL,
            action=action,
            ok=False,
            state="failed",
            message=launchd_message,
        )
    health_ok: bool | None = None
    if check_health:
        health_ok = wait_for_admin_health()
    if health_ok is True:
        return AdminLaunchdResult(
            label=ADMIN_LAUNCHD_LABEL,
            action=action,
            ok=True,
            state="healthy",
            message=f"{launchd_message}; {ADMIN_HEALTH_URL} OK",
            health_ok=True,
        )
    if health_ok is False:
        return AdminLaunchdResult(
            label=ADMIN_LAUNCHD_LABEL,
            action=action,
            ok=False,
            state="unhealthy",
            message=f"{launchd_message}; {ADMIN_HEALTH_URL} failed after wait",
            health_ok=False,
        )
    return AdminLaunchdResult(
        label=ADMIN_LAUNCHD_LABEL,
        action=action,
        ok=True,
        state="running",
        message=launchd_message,
    )


def manage_admin_launchd(
    action: LaunchdAction,
    *,
    repo_root: Path | None = None,
    check_health: bool = True,
) -> AdminLaunchdResult:
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    registry = load_platform_registry(repo_root=cwd_repo)
    effective_repo = resolve_effective_repo_root(registry, cwd_repo=cwd_repo)
    python_bin = registry.platform.python_bin or "python3"
    installed_plist = installed_admin_plist_path()

    if action == "start":
        plist_path, plist_changed = install_admin_plist(repo_root=effective_repo, python_bin=python_bin)
        loaded = is_launchd_loaded(ADMIN_LAUNCHD_LABEL)
        if loaded and plist_changed:
            launchd_ok, msg = reload_plist(plist_path, ADMIN_LAUNCHD_LABEL)
            if launchd_ok:
                msg = "reloaded (plist updated)"
        elif loaded:
            launchd_ok, msg = True, "already running"
        else:
            launchd_ok, msg = bootstrap_plist(plist_path)
        return _finalize_result(
            action=action,
            launchd_ok=launchd_ok,
            launchd_message=msg,
            check_health=check_health,
        )

    if action == "stop":
        if not installed_plist.is_file():
            return AdminLaunchdResult(
                label=ADMIN_LAUNCHD_LABEL,
                action=action,
                ok=True,
                state="stopped",
                message="plist not installed",
            )
        ok, msg = bootout_plist(installed_plist, ADMIN_LAUNCHD_LABEL)
        return AdminLaunchdResult(
            label=ADMIN_LAUNCHD_LABEL,
            action=action,
            ok=ok,
            state="stopped" if ok else "failed",
            message=msg,
        )

    if action == "restart":
        plist_path, _changed = install_admin_plist(repo_root=effective_repo, python_bin=python_bin)
        if is_launchd_loaded(ADMIN_LAUNCHD_LABEL):
            launchd_ok, msg = kickstart_launchd(ADMIN_LAUNCHD_LABEL)
            if not launchd_ok:
                launchd_ok, msg = force_reload_plist(plist_path, ADMIN_LAUNCHD_LABEL)
        else:
            launchd_ok, msg = bootstrap_plist(plist_path)
        return _finalize_result(
            action=action,
            launchd_ok=launchd_ok,
            launchd_message=msg,
            check_health=check_health,
        )

    if action == "status":
        loaded = is_launchd_loaded(ADMIN_LAUNCHD_LABEL)
        if loaded and check_health:
            health_ok = wait_for_admin_health(max_wait_sec=3.0, poll_interval_sec=0.5)
        else:
            health_ok = None
        if loaded and health_ok is True:
            state, msg, ok = "healthy", f"launchd loaded; {ADMIN_HEALTH_URL} OK", True
        elif loaded and health_ok is False:
            state, msg, ok = "unhealthy", f"launchd loaded; {ADMIN_HEALTH_URL} failed", False
        elif loaded:
            state, msg, ok = "loaded", "launchd loaded", True
        else:
            state, msg, ok = "stopped", "not loaded in launchd", True
        return AdminLaunchdResult(
            label=ADMIN_LAUNCHD_LABEL,
            action=action,
            ok=ok,
            state=state,
            message=msg,
            health_ok=health_ok,
        )

    raise ConfigurationError(f"Unsupported action: {action!r}")
