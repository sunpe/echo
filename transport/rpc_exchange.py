"""Bidirectional JSON-RPC exchange independent of Codex business methods."""

import asyncio

from ..domain.messages.errors import CodexConnectionError, CodexRPCError, CodexRPCTimeout
from .pending_rpc import PendingRPCs


class RPCExchange:
    def __init__(self, send, prepare_params, default_timeout):
        self._send = send
        self._prepare_params = prepare_params
        self._default_timeout = default_timeout
        self.pending = PendingRPCs()

    @property
    def entries(self):
        return self.pending.entries

    async def request(self, method, params=None, timeout=None):
        request_id, future = self.pending.allocate(method)
        envelope = {
            "id": request_id,
            "method": method,
            "params": self._prepare_params(params),
        }
        try:
            await self._send(envelope)
        except Exception:
            self.pending.remove(request_id)
            future.cancel()
            raise

        wait_seconds = timeout or self._default_timeout()
        try:
            return await asyncio.wait_for(future, wait_seconds)
        except asyncio.TimeoutError:
            self.pending.remove(request_id)
            raise CodexRPCTimeout(
                "{} timed out after {} seconds".format(method, wait_seconds)
            )

    async def notify(self, method, params=None):
        await self._send({
            "method": method,
            "params": self._prepare_params(params),
        })

    async def respond(self, request_id, result):
        await self._send({"id": request_id, "result": result})

    def accept(self, envelope):
        pending = self.pending.remove(envelope.get("id"))
        if pending is None:
            return False
        future, method = pending
        if future.done():
            return True
        if "error" in envelope:
            future.set_exception(CodexRPCError(method, envelope["error"]))
        else:
            future.set_result(envelope.get("result"))
        return True

    def fail(self, message):
        self.pending.fail(CodexConnectionError(message))

    def cancel(self):
        self.pending.cancel()
