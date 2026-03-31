from __future__ import annotations

import csv
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .logging_config import setup_logging

log = setup_logging("autolab_client")

_DEVICE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_TEMP_KEYS = ("coretemp", "k10temp", "cpu_thermal", "acpitz", "zenpower")

_nvidia_ok: bool | None = None
_nvidia_message: str = ""
_cpu_vendor_cache: str | None = None
_gpu_lspci_cache: str | None = None


def normalize_device_name(name: str | None) -> str:
    if not name or not isinstance(name, str):
        return ""
    s = name.strip()
    if not s or len(s) > 64 or not _DEVICE_RE.match(s):
        return ""
    return s


def _installer_computer_name() -> str:
    if sys.platform == "win32":
        n = os.environ.get("COMPUTERNAME", "").strip()
        if n:
            return n
    n = os.environ.get("HOSTNAME", "").strip()
    if n:
        return n.split(".")[0]
    try:
        return socket.gethostname().split(".")[0]
    except OSError:
        return ""


def get_local_device_name() -> str:
    env = normalize_device_name(os.getenv("HARDWARE_DEVICE_NAME", "").strip())
    if env:
        return env
    raw = _installer_computer_name()
    return normalize_device_name(raw) or "local"


def _nvidia_kb_s_to_mbps(kb_s: float | None) -> float | None:
    if kb_s is None:
        return None
    return kb_s / 1024.0


def _parse_csv_gpu_line(line: str) -> list[str | None]:
    try:
        row = next(csv.reader(io.StringIO(line.strip())))
    except StopIteration:
        return []
    out: list[str | None] = []
    for raw in row:
        s = raw.strip() if raw else ""
        out.append(None if not s or s == "[N/A]" else s)
    return out


def _run_nvidia_query(query_fields: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nvidia-smi", "--query-gpu=" + query_fields, "--format=csv,noheader,nounits", "-i", "0"],
        capture_output=True,
        text=True,
        timeout=8,
    )


def _query_nvidia_gpu_metrics() -> dict[str, Any] | None:
    global _nvidia_message
    full = (
        "name,utilization.gpu,memory.used,memory.total,temperature.gpu,"
        "clocks.current.graphics,pcie.tx_util,pcie.rx_util"
    )
    minimal = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,clocks.current.graphics"
    proc = None
    for query_fields in (full, minimal):
        try:
            proc = _run_nvidia_query(query_fields)
        except (OSError, subprocess.SubprocessError) as e:
            _nvidia_message = str(e)[:200]
            return None
        if proc.returncode == 0 and proc.stdout.strip():
            break
    if proc is None or proc.returncode != 0:
        _nvidia_message = (proc.stderr or proc.stdout or "").strip()[:200] if proc else "nvidia-smi failed"
        return None
    line = proc.stdout.strip().split("\n", 1)[0].strip()
    p = _parse_csv_gpu_line(line)
    if len(p) < 6:
        _nvidia_message = "unexpected CSV shape"
        return None

    def _f(i: int) -> float | None:
        if i >= len(p) or p[i] is None:
            return None
        try:
            return float(p[i])
        except ValueError:
            return None

    util = _f(1)
    mem_used = _f(2)
    mem_total = _f(3)
    mem_pct = (mem_used / mem_total) * 100.0 if mem_used is not None and mem_total and mem_total > 0 else None
    return {
        "gpu_name": p[0].strip() if p[0] else None,
        "gpu_util": util,
        "gpu_mem_percent": mem_pct,
        "gpu_temp": _f(4),
        "gpu_clock": _f(5),
        "pcie_tx_mbps": _nvidia_kb_s_to_mbps(_f(6) if len(p) > 6 else None),
        "pcie_rx_mbps": _nvidia_kb_s_to_mbps(_f(7) if len(p) > 7 else None),
    }


def verify_nvidia_gpu() -> tuple[bool, str]:
    global _nvidia_ok, _nvidia_message
    if not shutil.which("nvidia-smi"):
        _nvidia_ok = False
        _nvidia_message = "nvidia-smi not on PATH"
        return False, _nvidia_message
    if _nvidia_ok is True:
        return True, f"OK ({_nvidia_message})"
    if _nvidia_ok is False:
        return False, _nvidia_message or "unavailable"
    m = _query_nvidia_gpu_metrics()
    if m is None:
        _nvidia_ok = False
        return False, _nvidia_message or "query-gpu failed"
    _nvidia_ok = True
    _nvidia_message = (m.get("gpu_name") or "GPU").strip()
    return True, f"OK ({_nvidia_message})"


def _sample_nvidia_fields() -> dict[str, Any]:
    global _nvidia_ok, _nvidia_message
    empty = {
        "gpu_util": None,
        "gpu_mem_percent": None,
        "gpu_temp": None,
        "gpu_clock": None,
        "pcie_tx_mbps": None,
        "pcie_rx_mbps": None,
    }
    if _nvidia_ok is False:
        return empty
    if not shutil.which("nvidia-smi"):
        _nvidia_ok = False
        _nvidia_message = "nvidia-smi not on PATH"
        return empty
    m = _query_nvidia_gpu_metrics()
    if m is None:
        _nvidia_ok = False
        return empty
    if _nvidia_ok is None:
        _nvidia_ok = True
        _nvidia_message = (m.get("gpu_name") or "GPU").strip()
    return {
        "gpu_util": m.get("gpu_util"),
        "gpu_mem_percent": m.get("gpu_mem_percent"),
        "gpu_temp": m.get("gpu_temp"),
        "gpu_clock": m.get("gpu_clock"),
        "pcie_tx_mbps": m.get("pcie_tx_mbps"),
        "pcie_rx_mbps": m.get("pcie_rx_mbps"),
    }


def _detect_cpu_vendor_uncached() -> str:
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            text = ""
        for line in text.splitlines():
            low = line.lower()
            if "vendor_id" in low and ":" in line:
                rhs = line.split(":", 1)[1].strip().lower()
                if "intel" in rhs or "genuineintel" in rhs:
                    return "intel"
                if "amd" in rhs or "authenticamd" in rhs:
                    return "amd"
    elif sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if r.returncode == 0 and r.stdout:
                s = r.stdout.strip().lower()
                if "intel" in s:
                    return "intel"
                if "amd" in s:
                    return "amd"
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform == "win32":
        try:
            kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 8}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            r = subprocess.run(["wmic", "cpu", "get", "Name"], **kwargs)
            if r.returncode == 0 and r.stdout:
                s = r.stdout.lower()
                if "intel" in s:
                    return "intel"
                if "amd" in s:
                    return "amd"
        except (OSError, subprocess.SubprocessError):
            pass
    return "unknown"


def detect_cpu_vendor() -> str:
    global _cpu_vendor_cache
    if _cpu_vendor_cache is not None:
        return _cpu_vendor_cache
    _cpu_vendor_cache = _detect_cpu_vendor_uncached()
    return _cpu_vendor_cache


def _detect_gpu_vendor_lspci() -> str:
    global _gpu_lspci_cache
    if _gpu_lspci_cache is not None:
        return _gpu_lspci_cache
    if not sys.platform.startswith("linux") or not shutil.which("lspci"):
        _gpu_lspci_cache = "unknown"
        return "unknown"
    try:
        r = subprocess.run(["lspci", "-nn"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        _gpu_lspci_cache = "unknown"
        return "unknown"
    if r.returncode != 0 or not r.stdout:
        _gpu_lspci_cache = "unknown"
        return "unknown"
    found_nvidia = False
    found_amd = False
    for line in r.stdout.splitlines():
        ll = line.lower()
        if "vga" not in ll and "3d" not in ll and "display" not in ll:
            continue
        if "nvidia" in ll:
            found_nvidia = True
        if "amd" in ll or "ati technologies" in ll:
            found_amd = True
    if found_nvidia:
        _gpu_lspci_cache = "nvidia"
        return "nvidia"
    if found_amd:
        _gpu_lspci_cache = "amd"
        return "amd"
    _gpu_lspci_cache = "unknown"
    return "unknown"


def resolve_gpu_vendor(nvidia_smi_works: bool) -> str:
    if nvidia_smi_works:
        return "nvidia"
    return _detect_gpu_vendor_lspci()


def sample_system_metrics() -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        log.warning("psutil not installed, skipping hardware sample")
        return {}

    cpu_load = psutil.cpu_percent(interval=1)
    freq = psutil.cpu_freq()
    cpu_clock = freq.current if freq else None

    cpu_temp = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for key in _TEMP_KEYS:
                if key in temps and temps[key]:
                    cpu_temp = temps[key][0].current
                    break
            if cpu_temp is None:
                first_key = next(iter(temps))
                if temps[first_key]:
                    cpu_temp = temps[first_key][0].current
    except (AttributeError, OSError):
        pass

    ram_percent = None
    swap_percent = None
    try:
        ram_percent = float(psutil.virtual_memory().percent)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        sw = psutil.swap_memory()
        swap_percent = float(sw.percent) if sw.total > 0 else 0.0
    except (OSError, ValueError, AttributeError):
        pass

    nv = _sample_nvidia_fields()
    return {
        "cpu_load": cpu_load,
        "cpu_clock": cpu_clock,
        "cpu_temp": cpu_temp,
        "ram_percent": ram_percent,
        "swap_percent": swap_percent,
        "gpu_util": nv["gpu_util"],
        "gpu_mem_percent": nv["gpu_mem_percent"],
        "gpu_temp": nv["gpu_temp"],
        "gpu_clock": nv["gpu_clock"],
        "pcie_tx_mbps": nv["pcie_tx_mbps"],
        "pcie_rx_mbps": nv["pcie_rx_mbps"],
        "cpu_vendor": detect_cpu_vendor(),
        "gpu_vendor": resolve_gpu_vendor(_nvidia_ok is True),
    }


def collect_samples_over_interval(
    interval_seconds: float,
    *,
    kill_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    n = max(1, int(round(float(interval_seconds))))
    out: list[dict[str, Any]] = []
    for _ in range(n):
        if kill_event is not None and kill_event.is_set():
            break
        m = sample_system_metrics()
        if not m:
            break
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        out.append({"timestamp": ts, **m})
    return out


def push_samples(
    url: str,
    token: str,
    device: str,
    samples: list[dict[str, Any]],
    *,
    timeout: float | None = None,
) -> tuple[bool, str]:
    try:
        import requests
    except ImportError:
        return False, "requests not installed"

    payload = {"token": token, "device": device, "samples": samples}
    req_timeout = timeout if timeout is not None else max(60.0, float(len(samples)) * 5.0)
    try:
        r = requests.post(url, json=payload, timeout=req_timeout)
        r.raise_for_status()
        return True, "ok"
    except requests.RequestException as e:
        return False, str(e)


def run_push_loop(
    url: str,
    token: str,
    device: str,
    interval: float,
    *,
    kill_event: threading.Event | None = None,
    verbose: bool = False,
) -> None:
    iv = max(1.0, float(interval))
    ngrok_min_iv = max(1.0, float(os.getenv("HARDWARE_PUSH_NGROK_MIN_INTERVAL", "60")))
    if "ngrok" in url.lower() and iv < ngrok_min_iv:
        log.warning(
            "Detected ngrok URL; increasing push interval from %ss to %ss to reduce free-tier request usage.",
            iv,
            ngrok_min_iv,
        )
        iv = ngrok_min_iv
    ok, gpu_msg = verify_nvidia_gpu()
    log.info(
        "Client started (interval=%ss -> ~%d samples/cycle, device=%s, url=%s, gpu=%s)",
        iv,
        max(1, int(round(iv))),
        device,
        url,
        gpu_msg if ok else f"off ({gpu_msg})",
    )

    while kill_event is None or not kill_event.is_set():
        samples = collect_samples_over_interval(iv, kill_event=kill_event)
        if not samples:
            if kill_event is not None and kill_event.wait(timeout=max(1.0, iv)):
                break
            if kill_event is None:
                time.sleep(max(1.0, iv))
            continue

        success, msg = push_samples(url, token, device, samples)
        if verbose:
            if success:
                print(time.strftime("%H:%M:%S"), "ok", len(samples), "samples", flush=True)
            else:
                print(time.strftime("%H:%M:%S"), "error:", msg, file=sys.stderr, flush=True)
        if not success:
            log.warning("Hardware push failed: %s", msg)

