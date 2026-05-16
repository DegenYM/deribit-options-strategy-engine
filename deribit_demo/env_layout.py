"""Investor / sub-account env layout helpers.

Layout (canonical)::

    config/shared/defaults.env          # optional shared fallbacks
    config/shared/strategies/.env.<strategy>  # strategy tuning (no secrets)
    config/investors/<id>/accounts.toml  # manifest (investor id + ≤ few sub-accounts)
    config/investors/<id>/accounts/.env.<slug>  # sub-account credentials + sizing + optional overrides

Strategy profiles: ``config/shared/strategies/.env.<strategy>`` (legacy: repo-root ``.env.<strategy>``).
For files under ``config/investors/<id>/accounts/``, the sub-account env is merged **after** the strategy
profile so keys like ``SHORT_PUT_DELTA_MAX`` can override shared defaults. Legacy single-file ``.env`` runs
keep strategy-after-base precedence.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .exceptions import ConfigurationError

CONFIG_SHARED = Path("config/shared")
CONFIG_STRATEGIES = CONFIG_SHARED / "strategies"
CONFIG_INVESTORS = Path("config/investors")
ACCOUNTS_MANIFEST = "accounts.toml"
EXAMPLE_INVESTOR_ID = "_example"


@dataclass(frozen=True)
class InvestorAccountSpec:
    slug: str
    strategy: str
    env_path: Path
    enabled: bool = True
    display_name: str | None = None


@dataclass(frozen=True)
class InvestorManifest:
    investor_id: str
    display_name: str
    root: Path
    accounts: tuple[InvestorAccountSpec, ...]

    def enabled_accounts(self) -> tuple[InvestorAccountSpec, ...]:
        return tuple(account for account in self.accounts if account.enabled)

    def account_env_files(self) -> tuple[Path, ...]:
        return tuple(account.env_path for account in self.enabled_accounts())

    def env_for_slug(self, slug: str) -> Path:
        for account in self.accounts:
            if account.slug == slug:
                return account.env_path
        known = ", ".join(account.slug for account in self.accounts) or "(none)"
        raise ConfigurationError(f"Unknown account slug {slug!r}; known: {known}")


def find_repo_root(start: Path | str | None = None) -> Path | None:
    """Return repository root when ``deribit_demo`` and config layout are present."""
    cur = Path(start or Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    for parent in (cur, *cur.parents):
        if not (parent / "deribit_demo").is_dir():
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


def resolve_account_env_path(investor_dir: Path, slug: str) -> Path:
    """Prefer ``accounts/.env.<slug>``; fall back to legacy ``accounts/<slug>.env``."""
    accounts_dir = investor_dir / "accounts"
    preferred = (accounts_dir / account_env_basename(slug)).resolve()
    legacy = (accounts_dir / f"{slug}.env").resolve()
    if preferred.is_file():
        return preferred
    if legacy.is_file():
        return legacy
    return preferred


def resolve_investor_env_path(investor_dir: Path) -> Path | None:
    """Prefer ``.env.investor``; fall back to legacy ``investor.env``."""
    preferred = (investor_dir / ".env.investor").resolve()
    legacy = (investor_dir / "investor.env").resolve()
    if preferred.is_file():
        return preferred
    if legacy.is_file():
        return legacy
    return None


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
        raise ConfigurationError("Cannot locate repository root (missing deribit_demo/)")
    investor_dir = resolve_investor_dir(root, investor)
    manifest_path = investor_dir / ACCOUNTS_MANIFEST
    if not manifest_path.is_file():
        raise ConfigurationError(f"Missing {manifest_path}")

    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    investor_meta = data.get("investor") or {}
    investor_id = str(investor_meta.get("id") or investor_dir.name).strip()
    if not investor_id:
        raise ConfigurationError(f"{manifest_path}: [investor].id is required")
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
        account_display = row.get("display_name")
        accounts.append(
            InvestorAccountSpec(
                slug=slug,
                strategy=strategy,
                env_path=env_path,
                enabled=enabled,
                display_name=str(account_display).strip() if account_display else None,
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


def env_layer_paths(account_env: Path, base_strategy: str) -> tuple[Path, ...]:
    """Env files to merge in order; later paths override earlier keys.

    Investor sub-account envs (under ``config/investors/<id>/accounts/``) are applied **last** so they can
    override shared strategy profiles. All other entry env files keep the profile last (profile overlays
    the entry file), matching legacy single-``.env`` workflows.
    """
    account_env = account_env.resolve()
    root = find_repo_root(account_env)
    layers: list[Path] = []
    if root is not None:
        for name in (".env.defaults", "defaults.env"):
            defaults = root / CONFIG_SHARED / name
            if defaults.is_file():
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
    sub_account_layout = investor_dir is not None
    if sub_account_layout:
        if profile is not None:
            layers.append(profile)
        if account_env.is_file():
            layers.append(account_env)
    else:
        if account_env.is_file():
            layers.append(account_env)
        if profile is not None:
            layers.append(profile)
    return tuple(layers)
