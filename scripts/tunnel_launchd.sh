#!/usr/bin/env bash
# Start/stop/restart/status Cloudflare Tunnel LaunchAgent (macOS).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ACTION="${1:-}"
if [[ -z "$ACTION" ]]; then
  echo "Usage: $0 <start|stop|restart|status>" >&2
  exit 2
fi

case "$ACTION" in
  start|stop|restart|status) ;;
  *)
    echo "Unknown action: $ACTION (use start|stop|restart|status)" >&2
    exit 2
    ;;
esac

exec "$REPO_ROOT/bot" investor tunnel "$ACTION" "${@:2}"
