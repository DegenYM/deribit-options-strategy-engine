import sys
import time
from pathlib import Path

from cc_supervisor.main import Supervisor, TenantJob


def test_supervisor_starts_and_stops_process(tmp_path: Path):
    supervisor = Supervisor(python=sys.executable)
    job = TenantJob(
        tenant_id="tenant-a",
        desired="dry_run",
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        env={},
        state_dir=tmp_path / "tenant-a",
    )
    actions = supervisor.reconcile([job])
    assert actions["tenant-a"] == "running"
    assert supervisor.procs["tenant-a"].poll() is None
    time.sleep(0.2)
    supervisor.reconcile([])
    assert "tenant-a" not in supervisor.procs


def test_supervisor_stops_on_pause(tmp_path: Path):
    supervisor = Supervisor(python=sys.executable)
    running = TenantJob(
        tenant_id="tenant-b",
        desired="dry_run",
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        env={},
        state_dir=tmp_path / "tenant-b",
    )
    supervisor.reconcile([running])
    paused = TenantJob(
        tenant_id="tenant-b",
        desired="paused",
        argv=running.argv,
        env={},
        state_dir=running.state_dir,
    )
    actions = supervisor.reconcile([paused])
    assert actions["tenant-b"] == "stopped"
    assert "tenant-b" not in supervisor.procs
