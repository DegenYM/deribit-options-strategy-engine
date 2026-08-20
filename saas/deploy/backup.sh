#!/bin/sh
# Daily backup of Postgres dump (if pg_dump exists) and tenant worker state.
set -eu
ROOT="${CC_SAAS_DATA_DIR:-/var/lib/cc-saas}"
DEST="${CC_SAAS_BACKUP_DIR:-$ROOT/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST"
if command -v pg_dump >/dev/null 2>&1 && [ -n "${DATABASE_URL:-}" ]; then
  pg_dump "$DATABASE_URL" > "$DEST/postgres-$STAMP.sql" || true
fi
tar -C "$(dirname "$ROOT")" -czf "$DEST/data-$STAMP.tgz" "$(basename "$ROOT")"
# Keep two weeks
find "$DEST" -type f -mtime +14 -delete
echo "backup written under $DEST"
