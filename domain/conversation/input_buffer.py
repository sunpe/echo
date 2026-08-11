"""Thread-safe buffering for input submitted before the agent loop is ready."""

import threading


class StartupInputBuffer:
    def __init__(self):
        self._lock = threading.Lock()
        self._loop = None
        self._queue = None
        self._pending = []

    def activate(self, loop, queue):
        with self._lock:
            self._loop = loop
            self._queue = queue
            pending = self._pending
            self._pending = []
        for value in pending:
            queue.put_nowait(value)

    def send(self, value):
        with self._lock:
            if self._loop is None or self._queue is None:
                self._pending.append(value)
                return
            loop = self._loop
            queue = self._queue
        loop.call_soon_threadsafe(queue.put_nowait, value)

    @property
    def pending(self):
        with self._lock:
            return list(self._pending)
