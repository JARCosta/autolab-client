# autolab-client

Standalone AutoLab hardware client for remote machines.

It samples local CPU/RAM/GPU metrics and pushes batches to the AutoLab server endpoint:

- `POST /api/monitor/push`
- payload: `token`, `device`, and `samples`

## Features

- Samples once per second and uploads in batches (`HARDWARE_PUSH_INTERVAL` window)
- Device naming with safe normalization
- NVIDIA metrics via `nvidia-smi` when available
- CPU vendor and GPU vendor best-effort detection
- `--once` mode for smoke testing

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and set:

- `HARDWARE_PUSH_URL` (full server endpoint URL)
- `HARDWARE_PUSH_TOKEN` (must match server secret)
- `HARDWARE_DEVICE_NAME` (optional; defaults to hostname)
- `HARDWARE_PUSH_INTERVAL` (seconds per upload cycle; default `10`)

## Run (manual)

```bash
python3 -m autolab_client
```

Or smoke test one batch:

```bash
python3 -m autolab_client --once --verbose
```

## Linux: Docker + startup (systemd)

1) Build image and install service:

```bash
sudo bash deploy/linux/install-systemd.sh
```

2) Manage service:

```bash
sudo systemctl status autolab-client.service
sudo systemctl restart autolab-client.service
sudo journalctl -u autolab-client.service -f
```

This uses Docker under the hood and starts the client at machine boot.

## Windows: automatic startup task

Use PowerShell (preferably as Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\windows\install-startup-task.ps1
```

What it does:

- checks whether a scheduled task named `AutoLabClient` already exists
- if missing, creates a startup task (`AtStartup`) running as `SYSTEM`
- if admin rights are not available, falls back to a user logon task

To run immediately after installing:

```powershell
Start-ScheduledTask -TaskName AutoLabClient
```

## Notes

- If `nvidia-smi` is unavailable, GPU fields are sent as `null`.
- Network failures are logged and retried on next cycle.
- For host-accurate metrics, native Python/systemd is usually best. Docker is included for easier deployment.
