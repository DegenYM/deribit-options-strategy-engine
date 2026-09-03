"""Spawn one Covered Call worker process per tenant from Postgres desired_state."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

LOGGER = logging.getLogger("cc_supervisor")


@dataclass
class TenantJob:
    tenant_id: str
    desired: str
    argv: list[str]
    env: dict[str, str]
    state_dir: Path


class Supervisor:
    def __init__(self, python: str | None = None) -> None:
        self.python = python or sys.executable
        self.procs: dict[str, subprocess.Popen] = {}

    def stop(self, tenant_id: str, *, timeout: float = 8.0) -> None:
        proc = self.procs.get(tenant_id)
        if proc is None:
            return
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs.pop(tenant_id, None)

    def start(self, job: TenantJob) -> None:
        existing = self.procs.get(job.tenant_id)
        if existing is not None and existing.poll() is None:
            return
        job.state_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(job.env)
        log_path = job.state_dir / "worker.log"
        log_file = log_path.open("a", encoding="utf-8")
        proc = subprocess.Popen(
            job.argv,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.procs[job.tenant_id] = proc
        LOGGER.info("started worker tenant=%s pid=%s desired=%s", job.tenant_id, proc.pid, job.desired)

    def reconcile(self, jobs: list[TenantJob]) -> dict[str, str]:
        wanted = {job.tenant_id: job for job in jobs}
        for tenant_id in list(self.procs):
            if tenant_id not in wanted:
                self.stop(tenant_id)
        actions: dict[str, str] = {}
        for tenant_id, job in wanted.items():
            if job.desired == "panic":
                self.stop(tenant_id)
                panic_argv = [arg for arg in job.argv if arg != "run"]
                if "panic" not in panic_argv:
                    # argv is [python, -m, cc_engine, run, ...]
                    panic_argv = [self.python, "-m", "cc_engine", "panic", *job.argv[4:]]
                subprocess.run(panic_argv, env={**os.environ, **job.env}, check=False, timeout=120)
                actions[tenant_id] = "panic"
                continue
            if job.desired in {"stopped", "paused"}:
                if tenant_id in self.procs:
                    self.stop(tenant_id)
                    actions[tenant_id] = "stopped"
                continue
            self.start(job)
            actions[tenant_id] = "running"
        return actions


def jobs_from_db(db: Session, *, data_dir: Path, python: str) -> list[TenantJob]:
    from cc_saas.crypto import decrypt_secret
    from cc_saas.models import BotSettings, Credential, DesiredState, Tenant

    jobs: list[TenantJob] = []
    rows = db.query(DesiredState).all()
    for desired_row in rows:
        if desired_row.desired in {"stopped"}:
            continue
        tenant = db.get(Tenant, desired_row.tenant_id)
        if tenant is None or tenant.credential is None:
            continue
        cred: Credential = tenant.credential
        bot: BotSettings | None = tenant.bot_settings
        state_dir = data_dir / "tenants" / tenant.id
        coins = bot.coins_csv if bot else "BTC"
        risk = bot.risk_tier if bot else "low"
        sweep = bool(bot.profit_sweep) if bot else False
        live = desired_row.desired == "live"
        argv = [
            python,
            "-m",
            "cc_engine",
            "run",
            "--tenant-id",
            tenant.id,
            "--risk-tier",
            risk,
            "--coins",
            coins,
            "--state-dir",
            str(state_dir),
        ]
        if sweep:
            argv.append("--profit-sweep")
        if live:
            argv.append("--live")
        env = {
            "DERIBIT_CLIENT_ID": cred.client_id,
            "DERIBIT_CLIENT_SECRET": decrypt_secret(cred.secret_encrypted),
        }
        if bot and bot.telegram_chat_id and bot.telegram_token_encrypted:
            env["TELEGRAM_ALERTS_ENABLED"] = "true"
            env["TELEGRAM_CHAT_ID"] = bot.telegram_chat_id
            env["TELEGRAM_BOT_TOKEN"] = decrypt_secret(bot.telegram_token_encrypted)
        jobs.append(
            TenantJob(
                tenant_id=tenant.id,
                desired=desired_row.desired,
                argv=argv,
                env=env,
                state_dir=state_dir,
            )
        )
    return jobs


def mark_heartbeats(db: Session, supervisor: Supervisor, jobs: list[TenantJob]) -> None:
    from datetime import UTC, datetime

    from cc_saas.models import DesiredState, WorkerHeartbeat

    for job in jobs:
        proc = supervisor.procs.get(job.tenant_id)
        row = db.get(WorkerHeartbeat, job.tenant_id)
        if row is None:
            row = WorkerHeartbeat(tenant_id=job.tenant_id)
            db.add(row)
        row.pid = int(proc.pid) if proc is not None and proc.poll() is None else 0
        row.desired = job.desired
        row.updated_at = datetime.now(tz=UTC)
        if job.desired == "panic":
            desired_row = db.query(DesiredState).filter(DesiredState.tenant_id == job.tenant_id).one_or_none()
            if desired_row is not None:
                desired_row.desired = "paused"


def loop_forever(poll_seconds: float = 5.0) -> None:
    from cc_saas.config import settings
    from cc_saas.db import SessionLocal, init_db

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    supervisor = Supervisor()
    LOGGER.info("supervisor polling desired_state every %ss", poll_seconds)
    while True:
        db = SessionLocal()
        try:
            jobs = jobs_from_db(db, data_dir=settings.data_dir, python=sys.executable)
            supervisor.reconcile(jobs)
            mark_heartbeats(db, supervisor, jobs)
            db.commit()
        except Exception:
            LOGGER.exception("supervisor tick failed")
            db.rollback()
        finally:
            db.close()
        time.sleep(poll_seconds)


def main() -> None:
    loop_forever(float(os.environ.get("SUPERVISOR_POLL_SECONDS", "5")))


if __name__ == "__main__":
    main()
