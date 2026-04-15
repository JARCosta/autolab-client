from __future__ import annotations

import threading
from collections import deque


class MetricsBuffer:
    """Thread-safe bounded in-memory metrics buffer."""

    def __init__(self, *, max_samples: int):
        self._buffer: deque[dict] = deque(maxlen=max_samples)
        self._lock = threading.Lock()

    def append(self, sample: dict) -> None:
        with self._lock:
            self._buffer.append(sample)

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    def pop_chunk(self, n: int) -> list[dict]:
        count = max(1, int(n))
        with self._lock:
            take = min(count, len(self._buffer))
            if take <= 0:
                return []
            return [self._buffer.popleft() for _ in range(take)]

    def requeue_front(self, samples: list[dict]) -> None:
        if not samples:
            return
        with self._lock:
            # Reinsert failed samples at the front, preserving original order.
            self._buffer.extendleft(reversed(samples))

    def peek_recent(self, seconds: int) -> list[dict]:
        n = max(1, int(seconds))
        with self._lock:
            if not self._buffer:
                return []
            return list(self._buffer)[-n:]
