# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

ARG APP_UID=10001
ARG APP_GID=10001
# Set to 1 to also install the optional PDF stack (reportlab/matplotlib/numpy/pillow)
# used by investor fee-report PDFs. Core engine + dashboard do not need it.
ARG WITH_PDF=0

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt requirements-pdf.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && if [ "${WITH_PDF}" = "1" ]; then python -m pip install -r requirements-pdf.txt; fi

# Single healthcheck shared by the `live` and `frontend` compose services (same image).
# It inspects PID 1's command line to decide which probe applies:
#   * live     (scripts/run_live_profiles.py) -> heartbeat freshness via
#              scripts/check_live_heartbeat.py --dry-run (exit 1 when stale; no Telegram)
#   * frontend (python -m deribit_engine ... frontend --port N) -> GET /api/health on N
#              (falls back to $PORT, then 8765)
COPY <<'PY' /usr/local/bin/healthcheck.py
import os
import subprocess
import sys
import urllib.request

try:
    argv = open("/proc/1/cmdline", "rb").read().split(b"\0")
    argv = [a.decode("utf-8", "replace") for a in argv if a]
except OSError:
    argv = []

if any("run_live_profiles" in a for a in argv):
    cmd = [sys.executable, "scripts/check_live_heartbeat.py", "--dry-run"]
    investor = os.environ.get("INVESTOR")
    if investor:
        cmd += ["--investor", investor]
    raise SystemExit(subprocess.call(cmd, cwd="/app"))

port = None
if "--port" in argv:
    port = argv[argv.index("--port") + 1] if argv.index("--port") + 1 < len(argv) else None
port = port or os.environ.get("PORT") or "8765"
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
        raise SystemExit(0 if 200 <= resp.status < 300 else 1)
except Exception as exc:  # any failure means unhealthy
    print(f"healthcheck failed on port {port}: {exc}", file=sys.stderr)
    raise SystemExit(1) from None
PY

COPY --chown=app:app . .
RUN mkdir -p .state logs data \
    && chown -R app:app .state logs data

USER app

HEALTHCHECK --interval=60s --timeout=15s --start-period=90s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py"]

CMD ["python", "-m", "deribit_engine", "--help"]
