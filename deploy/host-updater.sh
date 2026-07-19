#!/bin/bash
# Crypto Wallet Tracker — Host-side updater (polling daemon)
# Runs as a long-lived systemd service. Every ~12 s, checks for a deploy
# request file on the shared Docker volume. When found: git reset + docker
# rebuild, write status, and ALWAYS delete the request file so the next
# click on "Mettre à jour" triggers again cleanly.
#
# Replaces the fragile systemd.path (PathExists) that blocked on
# unit-start-limit-hit when request.json was not deleted.

set -euo pipefail

# ── Config ──────────────────────────────────────────────────
APP_DIR="/opt/crypto-wallet-tracker"
DEPLOY_DIR="/var/lib/docker/volumes/crypto-wallet-tracker_wallet-data/_data/deploy"
REQUEST_FILE="${DEPLOY_DIR}/request.json"
STATUS_FILE="${DEPLOY_DIR}/status.json"
LOG_FILE="/var/log/crypto-updater.log"
POLL_INTERVAL=12   # seconds between polls

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"
}

# ── Read the REAL version from public/index.html ─────────────
# The verCurrent <strong> element holds the canonical version string.
read_version() {
    if [ -f "$APP_DIR/public/index.html" ]; then
        grep -oP 'id="verCurrent">\K[^<]+' "$APP_DIR/public/index.html" | head -1
    else
        echo "unknown"
    fi
}

# ── Process one deploy request ──────────────────────────────
# Returns 0 on success, 1 on failure (always deletes request.json).
process_request() {
    local request_payload
    request_payload="$(cat "$REQUEST_FILE" 2>/dev/null || echo "{}")"
    log "========== Deploy request detected =========="
    log "Request: $request_payload"

    # Write "running" status
    cat > "$STATUS_FILE" <<EOF
{"state":"running","message":"git fetch + reset --hard origin/main + docker rebuild in progress…","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":""}
EOF

    # ── SSH key setup (best-effort) ─────────────────────────
    DEPLOY_KEY=""
    ROOT_KEY="/tmp/root_deploy_key"

    find_and_copy_key() {
        local src="$1"
        if [ -f "$src" ] && [ -r "$src" ]; then
            cp "$src" "$ROOT_KEY"
            chmod 600 "$ROOT_KEY"
            chown root:root "$ROOT_KEY"
            DEPLOY_KEY="$ROOT_KEY"
            return 0
        fi
        return 1
    }

    find_and_copy_key /tmp/deploy_key || \
    find_and_copy_key /root/.ssh/id_ed25519 || \
    find_and_copy_key /home/cpt-claude/.ssh/id_ed25519 || \
    find_and_copy_key /home/cpt-frederic/.ssh/id_ed25519 || true

    GIT_SSH_CMD=""
    if [ -n "$DEPLOY_KEY" ]; then
        GIT_SSH_CMD="ssh -i ${DEPLOY_KEY} -o StrictHostKeyChecking=no"
    fi

    # ── git fetch + hard reset ──────────────────────────────
    log "Step 1/3: git fetch + reset --hard origin/main …"
    cd "$APP_DIR"

    if ! sudo -n env GIT_SSH_COMMAND="$GIT_SSH_CMD" git -C "$APP_DIR" fetch origin main --quiet 2>&1 | tee -a "$LOG_FILE"; then
        log "ERROR: git fetch failed"
        cat > "$STATUS_FILE" <<EOF
{"state":"failed","message":"git fetch origin main failed — check /var/log/crypto-updater.log","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":""}
EOF
        return 1
    fi

    if ! sudo -n git -C "$APP_DIR" reset --hard origin/main 2>&1 | tee -a "$LOG_FILE"; then
        log "ERROR: git reset --hard failed"
        cat > "$STATUS_FILE" <<EOF
{"state":"failed","message":"git reset --hard origin/main failed — check /var/log/crypto-updater.log","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":""}
EOF
        return 1
    fi

    # Remove untracked cruft (never touches /data or Docker volumes)
    sudo -n git -C "$APP_DIR" clean -fd 2>&1 | tee -a "$LOG_FILE" || true

    # Ensure deploy scripts remain executable after reset
    sudo -n chmod +x "$APP_DIR"/deploy/host-updater.sh 2>/dev/null || true
    sudo -n chmod +x "$APP_DIR"/deploy/*.sh 2>/dev/null || true

    log "Force-sync complete: working tree now at origin/main"

    # ── Fetch tags (for logging only) ───────────────────────
    sudo -n env GIT_SSH_COMMAND="$GIT_SSH_CMD" git -C "$APP_DIR" fetch --tags origin 2>/dev/null || true

    # ── Docker rebuild ──────────────────────────────────────
    log "Step 2/3: docker compose up -d --build …"
    cd "$APP_DIR"
    if ! sudo -n docker compose up -d --build 2>&1 | tee -a "$LOG_FILE"; then
        log "ERROR: docker compose failed"
        VERSION="$(read_version)"
        cat > "$STATUS_FILE" <<EOF
{"state":"failed","message":"docker compose up -d --build failed — check /var/log/crypto-updater.log","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":"$VERSION"}
EOF
        return 1
    fi

    # ── Read REAL version from index.html (verCurrent) ──────
    log "Step 3/3: reading deployed version from public/index.html …"
    sleep 3   # let container start
    VERSION="$(read_version)"
    log "Deploy successful (version: $VERSION)"

    cat > "$STATUS_FILE" <<EOF
{"state":"done","message":"Déploiement terminé — version $VERSION","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":"$VERSION"}
EOF

    # Cleanup deploy key
    rm -f "$ROOT_KEY" 2>/dev/null || true
    log "========== Deploy complete =========="
    return 0
}

# ── Main polling loop ───────────────────────────────────────
log "Crypto-updater daemon started (poll interval: ${POLL_INTERVAL}s)"
mkdir -p "$DEPLOY_DIR"

while true; do
    if [ -f "$REQUEST_FILE" ]; then
        process_request
        # ALWAYS delete request.json — even on failure — so the
        # next click on "Mettre à jour" creates a fresh file and
        # triggers a new deploy cycle.
        rm -f "$REQUEST_FILE"
        log "request.json deleted (next deploy cycle will trigger cleanly)"
    fi
    sleep "$POLL_INTERVAL"
done
