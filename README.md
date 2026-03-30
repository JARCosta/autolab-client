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

## Windows: virtualenv + run

`deploy/windows/run-client.ps1` creates `.venv` next to the repo if it is missing, runs `pip install -r requirements.txt`, then starts the client with that interpreter (so `python-dotenv` and friends are always available).

```powershell
.\deploy\windows\run-client.ps1
```

If `py` is not on `PATH` (common for **SYSTEM** scheduled tasks), either:

- run the script once **interactively** as your user so `.venv` is created, then the task only needs `.venv\Scripts\python.exe`, or
- pass a full Python path: `.\deploy\windows\run-client.ps1 -PythonExe 'C:\Program Files\Python312\python.exe'`

## Windows: automatic startup task

Use PowerShell (preferably as Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\windows\install-startup-task.ps1
```

What it does:

- checks whether a scheduled task named `AutoLabClient` already exists
- if missing, creates a startup task (`AtStartup`) running as `SYSTEM`
- if admin rights are not available, falls back to a user logon task

The task runs `run-client.ps1`, which manages `.venv` and dependencies as above.

To run immediately after installing:

```powershell
Start-ScheduledTask -TaskName AutoLabClient
```

## Updating the client

You do **not** need to recreate the Windows scheduled task or reinstall the Linux systemd unit when code changes. Those only store paths to scripts on disk; they do not embed your app.

What you do need:

1. **Pull (or copy) the new code** into the same folder.
2. **Refresh dependencies** (`run-client.ps1` already runs `pip install -r requirements.txt` each time; Linux Docker needs a rebuild to pick up `requirements.txt` or code baked into the image).
3. **Restart the running process** so Python/Docker loads the new files. Until you restart, the old process keeps running.

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\windows\update-client.ps1
```

That runs `git pull`, updates `.venv` packages if the venv exists, then stops and starts the `AutoLabClient` scheduled task (default name) so a new process runs. If the task does not stop the old Python process on your Windows build, end `python.exe` for this app in Task Manager or reboot once.

### Linux (Docker + systemd)

```bash
bash deploy/linux/update-client.sh
```

That runs `git pull`, `docker build`, and `systemctl restart autolab-client.service`.

## Notes

- If `nvidia-smi` is unavailable, GPU fields are sent as `null`.
- Network failures are logged and retried on next cycle.
- For host-accurate metrics, native Python/systemd is usually best. Docker is included for easier deployment.
