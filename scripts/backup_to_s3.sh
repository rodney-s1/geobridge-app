#!/usr/bin/env bash
# =============================================================================
# backup_to_s3.sh — Geobridge project backup to S3
#
# Usage:
#   ./scripts/backup_to_s3.sh              # full backup
#   ./scripts/backup_to_s3.sh --data-only  # only backend data/JSON files
#   ./scripts/backup_to_s3.sh --prune 10   # backup + keep only last 10 archives
#
# Uploads to:  s3://geobridge-data-backup/backups/
# Data sync:   s3://geobridge-data-backup/data/        (always synced)
#
# Requirements: aws CLI configured (~/.aws/credentials) or AWS_* env vars set
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
BUCKET="geobridge-data-backup"
BACKUP_PREFIX="backups"
DATA_PREFIX="data"
PROJECT_DIR="/home/user/webapp"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H-%M-%SZ")
ARCHIVE_NAME="geobridge-backup-${TIMESTAMP}.tar.gz"
TMP_DIR=$(mktemp -d)
ARCHIVE_PATH="${TMP_DIR}/${ARCHIVE_NAME}"

# Backend data files that are always synced to data/ (live working copies)
DATA_FILES_DIR="${PROJECT_DIR}/backend"
DATA_PATTERNS=(
    "geotab/sku_catalog.json"
    "geotab/sku_customer_overrides.json"
    "geotab/sku_mappings.json"
    "geotab/sync_cache.json"
    "geotab/contract_checkpoint.json"
)

# ── Arg parsing ───────────────────────────────────────────────────────────────
DATA_ONLY=false
PRUNE_KEEP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --data-only) DATA_ONLY=true ;;
        --prune)     PRUNE_KEEP="${2:-10}"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

# ── Helpers ───────────────────────────────────────────────────────────────────
log() { echo "[backup] $*"; }
hr()  { echo "──────────────────────────────────────────────"; }

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

# ── Step 1: Sync live data files → s3://…/data/ ───────────────────────────────
hr
log "Syncing live data files → s3://${BUCKET}/${DATA_PREFIX}/"
for rel_path in "${DATA_PATTERNS[@]}"; do
    full_path="${DATA_FILES_DIR}/${rel_path}"
    filename=$(basename "$rel_path")
    if [[ -f "$full_path" ]]; then
        aws s3 cp "$full_path" "s3://${BUCKET}/${DATA_PREFIX}/${filename}" \
            --only-show-errors
        log "  ✓  ${filename}"
    else
        log "  –  ${filename} (not found, skipping)"
    fi
done

if [[ "$DATA_ONLY" == "true" ]]; then
    hr
    log "--data-only: skipping full archive. Done."
    exit 0
fi

# ── Step 2: Create timestamped tar.gz archive ─────────────────────────────────
hr
log "Creating archive: ${ARCHIVE_NAME}"
tar -czf "$ARCHIVE_PATH" \
    --exclude="${PROJECT_DIR}/node_modules" \
    --exclude="${PROJECT_DIR}/frontend/node_modules" \
    --exclude="${PROJECT_DIR}/.wrangler" \
    --exclude="${PROJECT_DIR}/dist" \
    --exclude="${PROJECT_DIR}/frontend/dist" \
    --exclude="${PROJECT_DIR}/backend/__pycache__" \
    --exclude="${PROJECT_DIR}/backend/geotab/__pycache__" \
    --exclude="${PROJECT_DIR}/backend/venv" \
    --exclude="${PROJECT_DIR}/backend/.venv" \
    --exclude="${PROJECT_DIR}/.git" \
    -C "$(dirname "$PROJECT_DIR")" \
    "$(basename "$PROJECT_DIR")"

SIZE=$(du -sh "$ARCHIVE_PATH" | cut -f1)
log "  Archive size: ${SIZE}"

# ── Step 3: Upload archive → s3://…/backups/ ─────────────────────────────────
hr
log "Uploading → s3://${BUCKET}/${BACKUP_PREFIX}/${ARCHIVE_NAME}"
aws s3 cp "$ARCHIVE_PATH" \
    "s3://${BUCKET}/${BACKUP_PREFIX}/${ARCHIVE_NAME}" \
    --only-show-errors
log "  ✓  Upload complete"

# ── Step 4: Optional pruning — keep only the N most recent archives ───────────
if [[ "$PRUNE_KEEP" -gt 0 ]]; then
    hr
    log "Pruning: keeping last ${PRUNE_KEEP} backups..."
    # List all archives sorted by date (oldest first), delete all but last N
    ARCHIVES=$(aws s3 ls "s3://${BUCKET}/${BACKUP_PREFIX}/" \
        | grep "geobridge-backup-" \
        | sort \
        | awk '{print $4}')
    TOTAL=$(echo "$ARCHIVES" | grep -c . || true)
    DELETE_COUNT=$(( TOTAL - PRUNE_KEEP ))
    if [[ "$DELETE_COUNT" -gt 0 ]]; then
        TO_DELETE=$(echo "$ARCHIVES" | head -n "$DELETE_COUNT")
        while IFS= read -r key; do
            aws s3 rm "s3://${BUCKET}/${BACKUP_PREFIX}/${key}" --only-show-errors
            log "  🗑  Deleted old backup: ${key}"
        done <<< "$TO_DELETE"
    else
        log "  Nothing to prune (only ${TOTAL} backups exist)"
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
hr
log "Backup complete!"
log "  Archive : s3://${BUCKET}/${BACKUP_PREFIX}/${ARCHIVE_NAME}"
log "  Data    : s3://${BUCKET}/${DATA_PREFIX}/"
log "  Time    : ${TIMESTAMP}"
hr
