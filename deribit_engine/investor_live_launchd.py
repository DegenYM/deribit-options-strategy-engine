"""Manage per-investor live bot LaunchAgents (macOS launchd)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .env_layout import find_repo_root
from .exceptions import ConfigurationError
from .investor_launchd_common import (
    LaunchdAction,
    bootout_plist,
    bootstrap_plist,
    install_plist_file,
    investor_live_launchd_log_paths,
    is_launchd_loaded,
    launch_agents_dir,
    reload_plist,
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
class LiveLaunchdResult:
    investor_id: str
    label: str
    action: LaunchdAction
    ok: bool
    state: str
    message: str
    supervisor_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "investor_id": self.investor_id,
            "label": self.label,
            "action": self.action,
            "ok": self.ok,
            "state": self.state,
            "message": self.message,
            "supervisor_ok": self.supervisor_ok,
        }


def live_launchd_label(investor_id: str) -> str:
    return f"com.deribit.live.{validate_investor_id(investor_id)}"


def live_plist_filename(investor_id: str) -> str:
    return f"{live_launchd_label(investor_id)}.plist"


def generated_live_plist_path(repo_root: Path, investor_id: str) -> Path:
    return repo_root / "config/platform/generated/launchd" / live_plist_filename(investor_id)


def installed_live_plist_path(investor_id: str) -> Path:
    return launch_agents_dir() / live_plist_filename(investor_id)


def supervisor_log_path(repo_root: Path, investor_id: str) -> Path:
    del repo_root
    stdout, _ = investor_live_launchd_log_paths(investor_id)
    return stdout


def live_targets(
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
        if not entry.live_enabled and not include_disabled:
            raise ConfigurationError(f"Investor {normalized!r} has live_enabled=false in registry.toml")
        return (entry,)
    rows = (
        registry.investors if include_disabled else tuple(entry for entry in registry.investors if entry.live_enabled)
    )
    if not rows:
        raise ConfigurationError("No investors with live_enabled=true in registry.toml")
    return rows


def sync_generated_live_plist(
    investor_id: str,
    *,
    repo_root: Path,
    registry: PlatformRegistry,
) -> Path:
    render_launchd_plists(investor_id, repo_root=repo_root, registry=registry)
    path = generated_live_plist_path(repo_root, investor_id)
    if not path.is_file():
        raise ConfigurationError(f"Failed to write live plist: {path}")
    return path


def install_live_plist(
    investor_id: str,
    *,
    repo_root: Path,
    registry: PlatformRegistry,
) -> tuple[Path, bool]:
    src = sync_generated_live_plist(investor_id, repo_root=repo_root, registry=registry)
    dest = installed_live_plist_path(investor_id)
    return install_plist_file(src, dest)


def probe_live_supervisor(repo_root: Path, investor_id: str) -> bool:
    log_path = supervisor_log_path(repo_root, investor_id)
    if not log_path.is_file():
        return False
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return "started " in text and "pid=" in text


def wait_for_live_supervisor(
    repo_root: Path,
    investor_id: str,
    *,
    max_wait_sec: float = 20.0,
    poll_interval_sec: float = 0.5,
) -> bool:
    deadline = time.monotonic() + max_wait_sec
    while time.monotonic() < deadline:
        if probe_live_supervisor(repo_root, investor_id):
            return True
        time.sleep(poll_interval_sec)
    return False


def _finalize_live_result(
    *,
    entry: InvestorRegistryEntry,
    label: str,
    action: LaunchdAction,
    launchd_ok: bool,
    launchd_message: str,
    repo_root: Path,
    check_supervisor: bool,
) -> LiveLaunchdResult:
    if not launchd_ok:
        return LiveLaunchdResult(
            investor_id=entry.investor_id,
            label=label,
            action=action,
            ok=False,
            state="failed",
            message=launchd_message,
        )
    supervisor_ok: bool | None = None
    if check_supervisor and action in {"start", "restart"}:
        supervisor_ok = wait_for_live_supervisor(repo_root, entry.investor_id)
    elif check_supervisor and action == "status" and is_launchd_loaded(label):
        supervisor_ok = probe_live_supervisor(repo_root, entry.investor_id)
    if supervisor_ok is True:
        state = "running"
        message = f"{launchd_message}; supervisor started child bot(s)"
        ok = True
    elif supervisor_ok is False:
        state = "unhealthy"
        message = f"{launchd_message}; supervisor.log missing started pid="
        ok = False
    else:
        state = "running" if is_launchd_loaded(label) else "stopped"
        message = launchd_message
        ok = launchd_ok
    return LiveLaunchdResult(
        investor_id=entry.investor_id,
        label=label,
        action=action,
        ok=ok,
        state=state,
        message=message,
        supervisor_ok=supervisor_ok,
    )


def manage_live_launchd(
    action: LaunchdAction,
    *,
    repo_root: Path | None = None,
    investor_id: str | None = None,
    include_disabled: bool = False,
    check_supervisor: bool = True,
) -> list[LiveLaunchdResult]:
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    registry = load_platform_registry(repo_root=cwd_repo)
    effective_repo = resolve_effective_repo_root(registry, cwd_repo=cwd_repo)
    targets = live_targets(
        registry,
        investor_id=investor_id,
        include_disabled=include_disabled,
    )

    results: list[LiveLaunchdResult] = []
    for entry in targets:
        label = live_launchd_label(entry.investor_id)
        log_dir = effective_repo / "logs" / "live" / entry.investor_id
        log_dir.mkdir(parents=True, exist_ok=True)
        installed_plist = installed_live_plist_path(entry.investor_id)

        if action == "start":
            try:
                plist_path, plist_changed = install_live_plist(
                    entry.investor_id,
                    repo_root=effective_repo,
                    registry=registry,
                )
            except ConfigurationError as exc:
                results.append(
                    LiveLaunchdResult(
                        investor_id=entry.investor_id,
                        label=label,
                        action=action,
                        ok=False,
                        state="error",
                        message=str(exc),
                    )
                )
                continue
            loaded = is_launchd_loaded(label)
            if loaded and plist_changed:
                launchd_ok, msg = reload_plist(plist_path, label)
                if launchd_ok:
                    msg = "reloaded (plist updated)"
            elif loaded:
                launchd_ok, msg = True, "already running"
            else:
                launchd_ok, msg = bootstrap_plist(plist_path)
            result = _finalize_live_result(
                entry=entry,
                label=label,
                action=action,
                launchd_ok=launchd_ok,
                launchd_message=msg,
                repo_root=effective_repo,
                check_supervisor=check_supervisor,
            )
            if not result.ok and result.supervisor_ok is False and loaded and not plist_changed:
                reload_ok, _reload_msg = reload_plist(plist_path, label)
                if reload_ok:
                    result = _finalize_live_result(
                        entry=entry,
                        label=label,
                        action=action,
                        launchd_ok=True,
                        launchd_message="reloaded after unhealthy supervisor",
                        repo_root=effective_repo,
                        check_supervisor=check_supervisor,
                    )
            results.append(result)
            continue

        if action == "stop":
            if not installed_plist.is_file():
                results.append(
                    LiveLaunchdResult(
                        investor_id=entry.investor_id,
                        label=label,
                        action=action,
                        ok=True,
                        state="stopped",
                        message="plist not installed",
                    )
                )
                continue
            ok, msg = bootout_plist(installed_plist, label)
            results.append(
                LiveLaunchdResult(
                    investor_id=entry.investor_id,
                    label=label,
                    action=action,
                    ok=ok,
                    state="stopped" if ok else "failed",
                    message=msg,
                )
            )
            continue

        if action == "restart":
            try:
                plist_path, _plist_changed = install_live_plist(
                    entry.investor_id,
                    repo_root=effective_repo,
                    registry=registry,
                )
            except ConfigurationError as exc:
                results.append(
                    LiveLaunchdResult(
                        investor_id=entry.investor_id,
                        label=label,
                        action=action,
                        ok=False,
                        state="error",
                        message=str(exc),
                    )
                )
                continue
            launchd_ok, msg = reload_plist(plist_path, label)
            if launchd_ok and not is_launchd_loaded(label):
                launchd_ok, msg = bootstrap_plist(plist_path)
            elif launchd_ok:
                msg = "reloaded"
            results.append(
                _finalize_live_result(
                    entry=entry,
                    label=label,
                    action=action,
                    launchd_ok=launchd_ok,
                    launchd_message=msg,
                    repo_root=effective_repo,
                    check_supervisor=check_supervisor,
                )
            )
            continue

        if action == "status":
            loaded = is_launchd_loaded(label)
            supervisor_ok = (
                probe_live_supervisor(effective_repo, entry.investor_id) if check_supervisor and loaded else None
            )
            if loaded and supervisor_ok is True:
                state, msg, ok = "running", "launchd loaded; supervisor has started bot(s)", True
            elif loaded and supervisor_ok is False:
                state, msg, ok = "unhealthy", "launchd loaded; supervisor.log not ready", False
            elif loaded:
                state, msg, ok = "loaded", "launchd loaded", True
            else:
                state, msg, ok = "stopped", "not loaded in launchd", True
            results.append(
                LiveLaunchdResult(
                    investor_id=entry.investor_id,
                    label=label,
                    action=action,
                    ok=ok,
                    state=state,
                    message=msg,
                    supervisor_ok=supervisor_ok,
                )
            )
            continue

        raise ConfigurationError(f"Unsupported action: {action!r}")

    return results
