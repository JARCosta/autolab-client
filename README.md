# autolab-node

Standalone AutoLab hardware node for remote machines.

It samples local CPU/RAM/GPU metrics in one mode: daemon pull/stream protocol.
The node samples continuously into a local buffer and only sends data when
the server requests it for active viewers.

### Serve mode lifecycle

1. Node starts, samples 1/sec into a local buffer (up to 24 h), registers with server.
2. Idle — no HTTP traffic until someone opens the monitor dashboard.
3. Viewer opens dashboard → server calls node → node flushes **all** buffered data and pushes it.
4. Node enters **streaming**: pushes buffered batches on a timer while viewing.
5. Each **push** response includes **pong** (viewer still watching). If a tick has no samples to push, the node uses **`/api/monitor/ping`** once for the same signal.
6. If the server responds with **pong** (someone is still watching) → streaming continues.
7. No pong → node stops streaming, returns to idle buffering.

## Features

- Continuous 1 sample/sec background sampling
- Device naming with safe normalization
- NVIDIA metrics via `nvidia-smi` when available
- CPU vendor and GPU vendor best-effort detection
- Single daemon mode (no periodic push loop)

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and set:

- `HARDWARE_PUSH_URL` (full server endpoint URL)
- `HARDWARE_TOKEN` (shared auth token used for both push and pull)
- `HARDWARE_DEVICE_NAME` (optional; defaults to hostname)
- `HARDWARE_NODE_LISTEN_PORT` (daemon listen port; default `8765`; legacy: `HARDWARE_PULL_PORT`)
- `HARDWARE_NODE_URL` (explicit reachable URL for the server, e.g. `http://192.168.1.22:8765`; legacy: `HARDWARE_CLIENT_URL`)
- `HARDWARE_NODE_LISTEN_HOST` (optional bind address; legacy: `HARDWARE_PULL_HOST`)

## Run (manual)

```bash
python3 -m autolab_node
```

The node registers itself with the server on startup, so no manual
`HARDWARE_PULL_NODES` map is required on the server (though you can set one
as a static fallback; legacy env name: `HARDWARE_PULL_CLIENTS`).

Data is only sent when someone opens the monitor dashboard (or on **graceful** node shutdown; see below).

### Server restarts vs. the website

- **SQLite on the server** keeps history across autolab restarts (see server `data/`).
- **In-memory** pieces (which nodes are registered, "viewer active") reset when the server process restarts; nodes **re-register** on their next attempt.
- If **nobody** opened the monitor before a restart, you only lose what was **never pushed** yet: while idle, samples stay in the **node buffer** (up to ~24 h). After the server is back, opening the dashboard triggers a **fetch** and the node flushes that backlog.
- So "charging" the graph is: **browser -> `/api/monitor/fetch` -> node** flushes buffer -> data appears in SQLite.

### Graceful shutdown (Linux, Windows, Docker)

On **SIGTERM** / **SIGINT** (and **Ctrl+C** when run in a terminal), the node **pushes any buffered samples** to `HARDWARE_PUSH_URL` before exiting — same HTTP batch path as streaming.

- **Linux systemd / `docker stop`**: default **SIGTERM** first → flush runs. Avoid `SIGKILL` / “End task” if you care about the last buffer.
- **Windows**: closing a console with **Ctrl+C** flushes; **Task Manager “End task”** may kill the process without running handlers.
- **Docker**: image entrypoint is `python -m autolab_node` (PID 1), so `docker stop` delivers SIGTERM to the node.

## Linux: Docker + startup (systemd)

1) Build image and install service:

```bash
sudo bash deploy/linux/install-systemd.sh
```

2) Manage service:

```bash
sudo systemctl status autolab-node.service
sudo systemctl restart autolab-node.service
sudo journalctl -u autolab-node.service -f
```

This uses Docker under the hood and starts the node at machine boot.
The service publishes port `8765` so the server can POST to this node at **`/api/node/hardware/fetch`** (relative to the registered base URL).

## Windows: virtualenv + run

`deploy/windows/run-node.ps1` creates `.venv` next to the repo if it is missing, runs `pip install -r requirements.txt`, then starts the node with that interpreter (so `python-dotenv` and friends are always available).

```powershell
.\deploy\windows\run-node.ps1
```

If `py` is not on `PATH` (common for **SYSTEM** scheduled tasks), either:

- run the script once **interactively** as your user so `.venv` is created, then the task only needs `.venv\Scripts\python.exe`, or
- pass a full Python path: `.\deploy\windows\run-node.ps1 -PythonExe 'C:\Program Files\Python312\python.exe'`

## Windows: automatic startup task

Use PowerShell (preferably as Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\windows\install-startup-task.ps1
```

What it does:

- checks whether a scheduled task named `AutoLabNode` already exists
- if missing, creates a startup task (`AtStartup`) running as `SYSTEM`
- if admin rights are not available, falls back to a user logon task

The task runs `run-node.ps1`, which manages `.venv` and dependencies as above.

To run immediately after installing:

```powershell
Start-ScheduledTask -TaskName AutoLabNode
```

## Updating the node

You do **not** need to recreate the Windows scheduled task or reinstall the Linux systemd unit when code changes. Those only store paths to scripts on disk; they do not embed your app.

What you do need:

1. **Pull (or copy) the new code** into the same folder.
2. **Refresh dependencies** (`run-node.ps1` already runs `pip install -r requirements.txt` each time; Linux Docker needs a rebuild to pick up `requirements.txt` or code baked into the image).
3. **Restart the running process** so Python/Docker loads the new files. Until you restart, the old process keeps running.

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\windows\update-node.ps1
```

That runs `git pull`, updates `.venv` packages if the venv exists, then stops and starts the `AutoLabNode` scheduled task (default name) so a new process runs. If the task does not stop the old Python process on your Windows build, end `python.exe` for this app in Task Manager or reboot once.

### Linux (Docker + systemd)

```bash
bash deploy/linux/update-node.sh
```

That runs `git pull`, `docker build`, and `systemctl restart autolab-node.service`.

## Notes

- If `nvidia-smi` is unavailable, GPU fields are sent as `null`.
- Network failures are logged and retried on next cycle.
- For host-accurate metrics, native Python/systemd is usually best. Docker is included for easier deployment.
