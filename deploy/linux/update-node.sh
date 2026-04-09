#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SERVICE_TEMPLATE="${PROJECT_DIR}/deploy/linux/autolab-node.service"
SERVICE_FILE="/etc/systemd/system/autolab-node.service"
cd "${PROJECT_DIR}"

if [[ -d .git ]]; then
  echo "Pulling latest node from git..."
  git pull
else
  echo "No .git here; update files manually or re-clone."
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found; skipping image rebuild."
  exit 0
fi

echo "Rebuilding Docker image..."
sudo docker build -t autolab-node:latest "${PROJECT_DIR}"

if [[ -f "${SERVICE_TEMPLATE}" ]]; then
  echo "Refreshing systemd unit from template..."
  sed "s#__WORKDIR__#${PROJECT_DIR}#g" "${SERVICE_TEMPLATE}" | sudo tee "${SERVICE_FILE}" >/dev/null
  sudo systemctl daemon-reload
fi

if systemctl is-enabled autolab-node.service &>/dev/null; then
  echo "Restarting autolab-node.service..."
  sudo systemctl restart autolab-node.service
  sudo systemctl status autolab-node.service --no-pager || true
else
  echo "autolab-node.service not enabled; start it after install-systemd.sh if needed."
fi
