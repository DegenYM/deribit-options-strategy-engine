from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ccsaas-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'control.sqlite3'}")
os.environ.setdefault("CC_SAAS_DATA_DIR", str(_TMP))
os.environ.setdefault("CC_SAAS_SECRET_KEY", "test-secret-key-please-change")
os.environ.setdefault("CC_SAAS_CREDENTIAL_KEY", "test-secret-key-please-change")
os.environ.setdefault("WAITLIST_ONLY", "true")
os.environ.setdefault("ALLOW_DEV_BILLING", "true")
os.environ.setdefault("DRY_RUN_MIN_DAYS", "7")
os.environ.setdefault("CC_SAAS_WEB_DIR", str(Path(__file__).resolve().parents[1] / "apps" / "web"))

from cc_saas.db import SessionLocal, init_db  # noqa: E402
from cc_saas.main import create_app  # noqa: E402
from cc_saas.models import User  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def make_client() -> TestClient:
    init_db()
    return TestClient(create_app())


def signup(client: TestClient, email: str = "trader@example.com") -> TestClient:
    requested = client.post("/api/auth/magic-link", json={"email": email}).json()
    token = requested["dev_token"]
    verified = client.post("/api/auth/verify", json={"token": token})
    assert verified.status_code == 200
    return client


def approve(email: str) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        user.approved = True
        user.waitlisted = False
        user.is_admin = True
        db.commit()
    finally:
        db.close()
