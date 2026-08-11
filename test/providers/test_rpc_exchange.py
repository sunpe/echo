import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from echo.domain.messages.errors import CodexRPCError
from echo.providers.codex.protocol import ProtocolRouter
from echo.transport.rpc_exchange import RPCExchange


class RPCExchangeTest(unittest.IsolatedAsyncioTestCase):
    async def test_response_completes_the_matching_request(self):
        sent = []

        async def send(envelope):
            sent.append(envelope)

        exchange = RPCExchange(send, lambda value: value or {}, lambda: 1)
        pending = asyncio.create_task(exchange.request("thread/read", {"id": 7}))
        await asyncio.sleep(0)

        request_id = sent[0]["id"]
        self.assertTrue(exchange.accept({"id": request_id, "result": "ok"}))
        self.assertEqual("ok", await pending)

    async def test_rpc_error_retains_originating_method(self):
        sent = []

        async def send(envelope):
            sent.append(envelope)

        exchange = RPCExchange(send, lambda value: value or {}, lambda: 1)
        pending = asyncio.create_task(exchange.request("turn/start"))
        await asyncio.sleep(0)
        exchange.accept({
            "id": sent[0]["id"],
            "error": {"code": -1, "message": "rejected"},
        })

        with self.assertRaises(CodexRPCError) as raised:
            await pending
        self.assertIn("turn/start", str(raised.exception))


class ProtocolRouterTest(unittest.IsolatedAsyncioTestCase):
    async def test_response_bypasses_event_handlers(self):
        owner = SimpleNamespace(
            _rpc=SimpleNamespace(accept=MagicMock()),
        )

        await ProtocolRouter(owner).dispatch({"id": 9, "result": {}})

        owner._rpc.accept.assert_called_once_with({"id": 9, "result": {}})

    async def test_notification_uses_declared_route(self):
        owner = SimpleNamespace(
            _event_turn_started=AsyncMock(),
        )

        await ProtocolRouter(owner).dispatch({
            "method": "turn/started",
            "params": {"turn": {"id": "turn-1"}},
        })

        owner._event_turn_started.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
