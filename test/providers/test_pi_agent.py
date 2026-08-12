import unittest
import asyncio
from unittest.mock import AsyncMock, patch

from echo.domain.messages.message import PiAgentOptions
from echo.providers.pi.client import PiAgent, _RPC_STREAM_LIMIT


class PiAgentProtocolTest(unittest.TestCase):
    def test_resume_uses_pi_session_option(self):
        agent = PiAgent(PiAgentOptions(cli_path="/usr/bin/pi", session_id="session-1"))
        self.assertEqual(
            ["/usr/bin/pi", "--mode", "rpc", "--session", "session-1"],
            agent._command(),
        )

    def test_streaming_text_is_normalized(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))
        message = agent._parse({
            "type": "message_update",
            "id": "event-1",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": "hello",
            },
        })
        self.assertEqual("text_delta", message.type)
        self.assertEqual("hello", message.content)

    def test_agent_end_is_a_result(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))
        message = agent._parse({"type": "agent_end"})
        self.assertEqual("result", message.type)
        self.assertTrue(message.content["success"])

    def test_get_state_updates_the_active_model(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))
        message = agent._parse({
            "type": "response",
            "id": "state-1",
            "command": "get_state",
            "success": True,
            "data": {"model": {"provider": "openai", "id": "gpt-5.5"}},
        })
        self.assertEqual("model_update", message.type)
        self.assertEqual("openai/gpt-5.5", message.content["model"])
        self.assertEqual("openai/gpt-5.5", agent.options.model)

    def test_session_id_is_captured_from_rpc_event(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))
        asyncio.run(agent._capture_session_id({
            "type": "response",
            "command": "get_state",
            "success": True,
            "data": {
                "sessionId": "019fc842-edd7-7f67-b69c-cde89da2e6a4",
                "sessionFile": "/tmp/pi-session.jsonl",
            },
        }))
        self.assertEqual("019fc842-edd7-7f67-b69c-cde89da2e6a4", agent._session_id)
        self.assertEqual("019fc842-edd7-7f67-b69c-cde89da2e6a4", agent.options.session_id)
        message = agent._messages.get_nowait()
        self.assertEqual("thread_started", message.type)


class PiAgentLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_process_stream_accepts_large_rpc_lines(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))

        with patch(
            "echo.providers.pi.client.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=RuntimeError("stop after spawn")),
        ) as spawn:
            with self.assertRaisesRegex(RuntimeError, "stop after spawn"):
                await agent.connect()

        self.assertEqual(_RPC_STREAM_LIMIT, spawn.await_args.kwargs["limit"])
        self.assertGreater(_RPC_STREAM_LIMIT, 64 * 1024)

    async def test_outbound_actions_share_one_envelope_builder(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))
        agent.is_connected = True
        agent._transmit = AsyncMock()

        await agent.send_message("hello")
        await agent.steer("continue")
        await agent.steer("ignored", proceed_plan=True)
        await agent.interrupt()
        await agent.set_model("openai/gpt-test")
        await agent.set_plan_mode(True)

        payloads = [
            call.args[0] for call in agent._transmit.await_args_list
        ]
        self.assertEqual(
            ["prompt", "steer", "prompt", "abort", "set_model", "prompt"],
            [payload["type"] for payload in payloads],
        )
        self.assertEqual("hello", payloads[0]["message"])
        self.assertEqual("continue", payloads[1]["message"])
        self.assertEqual("/plan implement", payloads[2]["message"])
        self.assertEqual("gpt-test", payloads[4]["modelId"])
        self.assertEqual("/plan", payloads[5]["message"])

    async def test_message_stream_finishes_without_polling(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))
        await agent._message_queue().put(agent._parse({
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "one"},
        }))
        await agent._close_message_stream()

        messages = [message async for message in agent.receive_messages()]

        self.assertEqual(["one"], [message.content for message in messages])

    async def test_message_stream_reports_process_exit_reason(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))
        agent.stream_end_reason = "Pi process exited with code 2: fatal"
        await agent._close_message_stream()

        with self.assertRaisesRegex(
            RuntimeError, "Pi process exited with code 2: fatal"
        ):
            async for _message in agent.receive_messages():
                pass

    async def test_exit_reason_includes_the_latest_stderr_line(self):
        agent = PiAgent(PiAgentOptions(cli_path="pi"))
        agent.process = type("Process", (), {"returncode": 2})()
        agent._stderr_tail = ["warning", "fatal"]

        self.assertEqual(
            "Pi process exited with code 2: fatal",
            agent._exit_reason(),
        )
