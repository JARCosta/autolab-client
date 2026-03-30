#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/linux/install-systemd.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SERVICE_TEMPLATE="${PROJECT_DIR}/deploy/linux/autolab-client.service"
SERVICE_FILE="/etc/systemd/system/autolab-client.service"

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
  echo "Missing ${PROJECT_DIR}/.env (copy from .env.example first)."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but not installed."
  exit 1
fi

echo "Building docker image..."
docker build -t autolab-client:latest "${PROJECT_DIR}"

echo "Installing systemd unit..."
sed "s#__WORKDIR__#${PROJECT_DIR}#g" "${SERVICE_TEMPLATE}" > "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable autolab-client.service
systemctl restart autolab-client.service

echo "Service installed and started."
echo "Check status with: systemctl status autolab-client.service"
