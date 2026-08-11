"""Bookkeeping for request/response pairs on the Codex transport."""

import asyncio


class PendingRPCs:
    """Own request ids and futures without knowing the wire protocol."""

    def __init__(self):
        self._next = 0
        self.entries = {}

    def allocate(self, method):
        self._next += 1
        future = asyncio.get_event_loop().create_future()
        self.entries[self._next] = (future, method)
        return self._next, future

    def remove(self, request_id):
        return self.entries.pop(request_id, None)

    def fail(self, error):
        for future, _method in self.entries.values():
            if not future.done():
                future.set_exception(error)
        self.entries.clear()

    def cancel(self):
        for future, _method in self.entries.values():
            if not future.done():
                future.cancel()
        self.entries.clear()
