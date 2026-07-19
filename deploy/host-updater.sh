#!/bin/bash
# Crypto Wallet Tracker — Host-side updater
# Triggered by systemd when /data/deploy/request.json appears on the shared volume.
#
# The container writes request.json via the shared Docker volume.
# This script (running on the HOST) picks it up, performs git pull + docker rebuild,
# and writes status.json back so the container's GET /api/update/status can poll it.

set -euo pipefail

# ── Config ──────────────────────────────────────────────────
APP_DIR="/opt/crypto-wallet-tracker"
DEPLOY_DIR="/var/lib/docker/volumes/crypto-wallet-tracker_wallet-data/_data/deploy"
REQUEST_FILE="${DEPLOY_DIR}/request.json"
STATUS_FILE="${DEPLOY_DIR}/status.json"
LOG_TAG="crypto-updater"
LOG_FILE="/var/log/crypto-updater.log"
MAX_RETRIES=1

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"
}

# ── Guard: ensure deploy directory exists ───────────────────
mkdir -p "$DEPLOY_DIR"

# ── Check for request ───────────────────────────────────────
if [ ! -f "$REQUEST_FILE" ]; then
    log "No request file found, exiting."
    exit 0
fi

log "========== Deploy request detected =========="
log "Request: $(cat "$REQUEST_FILE")"

# ── Write "running" status ──────────────────────────────────
cat > "$STATUS_FILE" <<EOF
{"state":"running","message":"Git pull + docker rebuild in progress…","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":"","request":$(cat "$REQUEST_FILE")}
EOF

# ── git pull ────────────────────────────────────────────────
log "Step 1/2: git pull origin main …"
cd "$APP_DIR"

# Best-effort deploy key setup (VM may not have GitHub SSH key)
DEPLOY_KEY=""
if [ -f /tmp/deploy_key ]; then
    DEPLOY_KEY="/tmp/deploy_key"
elif [ -f /root/.ssh/id_ed25519 ]; then
    DEPLOY_KEY="/root/.ssh/id_ed25519"
elif [ -f /home/cpt-claude/.ssh/id_ed25519 ]; then
    DEPLOY_KEY="/home/cpt-claude/.ssh/id_ed25519"
elif [ -f /home/cpt-frederic/.ssh/id_ed25519 ]; then
    DEPLOY_KEY="/home/cpt-frederic/.ssh/id_ed25519"
fi

GIT_SSH=""
if [ -n "$DEPLOY_KEY" ]; then
    GIT_SSH="GIT_SSH_COMMAND=ssh -i ${DEPLOY_KEY} -o StrictHostKeyChecking=no"
fi

if ! sudo -n ${GIT_SSH} git -C "$APP_DIR" pull origin main 2>&1 | tee -a "$LOG_FILE"; then
    log "ERROR: git pull failed"
    cat > "$STATUS_FILE" <<EOF
{"state":"failed","message":"git pull origin main failed — check /var/log/crypto-updater.log","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":""}
EOF
    rm -f "$REQUEST_FILE"
    exit 1
fi

# ── Fetch tags (for version detection) ──────────────────────
sudo -n ${GIT_SSH} git -C "$APP_DIR" fetch --tags origin 2>/dev/null || true

# ── Get latest tag as version ───────────────────────────────
VERSION=$(sudo -n git -C "$APP_DIR" tag --sort=-creatordate 2>/dev/null | head -1 || echo "unknown")
log "Version after pull: $VERSION"

# ── Docker rebuild ──────────────────────────────────────────
log "Step 2/2: docker compose up -d --build …"
cd "$APP_DIR"
if ! sudo -n docker compose up -d --build 2>&1 | tee -a "$LOG_FILE"; then
    log "ERROR: docker compose failed"
    cat > "$STATUS_FILE" <<EOF
{"state":"failed","message":"docker compose up -d --build failed — check /var/log/crypto-updater.log","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":"$VERSION"}
EOF
    rm -f "$REQUEST_FILE"
    exit 1
fi

# ── Success ──────────────────────────────────────────────────
log "Deploy successful (version: $VERSION)"
cat > "$STATUS_FILE" <<EOF
{"state":"done","message":"Déploiement terminé — version $VERSION","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":"$VERSION"}
EOF

rm -f "$REQUEST_FILE"
log "========== Deploy complete =========="
exit 0
