"""Manage per-investor frontend LaunchAgents (macOS launchd)."""

from __future__ import annotations

import plistlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .env_layout import find_repo_root
from .exceptions import ConfigurationError
from .investor_launchd_common import (
    LaunchdAction as FrontendLaunchdAction,
    bootstrap_plist as _bootstrap_plist,
    bootout_plist as _bootout_plist,
    install_plist_file,
    is_launchd_loaded,
    launch_agents_dir,
    reload_plist as _reload_frontend_plist,
)
from .investor_ops import render_launchd_plists
from .investor_registry import (
    InvestorRegistryEntry,
    PlatformRegistry,
    load_platform_registry,
    resolve_effective_repo_root,
    validate_investor_id,
)


@dataclass(frozen=True)
class FrontendLaunchdResult:
    investor_id: str
    label: str
    frontend_port: int | None
    action: FrontendLaunchdAction
    ok: bool
    state: str
    message: str
    health_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "investor_id": self.investor_id,
            "label": self.label,
            "frontend_port": self.frontend_port,
            "action": self.action,
            "ok": self.ok,
            "state": self.state,
            "message": self.message,
            "health_ok": self.health_ok,
        }


def frontend_launchd_label(investor_id: str) -> str:
    return f"com.deribit.frontend.{validate_investor_id(investor_id)}"


def frontend_plist_filename(investor_id: str) -> str:
    return f"{frontend_launchd_label(investor_id)}.plist"


def generated_frontend_plist_path(repo_root: Path, investor_id: str) -> Path:
    return repo_root / "config/platform/generated/launchd" / frontend_plist_filename(investor_id)


def installed_frontend_plist_path(investor_id: str) -> Path:
    return launch_agents_dir() / frontend_plist_filename(investor_id)


def frontend_targets(
    registry: PlatformRegistry,
    *,
    investor_id: str | None = None,
    include_disabled: bool = False,
) -> tuple[InvestorRegistryEntry, ...]:
    if investor_id is not None:
        normalized = validate_investor_id(investor_id)
        entry = registry.entry_for(normalized)
        if entry is None:
            raise ConfigurationError(f"Investor {normalized!r} not found in registry.toml")
        if not entry.frontend_enabled and not include_disabled:
            raise ConfigurationError(
                f"Investor {normalized!r} has frontend_enabled=false in registry.toml"
            )
        return (entry,)
    rows = registry.investors if include_disabled else tuple(
        entry for entry in registry.investors if entry.frontend_enabled
    )
    if not rows:
        raise ConfigurationError("No investors with frontend_enabled=true in registry.toml")
    return rows


def sync_generated_frontend_plist(
    investor_id: str,
    *,
    repo_root: Path,
    registry: PlatformRegistry,
) -> Path:
    render_launchd_plists(investor_id, repo_root=repo_root, registry=registry)
    path = generated_frontend_plist_path(repo_root, investor_id)
    if not path.is_file():
        raise ConfigurationError(f"Failed to write frontend plist: {path}")
    return path


def read_frontend_port_from_plist(plist_path: Path) -> int | None:
    if not plist_path.is_file():
        return None
    try:
        data = plistlib.load(plist_path.open("rb"))
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    args = data.get("ProgramArguments") or []
    for index, item in enumerate(args):
        if item == "--port" and index + 1 < len(args):
            try:
                return int(args[index + 1])
            except (TypeError, ValueError):
                return None
    return None


def resolve_listen_port(entry: InvestorRegistryEntry, plist_path: Path) -> int | None:
    return read_frontend_port_from_plist(plist_path) or entry.frontend_port


def _registry_port_mismatch_note(
    registry_port: int | None,
    listen_port: int | None,
) -> str:
    if registry_port is None or listen_port is None or registry_port == listen_port:
        return ""
    return f"; registry.toml has :{registry_port} but plist uses :{listen_port}"


def install_frontend_plist(
    investor_id: str,
    *,
    repo_root: Path,
    registry: PlatformRegistry,
) -> tuple[Path, bool]:
    src = sync_generated_frontend_plist(investor_id, repo_root=repo_root, registry=registry)
    dest = installed_frontend_plist_path(investor_id)
    return install_plist_file(src, dest)


def is_frontend_loaded(label: str) -> bool:
    return is_launchd_loaded(label)


def probe_frontend_health(port: int, *, timeout_sec: float = 2.0) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as response:
            return 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def wait_for_frontend_health(
    port: int,
    *,
    max_wait_sec: float = 20.0,
    poll_interval_sec: float = 0.5,
    request_timeout_sec: float = 2.0,
) -> bool:
    deadline = time.monotonic() + max_wait_sec
    while time.monotonic() < deadline:
        if probe_frontend_health(port, timeout_sec=request_timeout_sec):
            return True
        time.sleep(poll_interval_sec)
    return False


def _finalize_launchd_result(
    *,
    entry: InvestorRegistryEntry,
    label: str,
    action: FrontendLaunchdAction,
    launchd_ok: bool,
    launchd_message: str,
    listen_port: int | None,
    check_health: bool,
) -> FrontendLaunchdResult:
    mismatch = _registry_port_mismatch_note(entry.frontend_port, listen_port)
    if not launchd_ok:
        return FrontendLaunchdResult(
            investor_id=entry.investor_id,
            label=label,
            frontend_port=listen_port,
            action=action,
            ok=False,
            state="failed",
            message=launchd_message + mismatch,
        )
    health_ok: bool | None = None
    if check_health and listen_port is not None:
        health_ok = wait_for_frontend_health(listen_port)
    if health_ok is True:
        state = "healthy"
        message = f"{launchd_message}; /api/health OK"
        ok = True
    elif health_ok is False:
        state = "unhealthy"
        message = f"{launchd_message}; /api/health failed after wait{mismatch}"
        ok = False
    else:
        state = "running"
        message = launchd_message + mismatch
        ok = True
    return FrontendLaunchdResult(
        investor_id=entry.investor_id,
        label=label,
        frontend_port=listen_port,
        action=action,
        ok=ok,
        state=state,
        message=message,
        health_ok=health_ok,
    )


def manage_frontend_launchd(
    action: FrontendLaunchdAction,
    *,
    repo_root: Path | None = None,
    investor_id: str | None = None,
    include_disabled: bool = False,
    check_health: bool = True,
) -> list[FrontendLaunchdResult]:
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    registry = load_platform_registry(repo_root=cwd_repo)
    effective_repo = resolve_effective_repo_root(registry, cwd_repo=cwd_repo)
    targets = frontend_targets(
        registry,
        investor_id=investor_id,
        include_disabled=include_disabled,
    )

    results: list[FrontendLaunchdResult] = []
    for entry in targets:
        label = frontend_launchd_label(entry.investor_id)
        log_dir = effective_repo / "logs" / "frontend" / entry.investor_id
        log_dir.mkdir(parents=True, exist_ok=True)
        installed_plist = installed_frontend_plist_path(entry.investor_id)

        if action == "start":
            try:
                plist_path, plist_changed = install_frontend_plist(
                    entry.investor_id,
                    repo_root=effective_repo,
                    registry=registry,
                )
            except ConfigurationError as exc:
                results.append(
                    FrontendLaunchdResult(
                        investor_id=entry.investor_id,
                        label=label,
                        frontend_port=entry.frontend_port,
                        action=action,
                        ok=False,
                        state="error",
                        message=str(exc),
                    )
                )
                continue
            listen_port = resolve_listen_port(entry, plist_path)
            loaded = is_frontend_loaded(label)
            if loaded and plist_changed:
                launchd_ok, msg = _reload_frontend_plist(plist_path, label)
                if launchd_ok:
                    msg = "reloaded (plist updated)"
            elif loaded:
                launchd_ok, msg = True, "already running"
            else:
                launchd_ok, msg = _bootstrap_plist(plist_path)
            result = _finalize_launchd_result(
                entry=entry,
                label=label,
                action=action,
                launchd_ok=launchd_ok,
                launchd_message=msg,
                listen_port=listen_port,
                check_health=check_health,
            )
            if (
                not result.ok
                and result.health_ok is False
                and loaded
                and not plist_changed
            ):
                reload_ok, reload_msg = _reload_frontend_plist(plist_path, label)
                if reload_ok:
                    result = _finalize_launchd_result(
                        entry=entry,
                        label=label,
                        action=action,
                        launchd_ok=True,
                        launchd_message="reloaded after unhealthy",
                        listen_port=listen_port,
                        check_health=check_health,
                    )
            results.append(result)
            continue

        if action == "stop":
            listen_port = resolve_listen_port(entry, installed_plist)
            if not installed_plist.is_file():
                results.append(
                    FrontendLaunchdResult(
                        investor_id=entry.investor_id,
                        label=label,
                        frontend_port=listen_port,
                        action=action,
                        ok=True,
                        state="stopped",
                        message="plist not installed",
                    )
                )
                continue
            ok, msg = _bootout_plist(installed_plist, label)
            results.append(
                FrontendLaunchdResult(
                    investor_id=entry.investor_id,
                    label=label,
                    frontend_port=listen_port,
                    action=action,
                    ok=ok,
                    state="stopped" if ok else "failed",
                    message=msg,
                )
            )
            continue

        if action == "restart":
            try:
                plist_path, plist_changed = install_frontend_plist(
                    entry.investor_id,
                    repo_root=effective_repo,
                    registry=registry,
                )
            except ConfigurationError as exc:
                results.append(
                    FrontendLaunchdResult(
                        investor_id=entry.investor_id,
                        label=label,
                        frontend_port=entry.frontend_port,
                        action=action,
                        ok=False,
                        state="error",
                        message=str(exc),
                    )
                )
                continue
            listen_port = resolve_listen_port(entry, plist_path)
            launchd_ok, msg = _reload_frontend_plist(plist_path, label)
            if launchd_ok and not is_frontend_loaded(label):
                launchd_ok, msg = _bootstrap_plist(plist_path)
            elif launchd_ok:
                msg = "reloaded"
            results.append(
                _finalize_launchd_result(
                    entry=entry,
                    label=label,
                    action=action,
                    launchd_ok=launchd_ok,
                    launchd_message=msg,
                    listen_port=listen_port,
                    check_health=check_health,
                )
            )
            continue

        if action == "status":
            listen_port = resolve_listen_port(entry, installed_plist)
            loaded = is_frontend_loaded(label)
            mismatch = _registry_port_mismatch_note(entry.frontend_port, listen_port)
            if loaded and check_health and listen_port is not None:
                health_ok = wait_for_frontend_health(
                    listen_port,
                    max_wait_sec=3.0,
                    poll_interval_sec=0.5,
                )
            else:
                health_ok = None
            if loaded and health_ok is True:
                state, msg, ok = "healthy", f"launchd loaded; /api/health OK{mismatch}", True
            elif loaded and health_ok is False:
                state, msg, ok = "unhealthy", f"launchd loaded; /api/health failed{mismatch}", False
            elif loaded:
                state, msg, ok = "loaded", f"launchd loaded{mismatch}", True
            else:
                state, msg, ok = "stopped", "not loaded in launchd", True
            results.append(
                FrontendLaunchdResult(
                    investor_id=entry.investor_id,
                    label=label,
                    frontend_port=listen_port,
                    action=action,
                    ok=ok,
                    state=state,
                    message=msg,
                    health_ok=health_ok,
                )
            )
            continue

        raise ConfigurationError(f"Unsupported action: {action!r}")

    return results
