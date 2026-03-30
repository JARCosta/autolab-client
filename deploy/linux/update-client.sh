#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_DIR}"

if [[ -d .git ]]; then
  echo "Pulling latest client from git..."
  git pull
else
  echo "No .git here; update files manually or re-clone."
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found; skipping image rebuild."
  exit 0
fi

echo "Rebuilding Docker image..."
sudo docker build -t autolab-client:latest "${PROJECT_DIR}"

if systemctl is-enabled autolab-client.service &>/dev/null; then
  echo "Restarting autolab-client.service..."
  sudo systemctl restart autolab-client.service
  sudo systemctl status autolab-client.service --no-pager || true
else
  echo "autolab-client.service not enabled; start it after install-systemd.sh if needed."
fi
