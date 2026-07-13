#!/bin/bash
# Crypto Wallet Tracker — One-line installer
# curl -fsSL https://raw.githubusercontent.com/LostInTheBugs/Crypto-Wallet-Tracker/main/install.sh | sudo bash

set -e

APP_DIR="/opt/crypto-wallet-tracker"
ENV_FILE="$APP_DIR/.env"

echo "=== Crypto Wallet Tracker Installer ==="

# Install Docker if needed
if ! command -v docker &>/dev/null; then
  echo "[*] Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

# Clone or update repo
if [ -d "$APP_DIR/.git" ]; then
  echo "[*] Updating existing installation..."
  cd "$APP_DIR" && git pull
else
  echo "[*] Cloning repository..."
  git clone https://github.com/LostInTheBugs/Crypto-Wallet-Tracker.git "$APP_DIR"
fi

cd "$APP_DIR"

# Create .env if not exists
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
# Crypto Wallet Tracker configuration
PORT=80
SESSION_SECRET=***
# Optional: Alchemy API key for better token discovery
# ALCHEMY_API_KEY=
EOF
  echo "[*] Created $ENV_FILE — edit if needed"
fi

# Build and start
echo "[*] Building Docker image..."
docker compose build --pull

echo "[*] Starting services..."
docker compose up -d

echo ""
echo "=== Installation complete ==="
echo "Open http://$(hostname -I | awk '{print $1}'):80"
echo "Config: $ENV_FILE"
echo "Logs:   docker compose logs -f"
