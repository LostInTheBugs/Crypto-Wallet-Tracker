#!/bin/bash
# Crypto Wallet Tracker — Host-side updater (polling daemon)
# Runs as a long-lived systemd service. Every ~12 s, checks for a deploy
# request file on the shared Docker volume. When found: git fetch (HTTPS,
# no credentials needed — public repo) + docker rebuild, write status,
# and ALWAYS delete the request file so the next click on "Mettre à jour"
# triggers again cleanly.
#
# Replaces the fragile systemd.path (PathExists) that blocked on
# unit-start-limit-hit when request.json was not deleted.
#
# 2026.07.18 — Switched from SSH (git@github.com) to HTTPS fetch.
# The repo is public, no credentials needed. credential.helper is
# explicitly disabled to avoid any interactive prompt.

set -euo pipefail

# ── Config ──────────────────────────────────────────────────
APP_DIR="/opt/crypto-wallet-tracker"
DEPLOY_DIR="/var/lib/docker/volumes/crypto-wallet-tracker_wallet-data/_data/deploy"
REQUEST_FILE="${DEPLOY_DIR}/request.json"
STATUS_FILE="${DEPLOY_DIR}/status.json"
CONFIG_FILE="${DEPLOY_DIR}/config.json"
LOG_FILE="/var/log/crypto-updater.log"
POLL_INTERVAL=12   # seconds between polls (manual-mode requests)
AUTO_CHECK_INTERVAL=180  # seconds between auto-mode git fetch checks (~3 min)

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

# ── Read the update mode from shared config ──────────────────
read_update_mode() {
    if [ -f "$CONFIG_FILE" ]; then
        python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('update_mode','manual'))" "$CONFIG_FILE" 2>/dev/null || echo "manual"
    else
        echo "manual"
    fi
}

# ── Auto-deploy: fetch origin/main and deploy if ahead ──────
# Returns 0 if no deploy needed, 1 on deploy failure, 2 if deployed.
auto_deploy_if_ahead() {
    local mode
    mode="$(read_update_mode)"
    if [ "$mode" != "auto" ]; then
        return 0  # manual mode, nothing to do
    fi

    log "[auto-check] fetching origin/main …"
    if ! sudo -n git -c credential.helper= -C "$APP_DIR" fetch origin main --quiet 2>&1 | tee -a "$LOG_FILE"; then
        log "[auto-check] WARNING: git fetch failed — will retry next cycle"
        return 0  # don't block the loop
    fi

    local local_head remote_head
    local_head="$(sudo -n git -C "$APP_DIR" rev-parse HEAD 2>/dev/null)" || return 0
    remote_head="$(sudo -n git -C "$APP_DIR" rev-parse origin/main 2>/dev/null)" || return 0

    if [ "$local_head" = "$remote_head" ]; then
        log "[auto-check] already at latest ($(echo "$local_head" | head -c 9))"
        return 0
    fi

    log "[auto-check] NEW VERSION DETECTED: local=$(echo "$local_head" | head -c 9) remote=$(echo "$remote_head" | head -c 9) — auto-deploy triggered"
    process_request "auto"
    return $?
}

# ── Process one deploy request ──────────────────────────────
# Accepts optional reason string (e.g. "auto") for logging.
process_request() {
    local reason="${1:-manual}"
    local request_payload
    request_payload="$(cat "$REQUEST_FILE" 2>/dev/null || echo "{}")"
    log "========== Deploy request detected (reason: $reason) =========="
    log "Request: $request_payload"

    # Write "running" status
    cat > "$STATUS_FILE" <<EOF
{"state":"running","message":"git fetch + reset --hard origin/main + docker rebuild in progress…","updated_at":"$(date -u +'%Y-%m-%dT%H:%M:%SZ')","version":""}
EOF

    # ── git fetch (HTTPS, public repo — no credentials needed) ──
    log "Step 1/3: git fetch + reset --hard origin/main …"
    cd "$APP_DIR"

    if ! sudo -n git -c credential.helper= -C "$APP_DIR" fetch origin main --quiet 2>&1 | tee -a "$LOG_FILE"; then
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
    sudo -n git -c credential.helper= -C "$APP_DIR" fetch --tags origin 2>/dev/null || true

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

    log "========== Deploy complete =========="
    return 0
}

# ── Main polling loop ───────────────────────────────────────
log "Crypto-updater daemon started (poll interval: ${POLL_INTERVAL}s, auto-check: ${AUTO_CHECK_INTERVAL}s, fetch: HTTPS)"
mkdir -p "$DEPLOY_DIR"

AUTO_TICK=0

while true; do
    # ── Manual request handling ─────────────────────────────
    if [ -f "$REQUEST_FILE" ]; then
        process_request "manual"
        # ALWAYS delete request.json — even on failure — so the
        # next click on "Mettre à jour" creates a fresh file and
        # triggers a new deploy cycle.
        rm -f "$REQUEST_FILE"
        log "request.json deleted (next deploy cycle will trigger cleanly)"
        AUTO_TICK=0  # reset auto timer after a manual deploy
    fi

    # ── Auto-update check (every ~3 min when mode=auto) ─────
    AUTO_TICK=$((AUTO_TICK + POLL_INTERVAL))
    if [ "$AUTO_TICK" -ge "$AUTO_CHECK_INTERVAL" ]; then
        AUTO_TICK=0
        auto_deploy_if_ahead
    fi

    sleep "$POLL_INTERVAL"
done
