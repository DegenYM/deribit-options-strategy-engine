"""Sync Cloudflare Tunnel ingress from ``registry.toml``.

This tunnel is **remotely managed**: Cloudflare dashboard/API config overrides
local ``~/.cloudflared/config.yml`` ingress. Both must stay in sync, or new
hostnames return HTTP 404 (catch-all).
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cloudflared_launchd import cloudflared_config_path, resolve_cloudflared_bin
from .exceptions import ConfigurationError
from .investor_registry import (
    InvestorRegistryEntry,
    PlatformRegistry,
    load_platform_registry,
    resolve_effective_repo_root,
    validate_investor_id,
)

_CERT_PEM = Path.home() / ".cloudflared" / "cert.pem"
_CF_API = "https://api.cloudflare.com/client/v4"


@dataclass(frozen=True)
class IngressRule:
    hostname: str
    service: str
    investor_id: str | None = None


@dataclass(frozen=True)
class ProvisionResult:
    ok: bool
    tunnel_id: str | None
    tunnel_name: str | None
    rules: tuple[IngressRule, ...]
    local_config_path: str | None
    local_changed: bool
    remote_changed: bool
    remote_version: int | None
    dns: tuple[dict[str, Any], ...]
    access_checklist: tuple[dict[str, str], ...]
    messages: tuple[str, ...]
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tunnel_id": self.tunnel_id,
            "tunnel_name": self.tunnel_name,
            "rules": [
                {
                    "hostname": rule.hostname,
                    "service": rule.service,
                    "investor_id": rule.investor_id,
                }
                for rule in self.rules
            ],
            "local_config_path": self.local_config_path,
            "local_changed": self.local_changed,
            "remote_changed": self.remote_changed,
            "remote_version": self.remote_version,
            "dns": list(self.dns),
            "access_checklist": list(self.access_checklist),
            "messages": list(self.messages),
            "dry_run": self.dry_run,
        }


def cloudflare_cert_path() -> Path:
    return _CERT_PEM


def load_argo_tunnel_login() -> dict[str, str]:
    path = cloudflare_cert_path()
    if not path.is_file():
        raise ConfigurationError(f"Missing {path}. Run: cloudflared tunnel login")
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END ARGO TUNNEL TOKEN-----",
        text,
        re.S,
    )
    if not match:
        raise ConfigurationError(f"{path}: expected ARGO TUNNEL TOKEN block")
    raw = match.group(1).replace("\n", "").strip()
    pad = "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.b64decode(raw + pad))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConfigurationError(f"{path}: cannot decode tunnel login token") from exc
    account_id = str(payload.get("accountID") or "").strip()
    api_token = str(payload.get("apiToken") or "").strip()
    zone_id = str(payload.get("zoneID") or "").strip()
    if not account_id or not api_token:
        raise ConfigurationError(f"{path}: login token missing accountID/apiToken")
    return {"accountID": account_id, "apiToken": api_token, "zoneID": zone_id}


def parse_local_tunnel_identity(config_text: str) -> tuple[str | None, str | None]:
    tunnel_id = None
    credentials_file = None
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith("tunnel:"):
            tunnel_id = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("credentials-file:"):
            credentials_file = stripped.split(":", 1)[1].strip().strip("\"'")
    return tunnel_id, credentials_file


def render_local_config_yml(
    *,
    tunnel_id: str,
    credentials_file: str,
    rules: list[IngressRule] | tuple[IngressRule, ...],
) -> str:
    lines = [
        f"tunnel: {tunnel_id}",
        f"credentials-file: {credentials_file}",
        "",
        "ingress:",
    ]
    for rule in rules:
        lines.append(f"  - hostname: {rule.hostname}")
        lines.append(f"    service: {rule.service}")
        lines.append("")
    lines.append("  - service: http_status:404")
    lines.append("")
    return "\n".join(lines)


def collect_ingress_rules(
    registry: PlatformRegistry,
    *,
    investor_id: str | None = None,
    include_disabled: bool = False,
) -> list[IngressRule]:
    selected: list[InvestorRegistryEntry]
    if investor_id:
        normalized = validate_investor_id(investor_id)
        entry = registry.entry_for(normalized)
        if entry is None:
            raise ConfigurationError(f"Investor {normalized!r} not found in registry.toml")
        selected = [entry]
    else:
        selected = list(registry.investors)

    rules: list[IngressRule] = []
    errors: list[str] = []
    for entry in selected:
        if not include_disabled and not entry.frontend_enabled:
            continue
        if not entry.hostname:
            errors.append(f"{entry.investor_id}: missing hostname")
            continue
        if entry.frontend_port is None:
            errors.append(f"{entry.investor_id}: missing frontend_port")
            continue
        host = entry.hostname.strip().lower()
        if host.endswith(".example.com") or "example.com" in host:
            errors.append(
                f"{entry.investor_id}: hostname {entry.hostname!r} still uses example.com — "
                "set a real hostname in registry.toml"
            )
            continue
        rules.append(
            IngressRule(
                hostname=host,
                service=f"http://127.0.0.1:{int(entry.frontend_port)}",
                investor_id=entry.investor_id,
            )
        )

    if investor_id and not rules and not errors:
        raise ConfigurationError(
            f"Investor {investor_id!r} has frontend_enabled=false; pass include_disabled or enable it"
        )
    if errors:
        raise ConfigurationError("Cannot build tunnel ingress:\n- " + "\n- ".join(errors))
    if not rules:
        raise ConfigurationError("No frontend_enabled investors with hostname + frontend_port in registry.toml")

    # Stable order: by port then hostname
    rules.sort(key=lambda rule: (int(rule.service.rsplit(":", 1)[-1]), rule.hostname))
    return rules


def merge_ingress_for_single_investor(
    existing_remote_ingress: list[dict[str, Any]],
    *,
    new_rule: IngressRule,
) -> list[dict[str, Any]]:
    """Replace/add one hostname; preserve unrelated remote hostnames; keep catch-all last."""
    catch_all: dict[str, Any] | None = None
    merged: list[dict[str, Any]] = []
    replaced = False
    for raw in existing_remote_ingress:
        hostname = str(raw.get("hostname") or "").strip().lower()
        service = str(raw.get("service") or "").strip()
        if not hostname:
            catch_all = {"service": service or "http_status:404", "originRequest": raw.get("originRequest") or {}}
            continue
        if hostname == new_rule.hostname:
            merged.append(
                {
                    "hostname": new_rule.hostname,
                    "service": new_rule.service,
                    "originRequest": raw.get("originRequest") or {},
                }
            )
            replaced = True
            continue
        merged.append(
            {
                "hostname": hostname,
                "service": service,
                "originRequest": raw.get("originRequest") or {},
            }
        )
    if not replaced:
        merged.append(
            {
                "hostname": new_rule.hostname,
                "service": new_rule.service,
                "originRequest": {},
            }
        )
    merged.append(catch_all or {"service": "http_status:404", "originRequest": {}})
    return merged


def rules_to_remote_ingress(rules: list[IngressRule] | tuple[IngressRule, ...]) -> list[dict[str, Any]]:
    ingress = [
        {
            "hostname": rule.hostname,
            "service": rule.service,
            "originRequest": {},
        }
        for rule in rules
    ]
    ingress.append({"service": "http_status:404", "originRequest": {}})
    return ingress


def cf_api_request(
    method: str,
    url: str,
    *,
    api_token: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ConfigurationError(f"Cloudflare API {method} {url} failed HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ConfigurationError(f"Cloudflare API {method} {url} failed: {exc}") from exc
    if not payload.get("success", False):
        raise ConfigurationError(f"Cloudflare API error: {payload.get('errors')}")
    return payload


def resolve_tunnel_id(
    *,
    tunnel_name: str,
    account_id: str,
    api_token: str,
    local_config: Path | None = None,
) -> str:
    if local_config and local_config.is_file():
        tunnel_id, _ = parse_local_tunnel_identity(local_config.read_text(encoding="utf-8"))
        if tunnel_id:
            return tunnel_id

    from urllib.parse import quote

    url = f"{_CF_API}/accounts/{account_id}/cfd_tunnel?name={quote(tunnel_name)}&is_deleted=false"
    payload = cf_api_request("GET", url, api_token=api_token)
    rows = payload.get("result") or []
    if not rows:
        raise ConfigurationError(f"Tunnel {tunnel_name!r} not found in Cloudflare account")
    tunnel_id = str(rows[0].get("id") or "").strip()
    if not tunnel_id:
        raise ConfigurationError(f"Tunnel {tunnel_name!r} response missing id")
    return tunnel_id


def get_remote_tunnel_config(
    *,
    account_id: str,
    tunnel_id: str,
    api_token: str,
) -> dict[str, Any]:
    url = f"{_CF_API}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    return cf_api_request("GET", url, api_token=api_token)


def put_remote_tunnel_config(
    *,
    account_id: str,
    tunnel_id: str,
    api_token: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    url = f"{_CF_API}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    return cf_api_request("PUT", url, api_token=api_token, body={"config": config})


def ensure_dns_cname(
    *,
    tunnel_name: str,
    hostname: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create/overwrite DNS route via cloudflared CLI (uses cert.pem)."""
    if dry_run:
        return {
            "hostname": hostname,
            "ok": True,
            "changed": False,
            "dry_run": True,
            "message": f"would run: cloudflared tunnel route dns {tunnel_name} {hostname}",
        }
    bin_path = resolve_cloudflared_bin()
    completed = subprocess.run(
        [bin_path, "tunnel", "route", "dns", tunnel_name, hostname],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (completed.stdout or "") + (completed.stderr or "")
    already = "already exists" in combined.lower() or "cname already exists" in combined.lower()
    ok = completed.returncode == 0 or already
    return {
        "hostname": hostname,
        "ok": ok,
        "changed": completed.returncode == 0 and not already,
        "dry_run": False,
        "message": combined.strip() or ("ok" if ok else f"exit {completed.returncode}"),
    }


def access_checklist_rows(
    registry: PlatformRegistry,
    *,
    investor_id: str | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    entries = registry.investors
    if investor_id:
        entry = registry.entry_for(validate_investor_id(investor_id))
        entries = (entry,) if entry else ()
    for entry in entries:
        if not entry.frontend_enabled or not entry.hostname:
            continue
        rows.append(
            {
                "investor_id": entry.investor_id,
                "hostname": entry.hostname,
                "dashboard_email": entry.dashboard_email or "",
                "step": (
                    "Zero Trust → Access → Applications → Add self-hosted app; "
                    f"domain={entry.hostname}; Allow email={entry.dashboard_email or '(set dashboard_email)'}"
                ),
            }
        )
    return rows


def provision_tunnel(
    *,
    repo_root: Path | None = None,
    investor_id: str | None = None,
    dry_run: bool = False,
    sync_local: bool = True,
    sync_remote: bool = True,
    sync_dns: bool = True,
    include_disabled: bool = False,
) -> ProvisionResult:
    cwd_repo = repo_root
    registry = load_platform_registry(repo_root=cwd_repo)
    resolve_effective_repo_root(registry, cwd_repo=cwd_repo)

    tunnel_name = (registry.platform.tunnel_name or "").strip()
    if not tunnel_name:
        raise ConfigurationError("registry.toml [platform].tunnel_name is required")

    messages: list[str] = []
    if investor_id:
        # Single-investor mode still rebuilds full local ingress from all enabled rows,
        # but only ensures DNS for the target and merges that hostname into remote.
        all_rules = collect_ingress_rules(registry, include_disabled=include_disabled)
        target_rules = collect_ingress_rules(
            registry,
            investor_id=investor_id,
            include_disabled=True,
        )
        target = target_rules[0]
        local_rules = (
            all_rules
            if any(r.hostname == target.hostname for r in all_rules)
            else sorted(
                [*all_rules, target],
                key=lambda rule: (int(rule.service.rsplit(":", 1)[-1]), rule.hostname),
            )
        )
        dns_hosts = [target.hostname]
        messages.append(f"Scoped to investor {target.investor_id}: {target.hostname} → {target.service}")
    else:
        local_rules = collect_ingress_rules(registry, include_disabled=include_disabled)
        target = None
        dns_hosts = [rule.hostname for rule in local_rules]
        messages.append(f"Provisioning {len(local_rules)} hostname(s) from registry.toml")

    local_path = cloudflared_config_path()
    tunnel_id: str | None = None
    credentials_file: str | None = None
    if local_path.is_file():
        tunnel_id, credentials_file = parse_local_tunnel_identity(local_path.read_text(encoding="utf-8"))

    login: dict[str, str] | None = None
    if sync_remote or tunnel_id is None:
        login = load_argo_tunnel_login()
        tunnel_id = resolve_tunnel_id(
            tunnel_name=tunnel_name,
            account_id=login["accountID"],
            api_token=login["apiToken"],
            local_config=local_path if local_path.is_file() else None,
        )

    if credentials_file is None:
        credentials_file = str(Path.home() / ".cloudflared" / f"{tunnel_id}.json")

    local_changed = False
    if sync_local:
        desired = render_local_config_yml(
            tunnel_id=tunnel_id or "",
            credentials_file=credentials_file,
            rules=local_rules,
        )
        current = local_path.read_text(encoding="utf-8") if local_path.is_file() else ""
        local_changed = current != desired
        if dry_run:
            messages.append(f"local config {'would update' if local_changed else 'unchanged'}: {local_path}")
        elif local_changed:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(desired, encoding="utf-8")
            messages.append(f"Wrote local config: {local_path}")
        else:
            messages.append(f"Local config unchanged: {local_path}")

    remote_changed = False
    remote_version: int | None = None
    if sync_remote:
        assert login is not None and tunnel_id is not None
        current_remote = get_remote_tunnel_config(
            account_id=login["accountID"],
            tunnel_id=tunnel_id,
            api_token=login["apiToken"],
        )
        result = current_remote.get("result") or {}
        existing_config = dict(result.get("config") or {})
        existing_ingress = list(existing_config.get("ingress") or [])
        remote_version = result.get("version")

        if investor_id and target is not None:
            desired_ingress = merge_ingress_for_single_investor(existing_ingress, new_rule=target)
        else:
            desired_ingress = rules_to_remote_ingress(local_rules)

        desired_config = dict(existing_config)
        desired_config["ingress"] = desired_ingress
        desired_config.setdefault("warp-routing", existing_config.get("warp-routing") or {"enabled": False})

        def _norm(ingress: list[dict[str, Any]]) -> list[tuple[str, str]]:
            out: list[tuple[str, str]] = []
            for row in ingress:
                out.append((str(row.get("hostname") or ""), str(row.get("service") or "")))
            return out

        remote_changed = _norm(existing_ingress) != _norm(desired_ingress)
        if dry_run:
            messages.append(
                f"remote tunnel config {'would update' if remote_changed else 'unchanged'} "
                f"(current version={remote_version})"
            )
        elif remote_changed:
            put_payload = put_remote_tunnel_config(
                account_id=login["accountID"],
                tunnel_id=tunnel_id,
                api_token=login["apiToken"],
                config=desired_config,
            )
            remote_version = (put_payload.get("result") or {}).get("version")
            messages.append(
                f"Updated remote tunnel ingress (version={remote_version}). "
                "cloudflared connector picks this up within seconds."
            )
        else:
            messages.append(f"Remote tunnel config unchanged (version={remote_version})")

    dns_results: list[dict[str, Any]] = []
    if sync_dns:
        for hostname in dns_hosts:
            dns_results.append(ensure_dns_cname(tunnel_name=tunnel_name, hostname=hostname, dry_run=dry_run))
            messages.append(f"DNS {hostname}: {dns_results[-1]['message']}")

    checklist = access_checklist_rows(registry, investor_id=investor_id)
    if checklist:
        messages.append(
            "Access still manual: create/update one Zero Trust Access Application per hostname (see access_checklist)."
        )

    ok = all(item.get("ok", True) for item in dns_results)
    return ProvisionResult(
        ok=ok,
        tunnel_id=tunnel_id,
        tunnel_name=tunnel_name,
        rules=tuple(local_rules),
        local_config_path=str(local_path),
        local_changed=local_changed,
        remote_changed=remote_changed,
        remote_version=remote_version if isinstance(remote_version, int) else None,
        dns=tuple(dns_results),
        access_checklist=tuple(checklist),
        messages=tuple(messages),
        dry_run=dry_run,
    )
