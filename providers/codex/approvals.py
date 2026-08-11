"""Async rendezvous between app-server requests and the editor UI."""

import asyncio

from ...domain.messages.message import Message


class ApprovalExchange:
    def __init__(self, outgoing_messages):
        self._outgoing = outgoing_messages
        self._waiters = {}

    async def ask(self, request_id, tool, arguments):
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        previous = self._waiters.pop(request_id, None)
        if previous is not None and not previous.done():
            previous.cancel()
        self._waiters[request_id] = waiter

        await self._outgoing.put(Message(
            "control_request",
            content={
                "request_id": request_id,
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": tool,
                    "input": arguments,
                },
            },
        ))
        try:
            response = await waiter
        except asyncio.CancelledError:
            return False
        finally:
            self._waiters.pop(request_id, None)

        if response.get("behavior") != "allow":
            return False
        revised = response.get("updatedInput") or {}
        return revised.get("answers", True)

    def resolve(self, request_id, response):
        waiter = self._waiters.get(request_id)
        if waiter is None or waiter.done():
            return False
        waiter.set_result(response)
        return True

    def cancel_all(self):
        waiters, self._waiters = self._waiters, {}
        for waiter in waiters.values():
            if not waiter.done():
                waiter.cancel()
