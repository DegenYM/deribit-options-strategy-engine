"""Platform registry for investor ops metadata (ports, hostnames, Access email).

Separate from per-investor ``accounts.toml`` (strategy manifest). Secrets stay in
``config/investors/<id>/accounts/.env.*`` only.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .env_layout import find_repo_root
from .exceptions import ConfigurationError

CONFIG_PLATFORM = Path("config/platform")
REGISTRY_FILENAME = "registry.toml"
REGISTRY_EXAMPLE = "registry.toml.example"

_INVESTOR_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class PlatformSettings:
    repo_root: Path | None
    python_bin: str | None
    domain: str | None
    tunnel_name: str | None
    next_frontend_port: int


@dataclass(frozen=True)
class InvestorRegistryEntry:
    investor_id: str
    display_name: str
    dashboard_email: str | None
    access_method: str
    hostname: str | None
    frontend_port: int | None
    live_enabled: bool
    frontend_enabled: bool


@dataclass(frozen=True)
class PlatformRegistry:
    path: Path
    platform: PlatformSettings
    investors: tuple[InvestorRegistryEntry, ...]

    def entry_for(self, investor_id: str) -> InvestorRegistryEntry | None:
        for entry in self.investors:
            if entry.investor_id == investor_id:
                return entry
        return None

    def investor_ids(self) -> tuple[str, ...]:
        return tuple(entry.investor_id for entry in self.investors)


def validate_investor_id(investor_id: str) -> str:
    normalized = investor_id.strip().lower()
    if not normalized or not _INVESTOR_ID_RE.match(normalized):
        raise ConfigurationError(
            f"Invalid investor id {investor_id!r}; use lowercase letters, digits, underscore "
            "(must start with a letter)."
        )
    if normalized == "_example":
        raise ConfigurationError("Investor id '_example' is reserved for templates.")
    return normalized


def registry_path(repo_root: Path) -> Path:
    return repo_root / CONFIG_PLATFORM / REGISTRY_FILENAME


def registry_example_path(repo_root: Path) -> Path:
    return repo_root / CONFIG_PLATFORM / REGISTRY_EXAMPLE


def load_platform_registry(
    *,
    repo_root: Path | str | None = None,
    create_if_missing: bool = False,
) -> PlatformRegistry:
    root = Path(repo_root) if repo_root is not None else find_repo_root(Path.cwd())
    if root is None:
        raise ConfigurationError("Cannot locate repository root (missing deribit_demo/)")
    path = registry_path(root)
    if not path.is_file():
        example = registry_example_path(root)
        if create_if_missing and example.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise ConfigurationError(f"Missing {path}. Copy {example} to {path.name} and edit platform settings.")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    platform_raw = data.get("platform") or {}
    repo_raw = platform_raw.get("repo_root")
    repo_override = Path(str(repo_raw)).expanduser().resolve() if repo_raw else None
    next_port_raw = platform_raw.get("next_frontend_port", 8765)
    try:
        next_port = int(next_port_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{path}: [platform].next_frontend_port must be an integer") from exc

    platform = PlatformSettings(
        repo_root=repo_override,
        python_bin=str(platform_raw["python_bin"]).strip() if platform_raw.get("python_bin") else None,
        domain=str(platform_raw["domain"]).strip() if platform_raw.get("domain") else None,
        tunnel_name=str(platform_raw["tunnel_name"]).strip() if platform_raw.get("tunnel_name") else None,
        next_frontend_port=next_port,
    )

    investors: list[InvestorRegistryEntry] = []
    for index, row in enumerate(data.get("investors") or []):
        if not isinstance(row, dict):
            raise ConfigurationError(f"{path}: investors[{index}] must be a table")
        investor_id = validate_investor_id(str(row.get("id") or ""))
        display_name = str(row.get("display_name") or investor_id).strip()
        access_method = str(row.get("access_method") or "email").strip().lower()
        if access_method not in {"email", "google"}:
            raise ConfigurationError(f"{path}: investors[{index}].access_method must be 'email' or 'google'")
        port_raw = row.get("frontend_port")
        frontend_port = int(port_raw) if port_raw is not None else None
        investors.append(
            InvestorRegistryEntry(
                investor_id=investor_id,
                display_name=display_name,
                dashboard_email=str(row["dashboard_email"]).strip() if row.get("dashboard_email") else None,
                access_method=access_method,
                hostname=str(row["hostname"]).strip() if row.get("hostname") else None,
                frontend_port=frontend_port,
                live_enabled=bool(row.get("live_enabled", True)),
                frontend_enabled=bool(row.get("frontend_enabled", True)),
            )
        )

    return PlatformRegistry(path=path, platform=platform, investors=tuple(investors))


def save_platform_registry(registry: PlatformRegistry) -> None:
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(_render_registry(registry), encoding="utf-8")


def resolve_effective_repo_root(registry: PlatformRegistry, *, cwd_repo: Path | None) -> Path:
    if registry.platform.repo_root is not None:
        return registry.platform.repo_root
    if cwd_repo is not None:
        return cwd_repo
    raise ConfigurationError("Cannot resolve repo root; set [platform].repo_root in registry.toml")


def default_hostname(investor_id: str, domain: str | None) -> str | None:
    if not domain:
        return None
    return f"{investor_id}.{domain}"


def allocate_frontend_port(registry: PlatformRegistry) -> int:
    used = {entry.frontend_port for entry in registry.investors if entry.frontend_port is not None}
    port = registry.platform.next_frontend_port
    while port in used:
        port += 1
    return port


def add_investor_to_registry(
    registry: PlatformRegistry,
    entry: InvestorRegistryEntry,
) -> PlatformRegistry:
    if registry.entry_for(entry.investor_id):
        raise ConfigurationError(f"Investor {entry.investor_id!r} already exists in {registry.path}")

    next_port = registry.platform.next_frontend_port
    if entry.frontend_port is not None:
        next_port = max(next_port, entry.frontend_port + 1)

    updated = PlatformRegistry(
        path=registry.path,
        platform=replace(registry.platform, next_frontend_port=next_port),
        investors=registry.investors + (entry,),
    )
    save_platform_registry(updated)
    return updated


def patch_registry_investor(
    registry: PlatformRegistry,
    investor_id: str,
    **fields: object,
) -> PlatformRegistry:
    investor_id = validate_investor_id(investor_id)
    updated_rows: list[InvestorRegistryEntry] = []
    found = False
    for entry in registry.investors:
        if entry.investor_id != investor_id:
            updated_rows.append(entry)
            continue
        found = True
        updated_rows.append(replace(entry, **fields))  # type: ignore[arg-type]
    if not found:
        raise ConfigurationError(f"Investor {investor_id!r} not found in {registry.path}")
    updated = PlatformRegistry(path=registry.path, platform=registry.platform, investors=tuple(updated_rows))
    save_platform_registry(updated)
    return updated


def _render_registry(registry: PlatformRegistry) -> str:
    lines = [
        "# Platform registry — ops metadata only (no API secrets).",
        "# Strategy manifest stays in config/investors/<id>/accounts.toml",
        "",
        "[platform]",
    ]
    platform = registry.platform
    if platform.repo_root is not None:
        lines.append(f'repo_root = "{platform.repo_root}"')
    if platform.python_bin:
        lines.append(f'python_bin = "{platform.python_bin}"')
    if platform.domain:
        lines.append(f'domain = "{platform.domain}"')
    if platform.tunnel_name:
        lines.append(f'tunnel_name = "{platform.tunnel_name}"')
    lines.append(f"next_frontend_port = {platform.next_frontend_port}")
    lines.append("")

    for entry in registry.investors:
        lines.extend(_format_investor_block(entry))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _format_investor_block(entry: InvestorRegistryEntry) -> list[str]:
    email = _toml_string(entry.dashboard_email) if entry.dashboard_email else '""'
    hostname = _toml_string(entry.hostname) if entry.hostname else "null"
    port = str(entry.frontend_port) if entry.frontend_port is not None else "null"
    return [
        "[[investors]]",
        f"id = {_toml_string(entry.investor_id)}",
        f"display_name = {_toml_string(entry.display_name)}",
        f"dashboard_email = {email}",
        f"access_method = {_toml_string(entry.access_method)}",
        f"hostname = {hostname}",
        f"frontend_port = {port}",
        f"live_enabled = {'true' if entry.live_enabled else 'false'}",
        f"frontend_enabled = {'true' if entry.frontend_enabled else 'false'}",
    ]


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
