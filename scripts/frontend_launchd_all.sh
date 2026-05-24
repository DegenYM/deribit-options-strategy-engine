#!/usr/bin/env bash
# One-shot start/stop/restart/status for all registry frontend_enabled investors.
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

exec "$REPO_ROOT/bot" investor frontend "$ACTION" "${@:2}"
