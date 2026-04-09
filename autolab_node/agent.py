from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import socket
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .hardware_client import (
    get_local_device_name,
    normalize_device_name,
    push_samples,
    sample_system_metrics,
    verify_nvidia_gpu,
)
from .logging_config import setup_logging

log = setup_logging("autolab_node")

NODE_FETCH_PATH = "/api/node/hardware/fetch"
PUSH_CHUNK_SIZE = 21600
MAX_BUFFER_SAMPLES = 86400
REGISTER_SNAPSHOT_SECONDS = 5
BATCH_SEND_INTERVAL = 30


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _detect_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class NodeDaemon:
    """Continuously samples hardware metrics into a local buffer.

    Lifecycle:
    1. Background sampler writes 1 sample/sec into a bounded deque.
    2. On startup the daemon registers with the server (POST /api/monitor/register).
    3. An HTTP endpoint waits for the server to request data (POST /api/node/hardware/fetch).
    4. On fetch: the full buffer is flushed and pushed to the server, then
       streaming mode starts.
    5. Streaming pushes batched history every 30 seconds while viewers are
       active; each push response includes ``pong`` (viewer active). If there is
       nothing to push in an interval, the node falls back to ``/api/monitor/ping``.
    """

    def __init__(
        self,
        *,
        push_url: str,
        push_token: str,
        pull_token: str,
        device: str,
        node_url: str,
        host: str,
        port: int,
        verbose: bool,
    ):
        self._push_url = push_url
        self._push_token = push_token
        self._pull_token = pull_token
        self._device = device
        self._node_url = node_url
        self._host = host
        self._port = port
        self._verbose = verbose

        self._buffer: deque[dict] = deque(maxlen=MAX_BUFFER_SAMPLES)
        self._buffer_lock = threading.Lock()

        self._streaming = threading.Event()
        self._stop = threading.Event()
        self._srv: ThreadingHTTPServer | None = None
        self._shutdown_flush_done = False

    # -- URL helpers --------------------------------------------------------

    def _server_api_url(self, path: str) -> str:
        base = self._push_url.rstrip("/")
        if base.endswith("/push"):
            base = base[:-5]
        return f"{base}/{path.lstrip('/')}"

    # -- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        verify_nvidia_gpu()

        atexit.register(self._atexit_flush)
        self._install_signal_handlers()

        threading.Thread(target=self._sample_loop, daemon=True, name="sampler").start()
        threading.Thread(target=self._stream_loop, daemon=True, name="streamer").start()
        threading.Thread(target=self._register_loop, daemon=True, name="registrar").start()

        log.info(
            "Daemon started (device=%s, listen=%s:%d, push=%s)",
            self._device,
            self._host,
            self._port,
            self._push_url,
        )
        http_thread = threading.Thread(
            target=self._run_http_server, name="http", daemon=False
        )
        http_thread.start()
        try:
            http_thread.join()
        except KeyboardInterrupt:
            self._graceful_stop("keyboard interrupt")
            http_thread.join(timeout=120)

    # -- Sampling -----------------------------------------------------------

    def _sample_loop(self) -> None:
        while not self._stop.is_set():
            m = sample_system_metrics()
            if not m:
                self._stop.wait(timeout=2)
                continue
            ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            with self._buffer_lock:
                self._buffer.append({"timestamp": ts, **m})

    def _buffer_len(self) -> int:
        with self._buffer_lock:
            return len(self._buffer)

    def _pop_buffer_chunk(self, n: int) -> list[dict]:
        size = max(1, int(n))
        with self._buffer_lock:
            count = min(size, len(self._buffer))
            if count <= 0:
                return []
            return [self._buffer.popleft() for _ in range(count)]

    def _requeue_front(self, samples: list[dict]) -> None:
        if not samples:
            return
        with self._buffer_lock:
            # Reinsert failed samples at the front, preserving original order.
            self._buffer.extendleft(reversed(samples))

    def _peek_recent_buffer(self, seconds: int) -> list[dict]:
        n = max(1, int(seconds))
        with self._buffer_lock:
            if not self._buffer:
                return []
            return list(self._buffer)[-n:]

    # -- Push helpers -------------------------------------------------------

    def _push_chunked(self) -> tuple[int, bool | None]:
        """Push buffered samples in chunks; keep unsent chunks queued."""
        total = 0
        last_pong: bool | None = None
        while True:
            chunk = self._pop_buffer_chunk(PUSH_CHUNK_SIZE)
            if not chunk:
                break
            ok, msg, pong = push_samples(
                self._push_url, self._push_token, self._device, chunk
            )
            if ok:
                total += len(chunk)
                last_pong = pong
            else:
                self._requeue_front(chunk)
                log.warning("Push chunk failed (%d samples): %s", len(chunk), msg)
                break
        return total, last_pong

    def _flush_and_push_all(self) -> int:
        """Send every buffered sample to the server (best-effort, one shot)."""
        if self._shutdown_flush_done:
            return 0
        buffered = self._buffer_len()
        self._shutdown_flush_done = True
        if buffered <= 0:
            return 0
        n, _pong = self._push_chunked()
        log.info("Shutdown flush: pushed %d sample(s) to server (buffer had %d)", n, buffered)
        return n

    def _graceful_stop(self, reason: str) -> None:
        """Stop workers, flush buffer to server, stop HTTP thread."""
        log.info("Graceful shutdown (%s)", reason)
        self._stop.set()
        # Unblock _stream_loop when it is idle on self._streaming.wait().
        self._streaming.set()
        try:
            if not self._shutdown_flush_done:
                self._flush_and_push_all()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            log.warning("Shutdown flush failed: %s", exc)
        if self._srv is not None:
            try:
                self._srv.shutdown()
            except OSError:
                pass

    def _atexit_flush(self) -> None:
        if self._shutdown_flush_done:
            return
        try:
            self._flush_and_push_all()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def _install_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT on the main thread (required by Python)."""

        def handler(signum: int, _frame) -> None:
            self._graceful_stop(f"signal {signum}")

        for name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    # -- Fetch handler (called by HTTP server) ------------------------------

    def handle_fetch(self) -> dict:
        buffered = self._buffer_len()
        pushed, _pong = self._push_chunked()
        self._streaming.set()
        if self._verbose:
            log.info(
                "Fetch: flushed %d buffered, pushed %d, streaming activated",
                buffered,
                pushed,
            )
        return {"ok": True, "inserted": pushed, "device": self._device, "streaming": True}

    # -- Streaming loop -----------------------------------------------------

    def _stream_loop(self) -> None:
        while not self._stop.is_set():
            self._streaming.wait()
            if self._stop.is_set():
                break

            log.info("Streaming started for '%s'", self._device)

            while self._streaming.is_set() and not self._stop.is_set():
                self._stop.wait(timeout=BATCH_SEND_INTERVAL)
                if self._stop.is_set():
                    break

                pushed, pong = self._push_chunked()
                if pushed > 0:
                    if self._verbose:
                        log.info("Stream batch: %d sample(s) pushed", pushed)
                    viewer_active = True if pong is None else pong
                else:
                    viewer_active = self._ping()

                if viewer_active is False:
                    self._streaming.clear()
                    log.info("No active viewer (pong) — returning to idle buffering")
                    break

    # -- Ping / pong --------------------------------------------------------

    def _ping(self) -> bool:
        try:
            import requests
        except ImportError:
            return False

        url = self._server_api_url("ping")
        try:
            r = requests.post(
                url,
                json={"token": self._push_token, "device": self._device},
                timeout=15,
            )
            if r.status_code >= 400:
                return False
            return bool(r.json().get("pong", False))
        except (requests.RequestException, ValueError) as exc:
            log.warning("Ping failed: %s", exc)
            return False

    # -- Registration -------------------------------------------------------

    def _register_loop(self) -> None:
        try:
            import requests as req_lib
        except ImportError:
            log.warning("requests not installed; cannot register with server")
            return

        url = self._server_api_url("register")
        # Give sampler a few seconds so register can include real data.
        self._stop.wait(timeout=max(1, REGISTER_SNAPSHOT_SECONDS))
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            payload = {
                "token": self._push_token,
                "device": self._device,
                "node_url": self._node_url,
                "samples": self._peek_recent_buffer(REGISTER_SNAPSHOT_SECONDS),
            }
            try:
                r = req_lib.post(url, json=payload, timeout=15)
                if r.status_code < 400:
                    inserted = None
                    try:
                        inserted = r.json().get("inserted")
                    except ValueError:
                        pass
                    log.info(
                        "Registered with server (device=%s, url=%s, inserted=%s)",
                        self._device,
                        self._node_url,
                        inserted if inserted is not None else "n/a",
                    )
                    return
                log.warning(
                    "Registration rejected (status=%d): %s",
                    r.status_code,
                    r.text[:200],
                )
            except req_lib.RequestException as exc:
                log.warning("Registration attempt %d failed: %s", attempt, exc)
            # Keep retrying until the server is up (no fixed attempt cap).
            backoff = min(60, 2 ** min(attempt, 6))
            if self._stop.wait(timeout=backoff):
                return

    # -- HTTP server --------------------------------------------------------

    def _run_http_server(self) -> None:
        daemon = self
        pull_token = self._pull_token

        class Handler(BaseHTTPRequestHandler):
            server_version = "AutoLabNode/1.0"

            def log_message(self, fmt: str, *args) -> None:  # pylint: disable=arguments-differ
                _ = (fmt, args)
                return

            def _json_response(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if self.path.rstrip("/") != NODE_FETCH_PATH:
                    self._json_response(404, {"error": "not found"})
                    return

                raw = self.rfile.read(
                    int(self.headers.get("Content-Length", "0") or 0)
                )
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._json_response(400, {"error": "invalid json"})
                    return

                if data.get("token") != pull_token:
                    self._json_response(401, {"error": "unauthorized"})
                    return

                result = daemon.handle_fetch()
                self._json_response(200, result)

        log.info(
            "Listening on http://%s:%d%s (device=%s)",
            self._host,
            self._port,
            NODE_FETCH_PATH,
            self._device,
        )
        self._srv = ThreadingHTTPServer((self._host, self._port), Handler)
        try:
            self._srv.serve_forever()
        finally:
            if not self._shutdown_flush_done:
                try:
                    self._flush_and_push_all()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            self._srv.server_close()
            self._srv = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AutoLab node: sample local hardware and push to server."
    )
    parser.add_argument("--url", default="", help="Override HARDWARE_PUSH_URL")
    parser.add_argument("--token", default="", help="Override HARDWARE_TOKEN")
    parser.add_argument("--device", default="", help="Override HARDWARE_DEVICE_NAME")
    parser.add_argument(
        "--node-url",
        default="",
        help="Override HARDWARE_NODE_URL (server-reachable URL for this node)",
    )
    parser.add_argument(
        "--listen-host",
        default=os.getenv("HARDWARE_NODE_LISTEN_HOST")
        or os.getenv("HARDWARE_PULL_HOST", "0.0.0.0"),
        help="Bind host (HARDWARE_NODE_LISTEN_HOST or legacy HARDWARE_PULL_HOST)",
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        default=int(
            os.getenv("HARDWARE_NODE_LISTEN_PORT")
            or os.getenv("HARDWARE_PULL_PORT", "8765")
        ),
        help="Bind port (HARDWARE_NODE_LISTEN_PORT or legacy HARDWARE_PULL_PORT)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print per-cycle result"
    )
    return parser


def main() -> None:
    _load_dotenv()
    args = build_parser().parse_args()

    url = (args.url or os.getenv("HARDWARE_PUSH_URL", "")).strip()
    token = (args.token or os.getenv("HARDWARE_TOKEN", "")).strip()
    pull_token = token
    device = normalize_device_name(args.device) or get_local_device_name()

    if not url or not token or not device:
        print(
            "Missing required config. Set HARDWARE_PUSH_URL and HARDWARE_TOKEN "
            "(HARDWARE_DEVICE_NAME is optional).",
            file=sys.stderr,
        )
        sys.exit(1)

    if not pull_token:
        print(
            "Missing HARDWARE_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)
    node_url = (
        args.node_url
        or os.getenv("HARDWARE_NODE_URL", "")
        or os.getenv("HARDWARE_CLIENT_URL", "")
    ).strip()
    if not node_url:
        listen_host = args.listen_host
        if listen_host == "0.0.0.0":
            listen_host = _detect_lan_ip()
        node_url = f"http://{listen_host}:{args.listen_port}"

    NodeDaemon(
        push_url=url,
        push_token=token,
        pull_token=pull_token,
        device=device,
        node_url=node_url,
        host=args.listen_host,
        port=args.listen_port,
        verbose=args.verbose,
    ).start()


if __name__ == "__main__":
    main()
