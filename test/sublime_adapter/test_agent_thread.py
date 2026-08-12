import sys
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules.setdefault("sublime", MagicMock())

from echo.runtime.provider_worker import ProviderWorker
from echo.runtime.operation_mailbox import OperationMailbox, ProviderOperation
from echo.runtime.session_store import create_chat_session, register_chat_session_type


class FakeLoop:
    def __init__(self):
        self.callbacks = []

    def call_soon_threadsafe(self, callback, *args):
        self.callbacks.append((callback, args))
        callback(*args)


class FakeQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, value):
        self.items.append(value)


class ProviderWorkerInputTest(unittest.TestCase):
    def test_session_factory_avoids_view_module_import(self):
        register_chat_session_type(SimpleNamespace)
        session = create_chat_session(name="echo")

        self.assertEqual("echo", session.name)

    def test_inputs_sent_during_startup_are_drained_in_order(self):
        mailbox = OperationMailbox()
        first = ProviderOperation("send_message", ("first",))
        second = ProviderOperation("send_message", ("second",))
        third = ProviderOperation("send_message", ("third",))
        mailbox.offer(first)
        mailbox.offer(second)

        loop = FakeLoop()
        queue = FakeQueue()
        mailbox.attach(loop, queue)
        mailbox.offer(third)

        self.assertEqual([first, second, third], queue.items)
        self.assertEqual([], mailbox.pending)

    def test_runtime_config_is_applied_on_agent_loop(self):
        loop = FakeLoop()
        agent = SimpleNamespace(
            plan_mode=False,
            model=None,
            set_model=lambda value: setattr(agent, "model", value),
        )
        thread = ProviderWorker("/tmp", lambda _message: None)
        thread.loop = loop
        thread.agent = agent

        thread.reconfigure(plan_mode=True, model="gpt-test")

        operation = thread._operations.pending[0]
        asyncio.run(thread._perform(operation))

        self.assertTrue(agent.plan_mode)
        self.assertEqual("gpt-test", agent.model)
        self.assertEqual(True, thread.agent_config["plan_mode"])

    def test_send_is_rejected_after_thread_stops(self):
        thread = ProviderWorker("/tmp", lambda _message: None)
        thread.running = False

        self.assertFalse(thread.enqueue("ignored"))
        self.assertEqual([], thread._operations.pending)


class ProviderWorkerLifecycleTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _configured_worker():
        worker = ProviderWorker(
            "/tmp",
            lambda _message: None,
            agent_config={
                "provider": "pi",
                "cli_path": "pi",
            },
        )
        worker._activate(asyncio.get_running_loop(), asyncio.Queue())
        return worker

    async def test_pi_does_not_require_a_workspace_bridge(self):
        captured = []
        thread = ProviderWorker(
            "/tmp",
            lambda _message: None,
            agent_config={"provider": "pi", "cli_path": "pi"},
        )
        thread._activate(asyncio.get_running_loop(), asyncio.Queue())

        def probe(_provider, options):
            captured.append(options)
            raise RuntimeError("probe")

        with patch("echo.runtime.provider_worker.create_agent", side_effect=probe):
            with self.assertRaisesRegex(RuntimeError, "probe"):
                await thread._serve()

        self.assertFalse(hasattr(
            captured[0], "developer_instructions_loader"
        ))
        self.assertFalse(hasattr(captured[0], "dynamic_tools"))

    async def test_input_failure_terminates_agent_loop(self):
        class FailingAgent:
            def __init__(self, _options):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def send_message(self, _text):
                raise RuntimeError("send failed")

            async def receive_messages(self):
                await asyncio.Event().wait()
                yield None

        thread = ProviderWorker(
            "/tmp",
            lambda _message: None,
            agent_config={
                "provider": "codex",
                "app_server": {"url": "ws://127.0.0.1:4500"},
            },
            local_tool_handler=MagicMock(),
        )
        loop = asyncio.get_running_loop()
        thread._activate(loop, asyncio.Queue())
        thread.operation_queue.put_nowait(ProviderOperation(
            "send_message", ("hello",), label="message", fatal=True,
        ))

        with patch(
            "echo.runtime.provider_worker.create_agent",
            side_effect=lambda _provider, options: FailingAgent(options),
        ):
            with self.assertRaisesRegex(RuntimeError, "send failed"):
                await thread._serve()

    async def test_pi_plan_mode_change_uses_provider_operation(self):
        thread = self._configured_worker()
        thread.agent = SimpleNamespace(set_plan_mode=AsyncMock())

        thread.reconfigure(plan_mode=True)
        operation = await thread.operation_queue.get()
        await thread._perform(operation)

        thread.agent.set_plan_mode.assert_awaited_once_with(True)

    async def test_provider_exit_preserves_the_stream_reason(self):
        class EndedAgent:
            stream_end_reason = "Pi process exited with code 2: fatal"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def receive_messages(self):
                if False:
                    yield None

        thread = self._configured_worker()
        with patch(
            "echo.runtime.provider_worker.create_agent",
            return_value=EndedAgent(),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Pi process exited with code 2: fatal"
            ):
                await thread._serve()

    async def test_rewind_failure_still_invokes_the_fork_callback(self):
        thread = self._configured_worker()
        thread.agent = SimpleNamespace(rewind=AsyncMock(
            side_effect=RuntimeError("rewind failed")
        ))

        results = []
        thread.fork("message-1", on_done=results.append)
        operation = await thread.operation_queue.get()
        with patch(
            "echo.runtime.provider_worker.sublime.set_timeout",
            side_effect=lambda fn, _delay: fn(),
        ):
            await thread._dispatch_operation(operation)

        self.assertEqual([None], results)

    async def test_intentional_stop_accepts_a_closed_event_stream(self):
        class EndedAgent:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def receive_messages(self):
                if False:
                    yield None

        thread = self._configured_worker()
        thread.stop()
        with patch(
            "echo.runtime.provider_worker.create_agent",
            return_value=EndedAgent(),
        ):
            await thread._serve()


if __name__ == "__main__":
    unittest.main()
