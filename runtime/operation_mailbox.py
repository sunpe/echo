"""Thread-safe handoff of provider operations to the agent event loop."""

import threading
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderOperation:
    method: str
    arguments: tuple = ()
    keywords: dict = field(default_factory=dict)
    label: str = "operation"
    fatal: bool = False
    callback: object = None


class OperationMailbox:
    def __init__(self):
        self._guard = threading.Lock()
        self._destination = None
        self._loop = None
        self._backlog = []

    def attach(self, loop, destination):
        with self._guard:
            self._loop, self._destination = loop, destination
            backlog, self._backlog = self._backlog, []
        for operation in backlog:
            destination.put_nowait(operation)

    def offer(self, operation):
        with self._guard:
            if self._destination is None:
                self._backlog.append(operation)
                return
            loop, destination = self._loop, self._destination
        loop.call_soon_threadsafe(destination.put_nowait, operation)

    @property
    def pending(self):
        with self._guard:
            return list(self._backlog)
