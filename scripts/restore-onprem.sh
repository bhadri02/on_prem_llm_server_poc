#!/usr/bin/env bash
#
# scripts/restore-onprem.sh
#
# Restores a backup produced by scripts/backup-onprem.sh: Postgres
# (users/roles/API keys, and the audit trail — audit_store is Postgres-
# backed too, restored by the same postgres.sql) and the model registry
# catalog (JSON). DESTRUCTIVE — overwrites current data with the backup's
# contents. Stops the affected containers first so nothing is writing to
# the DB/volumes mid-restore, then restarts them afterward.
#
# Usage:
#   chmod +x scripts/restore-onprem.sh
#   ./scripts/restore-onprem.sh ./backups/20260818T020000
#   ./scripts/restore-onprem.sh ./backups/20260818T020000 --yes   # skip confirmation
#
set -euo pipefail

BACKUP_DIR="${1:-}"
ASSUME_YES=false
for arg in "${@:2}"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=true ;;
    -h|--help) ;;
  esac
done

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

[ -n "$BACKUP_DIR" ] || die "Usage: $0 <backup-dir> [--yes]"
[ -d "$BACKUP_DIR" ] || die "Backup directory not found: $BACKUP_DIR"
[ -f "$BACKUP_DIR/postgres.sql" ] || die "Missing $BACKUP_DIR/postgres.sql — not a scripts/backup-onprem.sh backup?"
[ -f "$BACKUP_DIR/model-registry-data.tar.gz" ] || die "Missing $BACKUP_DIR/model-registry-data.tar.gz"

command -v docker >/dev/null 2>&1 || die "Docker is not installed."
docker info >/dev/null 2>&1 || die "Docker is installed but not running, or this user lacks permission."

for name in llm-postgres llm-admin-portal llm-audit-store llm-model-registry; do
  docker inspect "$name" >/dev/null 2>&1 || die "Container '$name' not found — is the stack running (docker-compose.prod.yml)?"
done

warn "This OVERWRITES the current Postgres DB (including the audit trail) and model registry catalog with the contents of $BACKUP_DIR."
if [ "$ASSUME_YES" != "true" ]; then
  read -rp "Type 'restore' to continue: " CONFIRM
  [ "$CONFIRM" = "restore" ] || die "Aborted."
fi

log "Stopping llm-admin-portal, llm-audit-store, llm-model-registry (postgres stays up for the SQL restore)"
docker stop llm-admin-portal llm-audit-store llm-model-registry >/dev/null

# --- Postgres (includes the audit trail) --------------------------------
log "Restoring Postgres from postgres.sql"
docker exec -i llm-postgres psql -U llm_user llm_platform < "$BACKUP_DIR/postgres.sql"

# --- Model registry volume ------------------------------------------------
log "Restoring model_registry_data volume"
docker run --rm \
  -v llm-platform-prod_model_registry_data:/data \
  -v "$(cd "$BACKUP_DIR" && pwd)":/backup \
  alpine sh -c "rm -rf /data/* /data/.[!.]* 2>/dev/null; tar xzf /backup/model-registry-data.tar.gz -C /data"

log "Restarting llm-audit-store, llm-model-registry, llm-admin-portal"
docker start llm-audit-store llm-model-registry >/dev/null
# admin-portal depends on audit-store/model-registry being healthy; give them
# a moment before bringing it back so its own startup checks don't race them.
sleep 5
docker start llm-admin-portal >/dev/null

log "Restore complete. Check container health with: docker ps --filter name=llm-"
