import asyncio
import unittest

from echo.providers.codex.approvals import ApprovalExchange


class ApprovalExchangeTest(unittest.IsolatedAsyncioTestCase):
    async def test_answer_payload_round_trips_through_ui_message(self):
        outgoing = asyncio.Queue()
        exchange = ApprovalExchange(outgoing)
        pending = asyncio.create_task(exchange.ask(
            "approval-1", "AskUserQuestion", {"questions": []}
        ))

        message = await outgoing.get()
        self.assertEqual("control_request", message.type)
        self.assertEqual("approval-1", message.content["request_id"])
        exchange.resolve("approval-1", {
            "behavior": "allow",
            "updatedInput": {"answers": {"choice": "yes"}},
        })

        self.assertEqual({"choice": "yes"}, await pending)

    async def test_cancel_all_denies_waiting_requests(self):
        outgoing = asyncio.Queue()
        exchange = ApprovalExchange(outgoing)
        pending = asyncio.create_task(exchange.ask("approval-2", "write", {}))
        await outgoing.get()

        exchange.cancel_all()

        self.assertFalse(await pending)


if __name__ == "__main__":
    unittest.main()
