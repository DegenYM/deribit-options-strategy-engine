"""Investor onboarding helpers: init, handoff import, validate, list, launchd/systemd render."""

from __future__ import annotations

import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import DeribitClient
from .config import has_private_creds_for_env, load_config
from .env_layout import (
    CONFIG_INVESTORS,
    EXAMPLE_INVESTOR_ID,
    account_env_basename,
    default_state_file,
    find_repo_root,
    load_investor_manifest,
    resolve_investor_dir,
)
from .exceptions import ConfigurationError
from .fee_snapshot_store import FeeSnapshotStore, fee_ledger_db_path
from .investor_registry import (
    InvestorRegistryEntry,
    PlatformRegistry,
    add_investor_to_registry,
    allocate_frontend_port,
    default_hostname,
    load_platform_registry,
    patch_registry_investor,
    resolve_effective_repo_root,
    validate_investor_id,
)

STRATEGY_BY_SLUG: dict[str, str] = {
    "naked": "naked_short",
    "bull_put": "bull_put_spread",
    "covered_call": "covered_call",
}

DISPLAY_NAME_BY_SLUG: dict[str, str] = {
    "naked": "Naked short (USDC linear)",
    "bull_put": "Bull put spread",
    "covered_call": "Covered call (BTC/ETH inventory)",
}

KNOWN_STRATEGY_SLUGS = frozenset(STRATEGY_BY_SLUG)


@dataclass(frozen=True)
class InitResult:
    investor_id: str
    investor_dir: Path
    strategies: tuple[str, ...]
    frontend_port: int | None
    launchd_paths: tuple[Path, ...]
    systemd_paths: tuple[Path, ...]


@dataclass(frozen=True)
class ValidationIssue:
    level: str  # error | warning
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    investor_id: str
    ok: bool
    issues: tuple[ValidationIssue, ...]
    api_checks: tuple[dict[str, Any], ...]
    hwm_bootstrap: dict[str, Any] | None = None


def parse_strategy_slugs(raw: str) -> tuple[str, ...]:
    slugs: list[str] = []
    for part in raw.split(","):
        slug = part.strip().lower()
        if not slug:
            continue
        if slug not in KNOWN_STRATEGY_SLUGS:
            known = ", ".join(sorted(KNOWN_STRATEGY_SLUGS))
            raise ConfigurationError(f"Unknown strategy slug {slug!r}; known: {known}")
        if slug not in slugs:
            slugs.append(slug)
    if not slugs:
        raise ConfigurationError("At least one strategy slug is required (e.g. naked,covered_call).")
    return tuple(slugs)


def investor_init(
    investor_id: str,
    *,
    strategies: tuple[str, ...],
    display_name: str | None = None,
    dashboard_email: str | None = None,
    deribit_env: str = "mainnet",
    register: bool = True,
    repo_root: Path | None = None,
) -> InitResult:
    investor_id = validate_investor_id(investor_id)
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    registry = load_platform_registry(repo_root=cwd_repo)
    repo = resolve_effective_repo_root(registry, cwd_repo=cwd_repo)

    investor_dir = repo / CONFIG_INVESTORS / investor_id
    if investor_dir.exists():
        raise ConfigurationError(f"Investor directory already exists: {investor_dir}")

    example_dir = repo / CONFIG_INVESTORS / EXAMPLE_INVESTOR_ID
    if not example_dir.is_dir():
        raise ConfigurationError(f"Missing template directory: {example_dir}")

    investor_dir.mkdir(parents=True)
    accounts_dir = investor_dir / "accounts"
    accounts_dir.mkdir()

    label = display_name or investor_id.replace("_", " ").title()
    _write_accounts_toml(investor_dir, investor_id=investor_id, display_name=label, strategies=strategies)
    _copy_investor_env_example(example_dir, investor_dir)

    for slug in strategies:
        _materialize_account_env(
            example_dir,
            investor_dir,
            slug=slug,
            investor_id=investor_id,
            deribit_env=deribit_env,
        )
        _copy_env_example_if_present(example_dir, accounts_dir, slug)

    _write_fee_env(accounts_dir, investor_id=investor_id, deribit_env=deribit_env)

    frontend_port: int | None = None
    if register:
        frontend_port = allocate_frontend_port(registry)
        hostname = default_hostname(investor_id, registry.platform.domain)
        entry = InvestorRegistryEntry(
            investor_id=investor_id,
            display_name=label,
            dashboard_email=dashboard_email,
            access_method="email",
            hostname=hostname,
            frontend_port=frontend_port,
            live_enabled=True,
            frontend_enabled=True,
        )
        add_investor_to_registry(registry, entry)

    launchd_paths = render_launchd_plists(
        investor_id,
        repo_root=repo,
        registry=registry,
        frontend_port=frontend_port,
    )
    systemd_paths = render_systemd_units(
        investor_id,
        repo_root=repo,
        registry=registry,
        frontend_port=frontend_port,
    )

    for sub in ("logs/live", "logs/frontend", "data/fee_ledger"):
        (repo / sub / investor_id).mkdir(parents=True, exist_ok=True)

    return InitResult(
        investor_id=investor_id,
        investor_dir=investor_dir,
        strategies=strategies,
        frontend_port=frontend_port,
        launchd_paths=launchd_paths,
        systemd_paths=systemd_paths,
    )


def import_handoff(
    handoff_path: Path,
    *,
    investor_id: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    data = tomllib.loads(handoff_path.read_text(encoding="utf-8"))
    investor_meta = data.get("investor") or {}
    resolved_id = validate_investor_id(str(investor_id or investor_meta.get("id") or ""))
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    registry = load_platform_registry(repo_root=cwd_repo)
    repo = resolve_effective_repo_root(registry, cwd_repo=cwd_repo)
    investor_dir = resolve_investor_dir(repo, resolved_id)
    if not investor_dir.is_dir():
        raise ConfigurationError(
            f"Investor directory missing: {investor_dir}. Run: ./bot investor init {resolved_id} ..."
        )

    deribit_env = str(investor_meta.get("deribit_env") or "mainnet").strip().lower()
    if deribit_env not in {"mainnet", "testnet", "prod"}:
        raise ConfigurationError(f"handoff [investor].deribit_env must be mainnet or testnet, got {deribit_env!r}")
    if deribit_env == "prod":
        deribit_env = "mainnet"

    updated_slugs: list[str] = []
    for row in data.get("strategies") or []:
        if not isinstance(row, dict):
            raise ConfigurationError("Each [[strategies]] entry must be a table")
        slug = str(row.get("slug") or "").strip().lower()
        if slug not in KNOWN_STRATEGY_SLUGS:
            raise ConfigurationError(f"Unknown strategy slug in handoff: {slug!r}")
        client_id = str(row.get("client_id") or "").strip()
        client_secret = str(row.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            raise ConfigurationError(f"handoff strategies[{slug}] requires client_id and client_secret")

        env_path = investor_dir / "accounts" / account_env_basename(slug)
        if not env_path.is_file():
            raise ConfigurationError(f"Missing {env_path}; run investor init with strategy {slug}")

        updates: dict[str, str] = {
            "DERIBIT_ENV": deribit_env,
            "DERIBIT_CLIENT_ID": client_id,
            "DERIBIT_CLIENT_SECRET": client_secret,
        }
        cap = row.get("reference_capital_usdc")
        if cap is not None:
            updates["REFERENCE_CAPITAL_USDC"] = str(cap).strip()
        _update_env_file(env_path, updates)
        updated_slugs.append(slug)

    fee_meta = data.get("fee") or data.get("fee_account")
    fee_updated = False
    if isinstance(fee_meta, dict):
        fee_id = str(fee_meta.get("client_id") or "").strip()
        fee_secret = str(fee_meta.get("client_secret") or "").strip()
        if fee_id and fee_secret:
            fee_env = investor_dir / "accounts" / account_env_basename("fee")
            if not fee_env.is_file():
                _write_fee_env(investor_dir / "accounts", investor_id=resolved_id, deribit_env=deribit_env)
            _update_env_file(
                fee_env,
                {
                    "DERIBIT_ENV": deribit_env,
                    "DERIBIT_CLIENT_ID": fee_id,
                    "DERIBIT_CLIENT_SECRET": fee_secret,
                },
            )
            fee_updated = True

    email = investor_meta.get("dashboard_email")
    if email:
        patch_registry_investor(
            registry,
            resolved_id,
            dashboard_email=str(email).strip(),
        )

    display = investor_meta.get("display_name")
    if display:
        patch_registry_investor(registry, resolved_id, display_name=str(display).strip())

    return {
        "investor_id": resolved_id,
        "strategies_updated": updated_slugs,
        "fee_updated": fee_updated,
        "deribit_env": deribit_env,
    }


def is_hwm_bootstrapped(store: FeeSnapshotStore, investor_id: str) -> bool:
    return store.load_hwm(investor_id) is not None or store.load_flow_baseline(investor_id) is not None


def bootstrap_initial_hwm(
    investor_id: str,
    *,
    repo_root: Path | None = None,
    force: bool = False,
    snapshot_kind: str = "onboarding",
) -> dict[str, Any]:
    """Derive initial HWM from transaction log (or INITIAL_HWM_NAV_PERF) and write fee ledger."""
    investor_id = validate_investor_id(investor_id)
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    registry = load_platform_registry(repo_root=cwd_repo)
    repo = resolve_effective_repo_root(registry, cwd_repo=cwd_repo)
    store = FeeSnapshotStore(fee_ledger_db_path(repo, investor_id))

    if not force and is_hwm_bootstrapped(store, investor_id):
        baseline = store.load_flow_baseline(investor_id)
        hwm = store.load_hwm(investor_id)
        initial = (
            str(baseline.initial_hwm_nav_perf) if baseline is not None else (str(hwm) if hwm is not None else None)
        )
        return {
            "skipped": True,
            "reason": "already_bootstrapped",
            "initial_hwm_nav_perf": initial,
        }

    from .investor_nav_snapshot import capture_investor_nav, store_nav_capture

    capture = capture_investor_nav(investor_id, repo_root=repo)
    row_id, bootstrap = store_nav_capture(
        capture,
        repo_root=repo,
        snapshot_kind=snapshot_kind,
        bootstrap_hwm=True,
        force_bootstrap=force,
    )
    out: dict[str, Any] = {
        "skipped": False,
        "snapshot_id": row_id,
        "nav_perf": str(capture.nav_perf),
        "aum_mgmt": str(capture.aum_mgmt),
        "hwm_bootstrap": bootstrap,
    }
    if isinstance(bootstrap, dict):
        if bootstrap.get("initial_hwm_nav_perf") is not None:
            out["initial_hwm_nav_perf"] = bootstrap["initial_hwm_nav_perf"]
        if bootstrap.get("report_path"):
            out["report_path"] = bootstrap["report_path"]
        if bootstrap.get("report_pdf_path"):
            out["report_pdf_path"] = bootstrap["report_pdf_path"]
    return out


def validate_investor(
    investor_id: str,
    *,
    check_api: bool = True,
    bootstrap_hwm: bool = True,
    repo_root: Path | None = None,
) -> ValidationResult:
    investor_id = validate_investor_id(investor_id)
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    issues: list[ValidationIssue] = []
    api_checks: list[dict[str, Any]] = []

    registry: PlatformRegistry | None = None
    try:
        registry = load_platform_registry(repo_root=cwd_repo)
    except ConfigurationError as exc:
        issues.append(ValidationIssue("warning", "registry_missing", str(exc)))

    try:
        manifest = load_investor_manifest(investor_id, repo_root=cwd_repo)
    except ConfigurationError as exc:
        return ValidationResult(
            investor_id=investor_id, ok=False, issues=(ValidationIssue("error", "manifest", str(exc)),), api_checks=()
        )

    if registry is not None:
        entry = registry.entry_for(investor_id)
        if entry is None:
            issues.append(
                ValidationIssue(
                    "warning",
                    "registry_entry_missing",
                    f"No [[investors]] row for {investor_id!r} in {registry.path}",
                )
            )
        elif entry.frontend_port is None:
            issues.append(
                ValidationIssue(
                    "warning",
                    "frontend_port_missing",
                    f"Registry entry for {investor_id!r} has no frontend_port",
                )
            )

    if manifest.investor_id != investor_id:
        issues.append(
            ValidationIssue(
                "error",
                "id_mismatch",
                f"accounts.toml [investor].id={manifest.investor_id!r} does not match {investor_id!r}",
            )
        )

    missing_creds = manifest.accounts_without_creds()
    for account in missing_creds:
        issues.append(
            ValidationIssue(
                "error",
                "missing_creds",
                f"Enabled account {account.slug!r} is missing DERIBIT_CLIENT_ID/SECRET",
            )
        )

    for account in manifest.enabled_accounts():
        if not account.env_path.is_file():
            issues.append(
                ValidationIssue(
                    "error",
                    "missing_env_file",
                    f"Missing env file for enabled account {account.slug!r}: {account.env_path}",
                )
            )

    fee_env = manifest.root / "accounts" / account_env_basename("fee")
    if fee_env.is_file() and not has_private_creds_for_env(fee_env):
        issues.append(
            ValidationIssue(
                "warning",
                "fee_creds_missing",
                f"Fee env present but missing credentials: {fee_env}",
            )
        )
    if fee_env.is_file():
        try:
            fee_cfg = load_config(fee_env, require_private=False)
            if not fee_cfg.is_fee_collection_account:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "fee_role_missing",
                        f"Add ACCOUNT_ROLE=fee to {fee_env} (fee wallet must not load strategy profiles)",
                    )
                )
        except ConfigurationError as exc:
            issues.append(
                ValidationIssue(
                    "error",
                    "fee_env_invalid",
                    f"Fee env failed to load: {exc}",
                )
            )

    if check_api:
        for account in manifest.operational_accounts():
            slug = account.slug
            try:
                config = load_config(account.env_path, require_private=True)
                client = DeribitClient(config)
                summaries = client.get_account_summaries(extended=False)
                api_checks.append(
                    {
                        "slug": slug,
                        "env": config.env,
                        "ok": True,
                        "account_count": len(summaries),
                    }
                )
            except Exception as exc:
                api_checks.append({"slug": slug, "ok": False, "error": str(exc)})
                issues.append(
                    ValidationIssue(
                        "error",
                        "api_auth_failed",
                        f"Deribit API check failed for {slug!r}: {exc}",
                    )
                )

        if fee_env.is_file() and has_private_creds_for_env(fee_env):
            try:
                config = load_config(fee_env, require_private=True)
                client = DeribitClient(config)
                summaries = client.get_account_summaries(extended=False)
                api_checks.append(
                    {
                        "slug": "fee",
                        "env": config.env,
                        "ok": True,
                        "account_count": len(summaries),
                    }
                )
            except Exception as exc:
                api_checks.append({"slug": "fee", "ok": False, "error": str(exc)})
                issues.append(
                    ValidationIssue(
                        "error",
                        "fee_api_auth_failed",
                        f"Deribit API check failed for fee account: {exc}",
                    )
                )

    has_errors = any(issue.level == "error" for issue in issues)
    hwm_bootstrap: dict[str, Any] | None = None
    if bootstrap_hwm and check_api and not has_errors:
        try:
            hwm_bootstrap = bootstrap_initial_hwm(investor_id, repo_root=cwd_repo)
        except Exception as exc:
            issues = list(issues)
            issues.append(
                ValidationIssue(
                    "error",
                    "hwm_bootstrap_failed",
                    f"Initial HWM bootstrap failed: {exc}",
                )
            )
            has_errors = True

    return ValidationResult(
        investor_id=investor_id,
        ok=not has_errors,
        issues=tuple(issues),
        api_checks=tuple(api_checks),
        hwm_bootstrap=hwm_bootstrap,
    )


def list_investors(*, repo_root: Path | None = None) -> list[dict[str, Any]]:
    cwd_repo = repo_root or find_repo_root(Path.cwd())
    if cwd_repo is None:
        raise ConfigurationError("Cannot locate repository root")

    rows: list[dict[str, Any]] = []
    registry: PlatformRegistry | None = None
    try:
        registry = load_platform_registry(repo_root=cwd_repo)
    except ConfigurationError:
        registry = None

    registry_ids = set(registry.investor_ids()) if registry else set()
    disk_ids = {
        path.name
        for path in (cwd_repo / CONFIG_INVESTORS).iterdir()
        if path.is_dir() and path.name not in {EXAMPLE_INVESTOR_ID} and not path.name.startswith(".")
    }
    all_ids = sorted(registry_ids | disk_ids)

    for investor_id in all_ids:
        row: dict[str, Any] = {"investor_id": investor_id}
        if registry is not None:
            entry = registry.entry_for(investor_id)
            if entry is not None:
                row.update(
                    {
                        "display_name": entry.display_name,
                        "dashboard_email": entry.dashboard_email,
                        "hostname": entry.hostname,
                        "frontend_port": entry.frontend_port,
                        "live_enabled": entry.live_enabled,
                        "frontend_enabled": entry.frontend_enabled,
                    }
                )
        manifest_path = cwd_repo / CONFIG_INVESTORS / investor_id / "accounts.toml"
        if manifest_path.is_file():
            manifest = load_investor_manifest(investor_id, repo_root=cwd_repo)
            row["accounts"] = [
                {
                    "slug": account.slug,
                    "strategy": account.strategy,
                    "enabled": account.enabled,
                    "has_creds": has_private_creds_for_env(account.env_path),
                }
                for account in manifest.accounts
            ]
        else:
            row["accounts"] = []
        rows.append(row)
    return rows


def _render_template_file(template_path: Path, replacements: dict[str, str]) -> str:
    if not template_path.is_file():
        raise ConfigurationError(f"Missing template: {template_path}")
    text = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def _investor_service_replacements(
    investor_id: str,
    *,
    repo_root: Path,
    python_bin: str,
    frontend_port: int,
) -> dict[str, str]:
    return {
        "__LABEL__": f"com.deribit.live.{investor_id}",
        "__REPO_ROOT__": str(repo_root),
        "__PYTHON_BIN__": python_bin,
        "__INVESTOR_ID__": investor_id,
        "__FRONTEND_PORT__": str(frontend_port),
    }


def render_launchd_plists(
    investor_id: str,
    *,
    repo_root: Path,
    registry: PlatformRegistry | None = None,
    frontend_port: int | None = None,
) -> tuple[Path, ...]:
    investor_id = validate_investor_id(investor_id)
    if registry is None:
        registry = load_platform_registry(repo_root=repo_root)
    entry = registry.entry_for(investor_id)
    port = frontend_port or (entry.frontend_port if entry else None) or 8765
    python_bin = registry.platform.python_bin or "python3"
    replacements = _investor_service_replacements(
        investor_id,
        repo_root=repo_root,
        python_bin=python_bin,
        frontend_port=port,
    )

    out_dir = repo_root / "config/platform/generated/launchd"
    out_dir.mkdir(parents=True, exist_ok=True)

    templates = {
        f"com.deribit.live.{investor_id}.plist": (
            repo_root / "config/launchd/com.deribit.live.plist.template",
            replacements,
        ),
        f"com.deribit.frontend.{investor_id}.plist": (
            repo_root / "config/launchd/com.deribit.frontend.plist.template",
            replacements,
        ),
    }

    written: list[Path] = []
    for filename, (template_path, file_replacements) in templates.items():
        text = _render_template_file(template_path, file_replacements)
        out_path = out_dir / filename
        out_path.write_text(text, encoding="utf-8")
        written.append(out_path)
    return tuple(written)


def render_systemd_units(
    investor_id: str,
    *,
    repo_root: Path,
    registry: PlatformRegistry | None = None,
    frontend_port: int | None = None,
) -> tuple[Path, ...]:
    investor_id = validate_investor_id(investor_id)
    if registry is None:
        registry = load_platform_registry(repo_root=repo_root)
    entry = registry.entry_for(investor_id)
    port = frontend_port or (entry.frontend_port if entry else None) or 8765
    python_bin = registry.platform.python_bin or "python3"
    replacements = _investor_service_replacements(
        investor_id,
        repo_root=repo_root,
        python_bin=python_bin,
        frontend_port=port,
    )

    out_dir = repo_root / "config/platform/generated/systemd"
    out_dir.mkdir(parents=True, exist_ok=True)

    templates = {
        f"com.deribit.live.{investor_id}.service": (
            repo_root / "config/systemd/com.deribit.live.service.template",
            replacements,
        ),
        f"com.deribit.frontend.{investor_id}.service": (
            repo_root / "config/systemd/com.deribit.frontend.service.template",
            replacements,
        ),
    }

    written: list[Path] = []
    for filename, (template_path, file_replacements) in templates.items():
        text = _render_template_file(template_path, file_replacements)
        out_path = out_dir / filename
        out_path.write_text(text, encoding="utf-8")
        written.append(out_path)
    return tuple(written)


def _write_accounts_toml(
    investor_dir: Path,
    *,
    investor_id: str,
    display_name: str,
    strategies: tuple[str, ...],
) -> None:
    lines = [
        "# Generated by: ./bot investor init",
        "# Strategy manifest (ops metadata is in config/platform/registry.toml)",
        "",
        "[investor]",
        f'id = "{investor_id}"',
        f'display_name = "{display_name}"',
        "",
    ]
    for slug in strategies:
        strategy = STRATEGY_BY_SLUG[slug]
        title = DISPLAY_NAME_BY_SLUG[slug]
        lines.extend(
            [
                "[[accounts]]",
                f'slug = "{slug}"',
                f'strategy = "{strategy}"',
                f'display_name = "{title}"',
                "enabled = true",
                "",
            ]
        )
    (investor_dir / "accounts.toml").write_text("\n".join(lines), encoding="utf-8")


def _copy_investor_env_example(example_dir: Path, investor_dir: Path) -> None:
    src = example_dir / ".env.investor.example"
    if src.is_file():
        shutil.copy2(src, investor_dir / ".env.investor.example")
        shutil.copy2(src, investor_dir / ".env.investor")


def _copy_env_example_if_present(example_dir: Path, accounts_dir: Path, slug: str) -> None:
    src = example_dir / "accounts" / f".env.{slug}.example"
    if src.is_file():
        shutil.copy2(src, accounts_dir / f".env.{slug}.example")


def _materialize_account_env(
    example_dir: Path,
    investor_dir: Path,
    *,
    slug: str,
    investor_id: str,
    deribit_env: str,
) -> None:
    example_env = example_dir / "accounts" / f".env.{slug}.example"
    dest = investor_dir / "accounts" / account_env_basename(slug)
    strategy = STRATEGY_BY_SLUG[slug]
    if example_env.is_file():
        text = example_env.read_text(encoding="utf-8")
        text = _substitute_placeholders(
            text,
            investor_id=investor_id,
            slug=slug,
            strategy=strategy,
            deribit_env=deribit_env,
        )
        dest.write_text(text, encoding="utf-8")
    else:
        dest.write_text(
            "\n".join(
                [
                    f"DERIBIT_ENV={deribit_env}",
                    "DERIBIT_CLIENT_ID=",
                    "DERIBIT_CLIENT_SECRET=",
                    f"OPTION_STRATEGY={strategy}",
                    f"ORDER_LABEL_PREFIX={investor_id}_{slug}",
                    f"STATE_FILE={default_state_file(investor_id, slug)}",
                    "REFERENCE_CAPITAL_USDC=1000",
                    "TARGET_PORTFOLIO_APR=0.25",
                    "TOP_N=5",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def _substitute_placeholders(
    text: str,
    *,
    investor_id: str,
    slug: str,
    strategy: str,
    deribit_env: str,
) -> str:
    state = str(default_state_file(investor_id, slug))
    replacements = {
        "demo": investor_id,
        "DERIBIT_ENV=testnet": f"DERIBIT_ENV={deribit_env}",
        f"ORDER_LABEL_PREFIX=demo_{slug}": f"ORDER_LABEL_PREFIX={investor_id}_{slug}",
        f"STATE_FILE=.state/investors/demo/{slug}.json": f"STATE_FILE={state}",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if f"OPTION_STRATEGY={strategy}" not in text:
        text = re.sub(r"OPTION_STRATEGY=.*", f"OPTION_STRATEGY={strategy}", text)
    return text


def _write_fee_env(accounts_dir: Path, *, investor_id: str, deribit_env: str) -> None:
    path = accounts_dir / account_env_basename("fee")
    body = "\n".join(
        [
            "# Operator fee-collection sub-account (Deribit sub-account name: fee).",
            "# Not listed in accounts.toml — excluded from live supervisor and frontend aggregation.",
            "# API: Account=read, Wallet=none, Trade=none. Read-only reconciliation sub-account.",
            "# Do NOT: ./bot run --env-file .../.env.fee",
            "ACCOUNT_ROLE=fee",
            f"DERIBIT_ENV={deribit_env}",
            "DERIBIT_CLIENT_ID=",
            "DERIBIT_CLIENT_SECRET=",
            f"ORDER_LABEL_PREFIX={investor_id}_fee",
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")
    shutil.copy2(path, accounts_dir / ".env.fee.example")


def _update_env_file(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
