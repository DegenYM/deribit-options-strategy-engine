"""Locked Covered Call parameter catalog (not user-editable)."""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from .settings import ALLOWED_TIERS

CATALOG_DIR = Path(__file__).resolve().parent.parent / "catalog"


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {str(k): str(v) for k, v in dotenv_values(path).items() if v is not None}


def merged_catalog_env(risk_tier: str) -> dict[str, str]:
    tier = risk_tier.strip().lower()
    if tier not in ALLOWED_TIERS:
        raise ValueError(f"unknown risk tier {risk_tier!r}")
    values: dict[str, str] = {}
    values.update(_parse_env_file(CATALOG_DIR / "saas_defaults.env"))
    values.update(_parse_env_file(CATALOG_DIR / "strategy.env"))
    values.update(_parse_env_file(CATALOG_DIR / "tiers" / f"{tier}.env"))
    values["OPTION_STRATEGY"] = "covered_call"
    values["RISK_TIER"] = tier
    values["SHORT_OPTION_SIDE"] = "call"
    values["OPTION_MARKETS_PROFILE"] = "inverse_native"
    return values
