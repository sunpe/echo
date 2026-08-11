"""Conversation scenarios at Echo's Codex transport boundary."""

import asyncio
import unittest
from unittest.mock import patch

from echo.domain.messages.message import CodexAgentOptions, MessageType
from echo.providers.codex.client import CodexAgent


class ScriptedAppServer:
    """In-memory transport that behaves like a small app-server."""

    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.thread_id = "echo-thread"

    async def connect(self):
        return None

    async def send(self, envelope):
        self.sent.append(envelope)
        request_id = envelope.get("id")
        if request_id is None:
            return
        method = envelope.get("method")
        params = envelope.get("params") or {}
        if method == "initialize":
            self.reply(request_id, {"capabilities": {}})
        elif method == "model/list":
            self.reply(request_id, {"data": []})
        elif method == "thread/start":
            self.reply(request_id, {"thread": {"id": self.thread_id}})
        elif method == "turn/start":
            self.reply(request_id, {})
            self.emit_turn(request_id, params)

    def reply(self, request_id, result):
        self.incoming.put_nowait({"id": request_id, "result": result})

    def notify(self, method, params):
        self.incoming.put_nowait({"method": method, "params": params})

    def emit_turn(self, request_id, params):
        text = params.get("input", [{}])[0].get("text", "")
        turn_id = "turn-{}".format(request_id)
        self.notify("turn/started", {"turnId": turn_id})
        if text == "Run ls":
            item = {
                "type": "commandExecution",
                "id": "command-{}".format(request_id),
                "command": "/bin/zsh -lc 'ls'",
                "commandActions": [{"type": "listFiles", "command": "ls"}],
            }
            self.notify("item/started", {"item": item})
            completed = dict(item)
            completed["status"] = "completed"
            self.notify("item/completed", {"item": completed})
        else:
            self.notify("item/agentMessage/delta", {
                "itemId": "message-{}".format(request_id),
                "delta": "reply:{}".format(text),
            })
        self.notify("turn/completed", {"turnId": turn_id})

    async def messages(self):
        while not self.closed:
            yield await self.incoming.get()

    async def close(self):
        self.closed = True

    def calls(self, method):
        return [item for item in self.sent if item.get("method") == method]


class CodexConversationScenario(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = ScriptedAppServer()
        self.transport_patch = patch(
            "echo.providers.codex.client.WebSocketTransport",
            return_value=self.server,
        )
        self.transport_patch.start()
        self.agent = None

    async def asyncTearDown(self):
        if self.agent is not None:
            await self.agent.disconnect()
        self.transport_patch.stop()

    async def connect(self, model=None):
        self.agent = CodexAgent(CodexAgentOptions(
            app_server_url="ws://127.0.0.1:4500",
            model=model,
        ))
        await self.agent.connect()
        return self.agent

    async def run_turn(self, prompt):
        await self.agent.send_message(prompt)
        received = []
        async for message in self.agent.receive_messages():
            received.append(message)
            if message.type == MessageType.STOP.value:
                return received
        self.fail("message stream ended before stop")

    async def test_two_turns_share_thread_and_keep_responses_separate(self):
        await self.connect()

        first = await self.run_turn("first")
        second = await self.run_turn("second")

        def text(messages):
            return [m.content for m in messages if m.type == MessageType.TEXT.value]

        self.assertEqual(["reply:first"], text(first))
        self.assertEqual(["reply:second"], text(second))
        self.assertEqual("echo-thread", self.agent.thread_id)
        self.assertEqual(2, len(self.server.calls("turn/start")))

    async def test_default_placeholder_never_crosses_transport(self):
        await self.connect(model=" default ")
        await self.run_turn("use server choice")

        thread_params = self.server.calls("thread/start")[0]["params"]
        turn_params = self.server.calls("turn/start")[0]["params"]
        self.assertNotIn("model", thread_params)
        self.assertNotIn("model", turn_params)
        self.assertIsNone(
            turn_params["collaborationMode"]["settings"]["model"]
        )

    async def test_steering_reuses_active_turn_as_expectation(self):
        await self.connect()
        self.agent._active_turn_id = "active-turn"

        await self.agent.steer("continue with implementation")

        params = self.server.calls("turn/start")[-1]["params"]
        self.assertEqual("active-turn", params["expectedTurnId"])
        self.assertEqual("echo-thread", params["threadId"])
        self.assertEqual(
            "continue with implementation", params["input"][0]["text"]
        )

    async def test_command_event_is_emitted_once_with_clean_command(self):
        await self.connect()

        messages = await self.run_turn("Run ls")

        tools = [m for m in messages if m.type == MessageType.TOOL_USE.value]
        self.assertEqual(1, len(tools))
        self.assertEqual("command_execution", tools[0].content["name"])
        self.assertEqual("ls", tools[0].content["command"])


if __name__ == "__main__":
    unittest.main()
