"""Manage Cloudflare Tunnel LaunchAgent (macOS launchd)."""

from __future__ import annotations

import re
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from .investor_registry import PlatformRegistry, load_platform_registry, resolve_effective_repo_root

_DEFAULT_TUNNEL_NAME = "tunnel"
_TUNNEL_METRICS_URL = "http://127.0.0.1:20241/metrics"
_CLOUDFLARED_BIN_CANDIDATES = (
    "/opt/homebrew/bin/cloudflared",
    "/usr/local/bin/cloudflared",
)


@dataclass(frozen=True)
class TunnelLaunchdResult:
    tunnel_name: str
    label: str
    action: LaunchdAction
    ok: bool
    state: str
    message: str
    health_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tunnel_name": self.tunnel_name,
            "label": self.label,
            "action": self.action,
            "ok": self.ok,
            "state": self.state,
            "message": self.message,
            "health_ok": self.health_ok,
        }


def tunnel_slug(tunnel_name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", tunnel_name.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or _DEFAULT_TUNNEL_NAME


def tunnel_launchd_label(tunnel_name: str) -> str:
    return f"com.deribit.cloudflared.{tunnel_slug(tunnel_name)}"


def tunnel_plist_filename(tunnel_name: str) -> str:
    return f"{tunnel_launchd_label(tunnel_name)}.plist"


def cloudflared_config_path() -> Path:
    return Path.home() / ".cloudflared" / "config.yml"


def resolve_cloudflared_bin() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    for candidate in _CLOUDFLARED_BIN_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise ConfigurationError("cloudflared not found; install with: brew install cloudflared")


def resolve_tunnel_name(registry: PlatformRegistry) -> str:
    name = (registry.platform.tunnel_name or "").strip()
    return name or _DEFAULT_TUNNEL_NAME


def generated_tunnel_plist_path(repo_root: Path, tunnel_name: str) -> Path:
    return repo_root / "config/platform/generated/launchd" / tunnel_plist_filename(tunnel_name)


def installed_tunnel_plist_path(tunnel_name: str) -> Path:
    return launch_agents_dir() / tunnel_plist_filename(tunnel_name)


def tunnel_log_paths(tunnel_name: str) -> tuple[Path, Path]:
    slug = tunnel_slug(tunnel_name)
    log_dir = Path.home() / "Library" / "Logs"
    return (
        log_dir / f"cloudflared-{slug}.log",
        log_dir / f"cloudflared-{slug}.err.log",
    )


def render_tunnel_plist(
    *,
    repo_root: Path,
    registry: PlatformRegistry,
) -> Path:
    tunnel_name = resolve_tunnel_name(registry)
    label = tunnel_launchd_label(tunnel_name)
    stdout_path, stderr_path = tunnel_log_paths(tunnel_name)
    config_path = cloudflared_config_path()
    if not config_path.is_file():
        raise ConfigurationError(f"Missing cloudflared config: {config_path}")

    replacements = {
        "__LABEL__": label,
        "__CLOUDFLARED_BIN__": resolve_cloudflared_bin(),
        "__CONFIG_PATH__": str(config_path),
        "__STDOUT_PATH__": str(stdout_path),
        "__STDERR_PATH__": str(stderr_path),
    }
    template_path = repo_root / "config/launchd/com.deribit.cloudflared.plist.template"
    text = _render_template_file(template_path, replacements)
    out_path = generated_tunnel_plist_path(repo_root, tunnel_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def install_tunnel_plist(
    *,
    repo_root: Path,
    registry: PlatformRegistry,
) -> tuple[Path, bool]:
    src = render_tunnel_plist(repo_root=repo_root, registry=registry)
    tunnel_name = resolve_tunnel_name(registry)
    dest = installed_tunnel_plist_path(tunnel_name)
    return install_plist_file(src, dest)


def probe_tunnel_metrics(*, timeout_sec: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(_TUNNEL_METRICS_URL, timeout=timeout_sec) as response:
            return 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def wait_for_tunnel_metrics(
    *,
    max_wait_sec: float = 15.0,
    poll_interval_sec: float = 0.5,
    request_timeout_sec: float = 2.0,
) -> bool:
    deadline = time.monotonic() + max_wait_sec
    while time.monotonic() < deadline:
        if probe_tunnel_metrics(timeout_sec=request_timeout_sec):
            return True
        time.sleep(poll_interval_sec)
    return False


def _finalize_result(
    *,
    tunnel_name: str,
    label: str,
    action: LaunchdAction,
    launchd_ok: bool,
    launchd_message: str,
    check_health: bool,
) -> TunnelLaunchdResult:
    if not launchd_ok:
        return TunnelLaunchdResult(
            tunnel_name=tunnel_name,
            label=label,
            action=action,
            ok=False,
            state="failed",
            message=launchd_message,
        )
    health_ok: bool | None = None
    if check_health:
        health_ok = wait_for_tunnel_metrics()
    if health_ok is True:
        return TunnelLaunchdResult(
            tunnel_name=tunnel_name,
            label=label,
            action=action,
            ok=True,
            state="healthy",
            message=f"{launchd_message}; tunnel metrics OK",
            health_ok=True,
        )
    if health_ok is False:
        return TunnelLaunchdResult(
            tunnel_name=tunnel_name,
            label=label,
            action=action,
            ok=False,
            state="unhealthy",
            message=f"{launchd_message}; tunnel metrics failed after wait",
            health_ok=False,
        )
    return TunnelLaunchdResult(
        tunnel_name=tunnel_name,
        label=label,
        action=action,
        ok=True,
        state="running",
        message=launchd_message,
    )


def manage_tunnel_launchd(
    action: LaunchdAction,
    *,
    repo_root: Path | None = None,
    check_health: bool = True,
) -> TunnelLaunchdResult:
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    registry = load_platform_registry(repo_root=cwd_repo)
    effective_repo = resolve_effective_repo_root(registry, cwd_repo=cwd_repo)
    tunnel_name = resolve_tunnel_name(registry)
    label = tunnel_launchd_label(tunnel_name)
    installed_plist = installed_tunnel_plist_path(tunnel_name)

    if action == "start":
        try:
            plist_path, plist_changed = install_tunnel_plist(repo_root=effective_repo, registry=registry)
        except ConfigurationError as exc:
            return TunnelLaunchdResult(
                tunnel_name=tunnel_name,
                label=label,
                action=action,
                ok=False,
                state="error",
                message=str(exc),
            )
        loaded = is_launchd_loaded(label)
        if loaded and plist_changed:
            launchd_ok, msg = reload_plist(plist_path, label)
            if launchd_ok:
                msg = "reloaded (plist updated)"
        elif loaded:
            launchd_ok, msg = True, "already running"
        else:
            launchd_ok, msg = bootstrap_plist(plist_path)
        return _finalize_result(
            tunnel_name=tunnel_name,
            label=label,
            action=action,
            launchd_ok=launchd_ok,
            launchd_message=msg,
            check_health=check_health,
        )

    if action == "stop":
        if not installed_plist.is_file():
            return TunnelLaunchdResult(
                tunnel_name=tunnel_name,
                label=label,
                action=action,
                ok=True,
                state="stopped",
                message="plist not installed",
            )
        ok, msg = bootout_plist(installed_plist, label)
        return TunnelLaunchdResult(
            tunnel_name=tunnel_name,
            label=label,
            action=action,
            ok=ok,
            state="stopped" if ok else "failed",
            message=msg,
        )

    if action == "restart":
        try:
            plist_path, _plist_changed = install_tunnel_plist(repo_root=effective_repo, registry=registry)
        except ConfigurationError as exc:
            return TunnelLaunchdResult(
                tunnel_name=tunnel_name,
                label=label,
                action=action,
                ok=False,
                state="error",
                message=str(exc),
            )
        if is_launchd_loaded(label):
            launchd_ok, msg = kickstart_launchd(label)
            if not launchd_ok:
                launchd_ok, msg = force_reload_plist(plist_path, label)
        else:
            launchd_ok, msg = bootstrap_plist(plist_path)
        return _finalize_result(
            tunnel_name=tunnel_name,
            label=label,
            action=action,
            launchd_ok=launchd_ok,
            launchd_message=msg,
            check_health=check_health,
        )

    if action == "status":
        loaded = is_launchd_loaded(label)
        if loaded and check_health:
            health_ok = wait_for_tunnel_metrics(max_wait_sec=3.0, poll_interval_sec=0.5)
        else:
            health_ok = None
        if loaded and health_ok is True:
            state, msg, ok = "healthy", "launchd loaded; tunnel metrics OK", True
        elif loaded and health_ok is False:
            state, msg, ok = "unhealthy", "launchd loaded; tunnel metrics failed", False
        elif loaded:
            state, msg, ok = "loaded", "launchd loaded", True
        else:
            state, msg, ok = "stopped", "not loaded in launchd", True
        return TunnelLaunchdResult(
            tunnel_name=tunnel_name,
            label=label,
            action=action,
            ok=ok,
            state=state,
            message=msg,
            health_ok=health_ok,
        )

    raise ConfigurationError(f"Unsupported action: {action!r}")
