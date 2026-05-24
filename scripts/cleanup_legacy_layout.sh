#!/usr/bin/env bash
# Migrate or remove legacy single-.env / flat-ledger artifacts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/cleanup_legacy_layout.sh [--dry-run]

Moves legacy runtime data to canonical paths and removes obsolete root artifacts:
  - data/fee_ledger/An  -> data/fee_ledger/an
  - data/frontend_ledger/An -> data/frontend_ledger/an
  - flat frontend_ledger dirs (naked/, covered_call/, *_sub/)
  - root .state/*.json (non-investors/)
  - root .env.<strategy> and .env.<strategy>_sub (superseded by config/shared/strategies/)

Does not touch config/investors/ secrets or active .state/investors/ files.

On case-insensitive volumes (default macOS APFS), An→an renames use a temp
directory so data is not deleted.
EOF
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] $*"
  else
    echo "$*"
    eval "$@"
  fi
}

# macOS default APFS is case-insensitive: An and an are the same path.
# Use a temp dir when only casing differs.
rename_dir_if_case_only() {
  local src="$1" dst="$2"
  [[ -d "$src" ]] || return 0
  local src_base dst_base
  src_base="$(basename "$src")"
  dst_base="$(basename "$dst")"
  local src_lower dst_lower
  src_lower="$(printf '%s' "$src_base" | tr '[:upper:]' '[:lower:]')"
  dst_lower="$(printf '%s' "$dst_base" | tr '[:upper:]' '[:lower:]')"
  if [[ "$src_lower" == "$dst_lower" && "$src_base" != "$dst_base" ]]; then
    local tmp
    tmp="$(dirname "$dst")/.${dst_base}_case_rename_$$"
    run "mv '$src' '$tmp'"
    run "mv '$tmp' '$dst'"
    return 0
  fi
  return 1
}

merge_dir() {
  local src="$1" dst="$2"
  [[ -d "$src" ]] || return 0
  if rename_dir_if_case_only "$src" "$dst"; then
    return 0
  fi
  if [[ -d "$dst" ]]; then
    run "rsync -a '$src/' '$dst/'"
    run "rm -rf '$src'"
  else
    run "mkdir -p '$(dirname "$dst")'"
    run "mv '$src' '$dst'"
  fi
}

cd "$REPO_ROOT"

# Case-normalize investor runtime dirs
merge_dir "$REPO_ROOT/data/fee_ledger/An" "$REPO_ROOT/data/fee_ledger/an"
merge_dir "$REPO_ROOT/data/frontend_ledger/An" "$REPO_ROOT/data/frontend_ledger/an"

# Flat legacy frontend ledger (pre per-investor layout)
for legacy in covered_call covered_call_sub naked naked_short_sub; do
  if [[ -d "$REPO_ROOT/data/frontend_ledger/$legacy" ]]; then
    echo "Removing legacy flat ledger: data/frontend_ledger/$legacy"
    run "rm -rf '$REPO_ROOT/data/frontend_ledger/$legacy'"
  fi
done

if [[ -f "$REPO_ROOT/data/frontend_ledger/metrics.db" ]]; then
  echo "Removing legacy root metrics.db (use data/frontend_ledger/<investor_id>/metrics.db)"
  run "rm -f '$REPO_ROOT/data/frontend_ledger/metrics.db'"
fi

# Root state files (pre .state/investors/)
if [[ -d "$REPO_ROOT/.state/investors" ]]; then
  for f in "$REPO_ROOT/.state"/*.json; do
    [[ -f "$f" ]] || continue
    echo "Removing legacy root state: $f"
    run "rm -f '$f'"
  done
  for f in "$REPO_ROOT/.state"/*.trade_journal.db; do
    [[ -f "$f" ]] || continue
    echo "Removing legacy root trade journal: $f"
    run "rm -f '$f'"
  done
  for f in "$REPO_ROOT/.state"/*.lock; do
    [[ -f "$f" ]] || continue
    run "rm -f '$f'"
  done
fi

# Root strategy env profiles (superseded by config/shared/strategies/)
for f in \
  "$REPO_ROOT/.env.naked_short" \
  "$REPO_ROOT/.env.naked_short_sub" \
  "$REPO_ROOT/.env.covered_call" \
  "$REPO_ROOT/.env.covered_call_sub" \
  "$REPO_ROOT/.env.bull_put_spread"
do
  if [[ -f "$f" ]]; then
    echo "Removing legacy root strategy env: $f"
    run "rm -f '$f'"
  fi
done

echo "Done."
