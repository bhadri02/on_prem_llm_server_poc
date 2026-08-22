#!/usr/bin/env bash
#
# scripts/backup-onprem.sh
#
# Automates the manual backup commands documented in docs/DEPLOYMENT.md's
# "Back up persistent data" section — Postgres (users/roles/API keys, and
# now also the audit trail — audit_store is Postgres-backed too, see
# CLAUDE.md's Audit Store section — same pg_dump covers both) and the model
# registry catalog (JSON). Ollama model weights are deliberately NOT backed
# up (large, and trivially re-pulled via OLLAMA_DEFAULT_MODEL/OLLAMA_EXTRA_MODELS).
#
# Assumes the stack was deployed via docker-compose.prod.yml, which fixes
# both the container names (llm-postgres, etc.) and the compose project name
# (name: llm-platform-prod, so volume names are always
# llm-platform-prod_<volume>) — this script does not need to be run from the
# deployment directory as a result.
#
# Usage:
#   chmod +x scripts/backup-onprem.sh
#   ./scripts/backup-onprem.sh                          # backs up to ./backups
#   ./scripts/backup-onprem.sh --dest /mnt/backups       # custom destination
#   ./scripts/backup-onprem.sh --retention-days 30       # prune older than 30 days (default 14)
#   ./scripts/backup-onprem.sh --retention-days 0        # keep everything, never prune
#
# To automate, add a crontab entry (as the user with docker permissions),
# e.g. nightly at 2am, keeping 14 days:
#   0 2 * * * /opt/llm-platform/scripts/backup-onprem.sh --dest /mnt/backups >> /var/log/llm-backup.log 2>&1
#
# Restoring: see scripts/restore-onprem.sh.
#
set -euo pipefail

DEST="./backups"
RETENTION_DAYS=14

for arg in "$@"; do
  case "$arg" in
    --dest=*) DEST="${arg#*=}" ;;
    --dest) shift_next_dest=true ;;
    --retention-days=*) RETENTION_DAYS="${arg#*=}" ;;
    --retention-days) shift_next_retention=true ;;
    -h|--help)
      echo "Usage: $0 [--dest DIR] [--retention-days N]"
      exit 0
      ;;
    *)
      if [ "${shift_next_dest:-false}" = "true" ]; then DEST="$arg"; shift_next_dest=false;
      elif [ "${shift_next_retention:-false}" = "true" ]; then RETENTION_DAYS="$arg"; shift_next_retention=false;
      else echo "Unknown argument: $arg" >&2; exit 1; fi
      ;;
  esac
done

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "Docker is not installed."
docker info >/dev/null 2>&1 || die "Docker is installed but not running, or this user lacks permission."

for name in llm-postgres llm-audit-store llm-model-registry; do
  docker inspect "$name" >/dev/null 2>&1 || die "Container '$name' not found — is the stack running (docker-compose.prod.yml)?"
done

STAMP="$(date +%Y%m%dT%H%M%S)"
RUN_DIR="$DEST/$STAMP"
mkdir -p "$RUN_DIR"
log "Backing up to $RUN_DIR"

# --- Postgres (users/roles/API keys, and the audit trail) -------------------
# --clean --if-exists so the dump is safe to replay into a non-empty DB
# (restore drops each object before recreating it, instead of erroring on
# primary-key conflicts against whatever's already there). Covers the
# audit_events table too — audit_store shares this same Postgres database.
log "Postgres (pg_dump) — includes users/roles/API keys and the audit trail"
docker exec llm-postgres pg_dump --clean --if-exists -U llm_user llm_platform > "$RUN_DIR/postgres.sql"

# --- Model registry catalog (JSON file) -------------------------------------
log "Model registry (model_registry_data volume)"
docker run --rm \
  -v llm-platform-prod_model_registry_data:/data:ro \
  -v "$(cd "$RUN_DIR" && pwd)":/backup \
  alpine tar czf /backup/model-registry-data.tar.gz -C /data .

SIZE="$(du -sh "$RUN_DIR" | cut -f1)"
log "Backup complete: $RUN_DIR ($SIZE)"
echo "  Restore both with: scripts/restore-onprem.sh $RUN_DIR"

# --- Retention -----------------------------------------------------------
if [ "$RETENTION_DAYS" -gt 0 ] 2>/dev/null; then
  log "Pruning backups older than $RETENTION_DAYS days in $DEST"
  find "$DEST" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -print -exec rm -rf {} \;
fi
