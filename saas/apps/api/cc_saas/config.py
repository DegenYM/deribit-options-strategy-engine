from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    credential_key: str
    data_dir: Path
    web_dir: Path
    magic_link_ttl_minutes: int
    session_days: int
    dry_run_min_days: int
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_success_url: str
    stripe_cancel_url: str
    public_base_url: str
    allow_dev_billing: bool
    waitlist_only: bool
    trial_days: int
    trial_plan_id: str

    @classmethod
    def from_environ(cls) -> Settings:
        here = Path(__file__).resolve()
        saas_root = here.parents[3]
        data_dir = Path(os.environ.get("CC_SAAS_DATA_DIR", str(saas_root / "var" / "data"))).resolve()
        web_dir = Path(os.environ.get("CC_SAAS_WEB_DIR", str(saas_root / "apps" / "web"))).resolve()
        secret = os.environ.get("CC_SAAS_SECRET_KEY", "dev-secret-change-me")
        cred_key = os.environ.get("CC_SAAS_CREDENTIAL_KEY", secret)
        default_db = f"sqlite:///{data_dir / 'control.sqlite3'}"
        return cls(
            database_url=os.environ.get("DATABASE_URL", default_db),
            secret_key=secret,
            credential_key=cred_key,
            data_dir=data_dir,
            web_dir=web_dir,
            magic_link_ttl_minutes=int(os.environ.get("MAGIC_LINK_TTL_MINUTES", "20")),
            session_days=int(os.environ.get("SESSION_DAYS", "14")),
            dry_run_min_days=int(os.environ.get("DRY_RUN_MIN_DAYS", "7")),
            stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
            stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
            stripe_success_url=os.environ.get("STRIPE_SUCCESS_URL", "http://127.0.0.1:8080/?billing=success"),
            stripe_cancel_url=os.environ.get("STRIPE_CANCEL_URL", "http://127.0.0.1:8080/?billing=cancel"),
            public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8080"),
            allow_dev_billing=_bool("ALLOW_DEV_BILLING", default=True),
            waitlist_only=_bool("WAITLIST_ONLY", default=True),
            trial_days=int(os.environ.get("TRIAL_DAYS", "30")),
            trial_plan_id=os.environ.get("TRIAL_PLAN_ID", "scout").strip().lower() or "scout",
        )


settings = Settings.from_environ()
