from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    future=True,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)


def _ensure_sqlite_columns() -> None:
    """Add columns that create_all will not attach to an existing SQLite file."""
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(subscriptions)").fetchall()}
        if cols and "trial_ends_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN trial_ends_at DATETIME")


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    from . import models as _models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
