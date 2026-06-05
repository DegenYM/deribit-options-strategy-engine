"""Investor / sub-account env layout helpers.

Layout (canonical)::

    config/shared/.env.defaults         # optional shared fallbacks
    config/shared/strategies/.env.<strategy>  # strategy identity (markets, collaterals, side)
    config/shared/strategies/tiers/<strategy>/.env.<tier>  # low | medium | high risk tuning
    config/investors/<id>/accounts.toml  # manifest (investor id + ≤ few sub-accounts)
    config/investors/<id>/accounts/.env.<slug>  # sub-account credentials + sizing + optional overrides

Strategy profiles: ``config/shared/strategies/.env.<strategy>`` (legacy: repo-root ``.env.<strategy>``).
For files under ``config/investors/<id>/accounts/``, the sub-account env is merged **after** the strategy
profile so keys like ``SHORT_PUT_DELTA_MAX`` can override shared defaults. Legacy single-file ``.env`` runs
keep strategy-after-base precedence.
"""

from __future__ import annotations

import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path

from .exceptions import ConfigurationError

_LEGACY_WARNED: set[str] = set()


def _warn_legacy_path(key: str, message: str) -> None:
    if key in _LEGACY_WARNED:
        return
    _LEGACY_WARNED.add(key)
    warnings.warn(message, DeprecationWarning, stacklevel=3)


def _is_canonical_strategy_profile(profile: Path, repo_root: Path) -> bool:
    profile = profile.resolve()
    strategies_dir = (Path(repo_root).resolve() / CONFIG_STRATEGIES).resolve()
    if profile.parent != strategies_dir:
        return False
    return profile.name.startswith(".env.") and not profile.name.endswith(".example")


def _warn_legacy_strategy_profile(profile: Path, repo_root: Path | None) -> None:
    if repo_root is None or _is_canonical_strategy_profile(profile, repo_root):
        return
    _warn_legacy_path(
        f"strategy_profile:{profile}",
        f"Legacy strategy profile {profile} is deprecated; use config/shared/strategies/.env.<strategy> instead.",
    )


CONFIG_SHARED = Path("config/shared")
CONFIG_STRATEGIES = CONFIG_SHARED / "strategies"
CONFIG_STRATEGY_TIERS = CONFIG_STRATEGIES / "tiers"
CONFIG_INVESTORS = Path("config/investors")
ACCOUNTS_MANIFEST = "accounts.toml"
RISK_TIER_MEDIUM = "medium"
KNOWN_RISK_TIERS = frozenset({"low", RISK_TIER_MEDIUM, "high"})
EXAMPLE_INVESTOR_ID = "_example"
FEE_ACCOUNT_SLUG = "fee"
MAIN_ACCOUNT_SLUG = "main"
ACCOUNT_ROLE_FEE = "fee"
ACCOUNT_ROLE_MAIN = "main"


def normalize_risk_tier(raw: str | None, *, default: str = RISK_TIER_MEDIUM) -> str:
    """Return ``low`` | ``medium`` | ``high`` (default ``medium``)."""
    tier = (raw or default).strip().lower()
    if tier not in KNOWN_RISK_TIERS:
        known = ", ".join(sorted(KNOWN_RISK_TIERS))
        raise ConfigurationError(f"Unknown risk tier {tier!r}; known: {known}")
    return tier


def risk_tier_profile_path(
    repo_root: Path,
    base_strategy: str,
    risk_tier: str,
) -> Path:
    """Path to tier env: ``config/shared/strategies/tiers/<strategy>/.env.<tier>``."""
    tier = normalize_risk_tier(risk_tier)
    return (Path(repo_root).resolve() / CONFIG_STRATEGY_TIERS / base_strategy / f".env.{tier}").resolve()


@dataclass(frozen=True)
class InvestorAccountSpec:
    slug: str
    strategy: str
    env_path: Path
    enabled: bool = True
    live_enabled: bool = True
    display_name: str | None = None
    risk_tier: str = RISK_TIER_MEDIUM


@dataclass(frozen=True)
class InvestorManifest:
    investor_id: str
    display_name: str
    root: Path
    accounts: tuple[InvestorAccountSpec, ...]

    def enabled_accounts(self) -> tuple[InvestorAccountSpec, ...]:
        return tuple(account for account in self.accounts if account.enabled)

    def accounts_without_creds(self) -> tuple[InvestorAccountSpec, ...]:
        """Enabled manifest rows missing ``DERIBIT_CLIENT_ID`` / ``SECRET``."""
        from .config import has_private_creds_for_env

        return tuple(account for account in self.enabled_accounts() if not has_private_creds_for_env(account.env_path))

    def operational_accounts(self) -> tuple[InvestorAccountSpec, ...]:
        """Enabled rows that can call private Deribit APIs (have API credentials)."""
        from .config import has_private_creds_for_env

        return tuple(account for account in self.enabled_accounts() if has_private_creds_for_env(account.env_path))

    def live_operational_accounts(self) -> tuple[InvestorAccountSpec, ...]:
        """Enabled rows with API credentials that should run under live supervision."""
        return tuple(account for account in self.operational_accounts() if account.live_enabled)

    def account_env_files(
        self,
        *,
        require_creds: bool = False,
        require_live: bool = False,
    ) -> tuple[Path, ...]:
        if require_live:
            accounts = self.live_operational_accounts()
        elif require_creds:
            accounts = self.operational_accounts()
        else:
            accounts = self.enabled_accounts()
        return tuple(account.env_path for account in accounts)

    def env_for_slug(self, slug: str) -> Path:
        for account in self.accounts:
            if account.slug == slug:
                return account.env_path
        known = ", ".join(account.slug for account in self.accounts) or "(none)"
        raise ConfigurationError(f"Unknown account slug {slug!r}; known: {known}")


def find_repo_root(start: Path | str | None = None) -> Path | None:
    """Return repository root when ``deribit_engine`` and config layout are present."""
    cur = Path(start or Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    for parent in (cur, *cur.parents):
        if not (parent / "deribit_engine").is_dir():
            continue
        if (parent / CONFIG_STRATEGIES).is_dir() or (parent / ".env.example").is_file():
            return parent
    return None


def investor_dir_for_account(account_env: Path) -> Path | None:
    """``config/investors/<id>`` when ``account_env`` lives under ``.../accounts/``."""
    account_env = account_env.resolve()
    if account_env.parent.name != "accounts":
        return None
    investor_dir = account_env.parent.parent
    if investor_dir.parent.name != "investors":
        return None
    if investor_dir.parent.parent.name != "config":
        return None
    return investor_dir


def account_env_basename(slug: str) -> str:
    """Standard sub-account env filename (IDE / dotenv friendly)."""
    return f".env.{slug.strip()}"


def fee_account_env_path(investor_dir: Path) -> Path:
    """Operator fee-collection sub-account env (not in ``accounts.toml``)."""
    return (investor_dir / "accounts" / account_env_basename(FEE_ACCOUNT_SLUG)).resolve()


def main_account_env_path(investor_dir: Path) -> Path:
    """Main-account env for Deribit subaccount transfers (not in ``accounts.toml``)."""
    return (investor_dir / "accounts" / account_env_basename(MAIN_ACCOUNT_SLUG)).resolve()


def is_main_account_env_path(account_env: Path) -> bool:
    """True when ``account_env`` is the standard main-account env file."""
    return account_env.resolve().name == account_env_basename(MAIN_ACCOUNT_SLUG)


def is_fee_account_env_path(account_env: Path) -> bool:
    """True when ``account_env`` is the standard fee wallet env file."""
    return account_env.resolve().name == account_env_basename(FEE_ACCOUNT_SLUG)


def resolve_account_env_path(investor_dir: Path, slug: str) -> Path:
    """Prefer ``accounts/.env.<slug>``; fall back to legacy ``accounts/<slug>.env``."""
    accounts_dir = investor_dir / "accounts"
    preferred = (accounts_dir / account_env_basename(slug)).resolve()
    legacy = (accounts_dir / f"{slug}.env").resolve()
    if preferred.is_file():
        return preferred
    if legacy.is_file():
        _warn_legacy_path(
            f"account_env:{legacy}",
            f"Legacy account env {legacy.name!r} is deprecated; rename to {preferred.name!r}.",
        )
        return legacy
    return preferred


def resolve_investor_env_path(investor_dir: Path) -> Path | None:
    """Prefer ``.env.investor``; fall back to legacy ``investor.env``."""
    preferred = (investor_dir / ".env.investor").resolve()
    legacy = (investor_dir / "investor.env").resolve()
    if preferred.is_file():
        return preferred
    if legacy.is_file():
        _warn_legacy_path(
            f"investor_env:{legacy}",
            f"Legacy investor env {legacy.name!r} is deprecated; rename to {preferred.name!r}.",
        )
        return legacy
    return None


def investor_id_from_account_env(account_env: Path, *, repo_root: Path | str | None = None) -> str | None:
    """Manifest ``[investor].id`` when ``account_env`` is under ``config/investors/<id>/accounts/``."""
    investor_dir = investor_dir_for_account(account_env)
    if investor_dir is None:
        return None
    root = repo_root or find_repo_root(account_env)
    if root is not None:
        manifest_path = investor_dir / ACCOUNTS_MANIFEST
        if manifest_path.is_file():
            try:
                return load_investor_manifest(investor_dir.name, repo_root=root).investor_id
            except ConfigurationError:
                pass
    return investor_dir.name


def resolve_investor_scope(
    account_envs: tuple[Path, ...] | list[Path],
    *,
    repo_root: Path | str | None = None,
) -> str | None:
    """Return a single investor id when all env files belong to one investor layout.

    Returns ``None`` for legacy single-``.env`` layouts (no ``config/investors/...``).
    Raises :class:`ConfigurationError` when env files span multiple investors.
    """
    if not account_envs:
        return None
    root = repo_root or find_repo_root(account_envs[0])
    investor_ids: set[str] = set()
    for account_env in account_envs:
        investor_id = investor_id_from_account_env(account_env, repo_root=root)
        if investor_id:
            investor_ids.add(investor_id)
    if len(investor_ids) > 1:
        joined = ", ".join(sorted(investor_ids))
        raise ConfigurationError(
            f"Account env files span multiple investors ({joined}); run one frontend or live supervisor per investor."
        )
    return next(iter(investor_ids)) if investor_ids else None


def investor_frontend_ledger_dir(repo_root: Path | str, investor_id: str) -> Path:
    """Per-investor dashboard data (equity jsonl + metrics.db)."""
    return Path(repo_root) / "data" / "frontend_ledger" / investor_id


def investor_live_log_dir(repo_root: Path | str, investor_id: str) -> Path:
    """Per-investor live ``run --live`` supervisor logs."""
    return Path(repo_root) / "logs" / "live" / investor_id


def investor_metrics_db_path(repo_root: Path | str, investor_id: str) -> Path:
    return investor_frontend_ledger_dir(repo_root, investor_id) / "metrics.db"


def default_state_file(investor_id: str, slug: str) -> Path:
    """Canonical ``STATE_FILE`` for a sub-account under ``config/investors/<id>/``."""
    return Path(".state/investors") / investor_id / f"{slug}.json"


def account_slug_from_env_path(account_env: Path) -> str | None:
    """Extract manifest slug from ``accounts/.env.<slug>`` (or legacy name)."""
    account_env = account_env.resolve()
    if account_env.parent.name != "accounts":
        return None
    name = account_env.name
    if name.startswith(".env."):
        slug = name.removeprefix(".env.")
        if slug.endswith(".example"):
            slug = slug[: -len(".example")]
        return slug.strip() or None
    if account_env.suffix == ".env":
        return account_env.stem.strip() or None
    return None


def resolve_investor_dir(repo_root: Path, investor: str | Path) -> Path:
    raw = Path(investor)
    if raw.is_dir():
        return raw.resolve()
    return (repo_root / CONFIG_INVESTORS / str(investor)).resolve()


def load_investor_manifest(
    investor: str | Path,
    *,
    repo_root: Path | str | None = None,
) -> InvestorManifest:
    root = find_repo_root(repo_root or Path.cwd())
    if root is None:
        raise ConfigurationError("Cannot locate repository root (missing deribit_engine/)")
    investor_dir = resolve_investor_dir(root, investor)
    manifest_path = investor_dir / ACCOUNTS_MANIFEST
    if not manifest_path.is_file():
        raise ConfigurationError(f"Missing {manifest_path}")

    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    investor_meta = data.get("investor") or {}
    from .investor_registry import validate_investor_id

    raw_id = str(investor_meta.get("id") or investor_dir.name).strip()
    if not raw_id:
        raise ConfigurationError(f"{manifest_path}: [investor].id is required")
    investor_id = validate_investor_id(raw_id)
    display_name = str(investor_meta.get("display_name") or investor_id).strip()

    accounts: list[InvestorAccountSpec] = []
    for index, row in enumerate(data.get("accounts") or []):
        if not isinstance(row, dict):
            raise ConfigurationError(f"{manifest_path}: accounts[{index}] must be a table")
        slug = str(row.get("slug") or "").strip()
        strategy = str(row.get("strategy") or "").strip()
        if not slug:
            raise ConfigurationError(f"{manifest_path}: accounts[{index}].slug is required")
        if not strategy:
            raise ConfigurationError(f"{manifest_path}: accounts[{index}].strategy is required")
        env_raw = row.get("env_file")
        if env_raw:
            env_path = Path(str(env_raw))
            if not env_path.is_absolute():
                env_path = (investor_dir / env_path).resolve()
        else:
            env_path = resolve_account_env_path(investor_dir, slug)
        enabled = bool(row.get("enabled", True))
        live_enabled = bool(row.get("live_enabled", True))
        account_display = row.get("display_name")
        risk_tier = normalize_risk_tier(row.get("risk_tier"))
        accounts.append(
            InvestorAccountSpec(
                slug=slug,
                strategy=strategy,
                env_path=env_path,
                enabled=enabled,
                live_enabled=live_enabled,
                display_name=str(account_display).strip() if account_display else None,
                risk_tier=risk_tier,
            )
        )

    if not accounts:
        raise ConfigurationError(f"{manifest_path}: at least one [[accounts]] entry is required")

    return InvestorManifest(
        investor_id=investor_id,
        display_name=display_name,
        root=investor_dir,
        accounts=tuple(accounts),
    )


def strategy_profile_search_paths(
    account_env: Path,
    base_strategy: str,
    *,
    repo_root: Path | None = None,
) -> tuple[Path, ...]:
    """Ordered strategy profile candidates (first match wins)."""
    account_env = account_env.resolve()
    root = repo_root or find_repo_root(account_env)
    paths: list[Path] = []
    if root is not None:
        strategies_dir = root / CONFIG_STRATEGIES
        paths.append(strategies_dir / f".env.{base_strategy}")
        paths.append(strategies_dir / f"{base_strategy}.env")
    paths.append(account_env.parent / f".env.{base_strategy}")
    if root is not None:
        paths.append(root / f".env.{base_strategy}")
        if base_strategy == "naked_short":
            paths.append(root / ".env.naked_short_put")
    return tuple(paths)


def _read_env_key(account_env: Path, key: str) -> str:
    if not account_env.is_file():
        return ""
    target = key.strip()
    for line in account_env.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        row_key, value = stripped.split("=", 1)
        if row_key.strip() == target:
            return value.strip()
    return ""


def _read_account_role(account_env: Path) -> str:
    return _read_env_key(account_env, "ACCOUNT_ROLE").lower()


def _is_fee_account_env_file(account_env: Path) -> bool:
    account_env = account_env.resolve()
    if is_fee_account_env_path(account_env):
        role = _read_account_role(account_env)
        return role == "" or role == ACCOUNT_ROLE_FEE
    return _read_account_role(account_env) == ACCOUNT_ROLE_FEE


def _is_main_account_env_file(account_env: Path) -> bool:
    account_env = account_env.resolve()
    if is_main_account_env_path(account_env):
        role = _read_account_role(account_env)
        return role == "" or role == ACCOUNT_ROLE_MAIN
    return _read_account_role(account_env) == ACCOUNT_ROLE_MAIN


def _fee_account_layer_paths(account_env: Path) -> tuple[Path, ...]:
    """Shared defaults + investor env + fee wallet env; no strategy profile."""
    account_env = account_env.resolve()
    root = find_repo_root(account_env)
    layers: list[Path] = []
    if root is not None:
        for name in (".env.defaults", "defaults.env"):
            defaults = root / CONFIG_SHARED / name
            if defaults.is_file():
                if name == "defaults.env":
                    _warn_legacy_path(
                        "defaults:defaults.env",
                        "config/shared/defaults.env is deprecated; rename to config/shared/.env.defaults.",
                    )
                layers.append(defaults)
                break
    investor_dir = investor_dir_for_account(account_env)
    if investor_dir is not None:
        investor_env = resolve_investor_env_path(investor_dir)
        if investor_env is not None:
            layers.append(investor_env)
    if account_env.is_file():
        layers.append(account_env)
    return tuple(layers)


def _main_account_layer_paths(account_env: Path) -> tuple[Path, ...]:
    """Shared defaults + investor env + main account env; no strategy profile."""
    return _fee_account_layer_paths(account_env)


def env_layer_paths(account_env: Path, base_strategy: str) -> tuple[Path, ...]:
    """Env files to merge in order; later paths override earlier keys.

    Investor sub-account envs (under ``config/investors/<id>/accounts/``) are applied **last** so they can
    override shared strategy profiles. All other entry env files keep the profile last (profile overlays
    the entry file), matching legacy single-``.env`` workflows.

    Fee collection envs (``ACCOUNT_ROLE=fee``) skip strategy profiles entirely.
    Main account envs (``ACCOUNT_ROLE=main``) skip strategy profiles entirely.
    """
    account_env = account_env.resolve()
    if _is_fee_account_env_file(account_env):
        return _fee_account_layer_paths(account_env)
    if _is_main_account_env_file(account_env):
        return _main_account_layer_paths(account_env)
    root = find_repo_root(account_env)
    layers: list[Path] = []
    if root is not None:
        for name in (".env.defaults", "defaults.env"):
            defaults = root / CONFIG_SHARED / name
            if defaults.is_file():
                if name == "defaults.env":
                    _warn_legacy_path(
                        "defaults:defaults.env",
                        "config/shared/defaults.env is deprecated; rename to config/shared/.env.defaults.",
                    )
                layers.append(defaults)
                break
    investor_dir = investor_dir_for_account(account_env)
    if investor_dir is not None:
        investor_env = resolve_investor_env_path(investor_dir)
        if investor_env is not None:
            layers.append(investor_env)
    profile = next(
        (path for path in strategy_profile_search_paths(account_env, base_strategy, repo_root=root) if path.is_file()),
        None,
    )
    if profile is not None:
        _warn_legacy_strategy_profile(profile, root)
    risk_tier = normalize_risk_tier(_read_env_key(account_env, "RISK_TIER"))
    tier_profile: Path | None = None
    if root is not None:
        candidate = risk_tier_profile_path(root, base_strategy, risk_tier)
        if candidate.is_file():
            tier_profile = candidate
    sub_account_layout = investor_dir is not None
    if sub_account_layout:
        if profile is not None:
            layers.append(profile)
        if tier_profile is not None:
            layers.append(tier_profile)
        if account_env.is_file():
            layers.append(account_env)
    else:
        if account_env.is_file():
            layers.append(account_env)
        if profile is not None:
            layers.append(profile)
        if tier_profile is not None:
            layers.append(tier_profile)
    return tuple(layers)
