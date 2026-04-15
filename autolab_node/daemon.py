from __future__ import annotations

import atexit
import json
import signal
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .buffer_store import MetricsBuffer
from .hardware_client import push_samples, sample_system_metrics, verify_nvidia_gpu
from .logging_config import setup_logging
from .server_client import ping_server, register_node

log = setup_logging("autolab_node")

NODE_FETCH_PATH = "/api/node/hardware/fetch"
PUSH_CHUNK_SIZE = 21600
MAX_BUFFER_SAMPLES = 86400
REGISTER_SNAPSHOT_SECONDS = 5
BATCH_SEND_INTERVAL = 30


class NodeDaemon:
    """Run local sampling and stream snapshots to the AutoLab server on demand."""

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

        self._buffer = MetricsBuffer(max_samples=MAX_BUFFER_SAMPLES)
        self._streaming = threading.Event()
        self._stop = threading.Event()
        self._srv: ThreadingHTTPServer | None = None
        self._shutdown_flush_done = False

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

    def _sample_loop(self) -> None:
        while not self._stop.is_set():
            metrics = sample_system_metrics()
            if not metrics:
                self._stop.wait(timeout=2)
                continue
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._buffer.append({"timestamp": timestamp, **metrics})

    def _push_chunked(self) -> tuple[int, bool | None]:
        total = 0
        last_pong: bool | None = None
        while True:
            chunk = self._buffer.pop_chunk(PUSH_CHUNK_SIZE)
            if not chunk:
                break
            ok, msg, pong = push_samples(
                self._push_url, self._push_token, self._device, chunk
            )
            if ok:
                total += len(chunk)
                last_pong = pong
            else:
                self._buffer.requeue_front(chunk)
                log.warning("Push chunk failed (%d samples): %s", len(chunk), msg)
                break
        return total, last_pong

    def _flush_and_push_all(self) -> int:
        if self._shutdown_flush_done:
            return 0
        buffered = self._buffer.size()
        self._shutdown_flush_done = True
        if buffered <= 0:
            return 0
        pushed, _pong = self._push_chunked()
        log.info(
            "Shutdown flush: pushed %d sample(s) to server (buffer had %d)",
            pushed,
            buffered,
        )
        return pushed

    def _graceful_stop(self, reason: str) -> None:
        log.info("Graceful shutdown (%s)", reason)
        self._stop.set()
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

    def handle_fetch(self) -> dict:
        buffered = self._buffer.size()
        pushed, _pong = self._push_chunked()
        self._streaming.set()
        if self._verbose:
            log.info(
                "Fetch: flushed %d buffered, pushed %d, streaming activated",
                buffered,
                pushed,
            )
        return {"ok": True, "inserted": pushed, "device": self._device, "streaming": True}

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
                    viewer_active = ping_server(
                        push_url=self._push_url,
                        token=self._push_token,
                        device=self._device,
                    )

                if viewer_active is False:
                    self._streaming.clear()
                    log.info("No active viewer (pong) - returning to idle buffering")
                    break

    def _register_loop(self) -> None:
        self._stop.wait(timeout=max(1, REGISTER_SNAPSHOT_SECONDS))
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            ok, msg = register_node(
                push_url=self._push_url,
                token=self._push_token,
                device=self._device,
                node_url=self._node_url,
                samples=self._buffer.peek_recent(REGISTER_SNAPSHOT_SECONDS),
            )
            if ok:
                log.info(
                    "Registered with server (device=%s, url=%s, %s)",
                    self._device,
                    self._node_url,
                    msg,
                )
                return
            log.warning("Registration attempt %d failed: %s", attempt, msg)
            backoff = min(60, 2 ** min(attempt, 6))
            if self._stop.wait(timeout=backoff):
                return

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
