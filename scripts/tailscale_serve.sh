#!/usr/bin/env bash
# Expose loopback investor dashboards + admin on the Tailscale tailnet (Serve).
# Does not rebind uvicorn and does not enable Funnel.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ACTION="${1:-}"
ROOT_INVESTOR="${2:-youming}"
if [[ -z "$ACTION" ]]; then
  echo "Usage: $0 <start|stop|status> [root-investor]" >&2
  echo "  start   tailscale serve --bg for every frontend_enabled port in registry.toml" >&2
  echo "  stop    tailscale serve reset" >&2
  echo "  status  print Serve URLs" >&2
  echo "  [root-investor]  which dashboard maps to https://<magicdns>/  (default: youming)" >&2
  exit 2
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale CLI not found. Install Tailscale.app first." >&2
  exit 1
fi

list_ports() {
  python3 - "$REPO_ROOT" "$ROOT_INVESTOR" <<'PY'
from pathlib import Path
import sys
import tomllib

repo, root_id = Path(sys.argv[1]), sys.argv[2].strip().lower()
data = tomllib.loads((repo / "config/platform/registry.toml").read_text(encoding="utf-8"))
rows = []
for row in data.get("investors") or []:
    if not row.get("frontend_enabled", False):
        continue
    port = row.get("frontend_port")
    if port is None:
        continue
    rows.append((str(row.get("id") or ""), int(port)))
if not rows:
    raise SystemExit("no frontend_enabled investors with frontend_port in registry.toml")
root_port = next((port for inv, port in rows if inv == root_id), None)
if root_port is None:
    root_port = min(port for _inv, port in rows)
print(root_port)
for inv, port in rows:
    print(f"{inv}\t{port}")
PY
}

dns_name() {
  python3 - <<'PY'
import json, subprocess
raw = subprocess.check_output(["tailscale", "status", "--json"], text=True)
name = (json.loads(raw).get("Self") or {}).get("DNSName") or ""
print(name.rstrip("."))
PY
}

case "$ACTION" in
  start)
    mapfile -t LINES < <(list_ports)
    root_port="${LINES[0]}"
    echo "Serving https://$(dns_name)/ → 127.0.0.1:${root_port} (${ROOT_INVESTOR})"
    tailscale serve --bg --yes "$root_port"
    echo "Serving http://$(dns_name)/ → 127.0.0.1:${root_port} (IPv4 HTTP fallback)"
    tailscale serve --bg --yes --http=80 "$root_port"
    # Also publish the root investor on its registry port. Admin iframes
    # rewrite 127.0.0.1:<frontend_port> → https://<magicdns>:<frontend_port>,
    # so youming (8765) must exist there, not only on :443.
    for line in "${LINES[@]:1}"; do
      inv="${line%%$'\t'*}"
      port="${line#*$'\t'}"
      echo "Serving https://$(dns_name):${port}/ → 127.0.0.1:${port} (${inv})"
      tailscale serve --bg --yes --https="$port" "$port"
    done
    echo "Serving https://$(dns_name):8750/ → 127.0.0.1:8750 (admin)"
    tailscale serve --bg --yes --https=8750 8750
    tailscale serve status
    ;;
  stop)
    tailscale serve reset
    echo "Tailscale Serve reset."
    ;;
  status)
    HOST="$(dns_name)"
    echo "MagicDNS: https://${HOST}/"
    echo "HTTP fallback: http://${HOST}/"
    echo
    tailscale serve status
    echo
    echo "Admin (tailnet only): https://${HOST}:8750/"
    echo "Public investor URLs still go through Cloudflare, not Funnel."
    ;;
  *)
    echo "Unknown action: $ACTION (use start|stop|status)" >&2
    exit 2
    ;;
esac
