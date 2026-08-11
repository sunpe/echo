"""Threaded provider worker with a supervised asyncio session."""

import asyncio
import inspect
import logging
import threading

import sublime

from ..domain.messages.message import Message
from ..domain.ports.workspace import DEFAULT_CONFIRM_TOOLS
from ..providers import build_agent_options, create_agent
from ..workspace import DEFAULT_ENABLED_TOOLS, dynamic_tool_specs
from ..domain.conversation.input_buffer import StartupInputBuffer


LOG = logging.getLogger("echo")


class ProviderWorker(threading.Thread):
    def __init__(self, cwd, on_message, agent_config=None, add_dirs=None,
                 local_tool_handler=None):
        super().__init__(daemon=True)
        self.cwd = cwd
        self.on_message = on_message
        self.agent_config = dict(agent_config or {})
        self.add_dirs = list(add_dirs or ())
        self.local_tool_handler = local_tool_handler
        self.loop = None
        self.agent = None
        self.input_queue = None
        self._stop_signal = None
        self._startup_inputs = StartupInputBuffer()
        self.running = True
        self._intentional_stop = False

    def run(self):
        failure = None
        try:
            asyncio.run(self._serve())
        except Exception as error:
            failure = error
            if self._intentional_stop:
                LOG.debug("Provider worker stopped during shutdown: %s", error)
            else:
                LOG.exception("Echo provider worker failed")
        finally:
            self.running = False
            self.agent = None
            self.input_queue = None
            self._stop_signal = None
            self.loop = None
        if failure is not None and not self._intentional_stop:
            detail = str(failure)
            self._publish(Message(
                "connection_state", content={"state": "failed", "detail": detail}
            ), force=True)
            self._publish(("error", detail), force=True)

    async def _serve(self):
        if self.input_queue is None:
            self._activate(asyncio.get_running_loop(), asyncio.Queue())
        provider = self.agent_config.get("provider")
        async with create_agent(provider, self._build_options(provider)) as agent:
            self.agent = agent
            self._publish(Message("connection_state", {"state": "ready"}))
            jobs = {
                "input": asyncio.create_task(self._pump_input()),
                "events": asyncio.create_task(self._pump_events()),
                "stop": asyncio.create_task(self._stop_signal.wait()),
            }
            done, _ = await asyncio.wait(
                jobs.values(), return_when=asyncio.FIRST_COMPLETED
            )
            try:
                if (
                    jobs["stop"] in done
                    or self._intentional_stop
                    or not self.running
                ):
                    return
                failures = [
                    task.exception() for task in done
                    if not task.cancelled() and task.exception() is not None
                ]
                if failures:
                    raise failures[0]
                if jobs["events"] in done:
                    reason = getattr(agent, "stream_end_reason", None)
                    raise RuntimeError(reason or (
                        "{} provider event stream closed".format(
                            provider or "configured"
                        )
                    ))
                raise RuntimeError("Provider input loop stopped unexpectedly")
            finally:
                for task in jobs.values():
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*jobs.values(), return_exceptions=True)

    def _activate(self, loop, queue):
        self.loop = loop
        self.input_queue = queue
        self._stop_signal = asyncio.Event()
        if not self.running:
            self._stop_signal.set()
        self._startup_inputs.activate(loop, queue)

    def _build_options(self, provider):
        local = self.agent_config.get("local_tools") or {}
        bridge = self.local_tool_handler
        if provider == "codex" and bridge is None:
            raise RuntimeError("Codex app-server requires a workspace bridge")
        enabled = local.get("enabled", DEFAULT_ENABLED_TOOLS)
        runtime = {
            "cwd": self.cwd,
            "add_dirs": self.add_dirs,
            "model": self.agent_config.get("model"),
            "plan_mode": self.agent_config.get("plan_mode", False),
            "disallowed_tools": self.agent_config.get("disallowed_tools"),
            "session_id": self.agent_config.get("session_id"),
            "app_server": self.agent_config.get("app_server") or {},
            "local_tool_handler": bridge,
            "dynamic_tools": dynamic_tool_specs(enabled) if provider == "codex" else [],
            "local_tools_require_approval": local.get("always_confirm", DEFAULT_CONFIRM_TOOLS),
            "developer_instructions_loader": (
                bridge.load_project_instructions if provider == "codex" else None
            ),
            "connection_state_callback": self._connection_changed,
            "request_fields_loader": self._request_fields if provider == "codex" else None,
            "cli_path": self.agent_config.get("cli_path"),
            "system_prompt": self.agent_config.get("system_prompt"),
            "env": self.agent_config.get("env") or {},
        }
        return build_agent_options(provider, runtime)

    @staticmethod
    def _request_fields():
        settings = sublime.load_settings("echo.sublime-settings")
        providers = settings.get("providers") or {}
        codex = providers.get("codex") or {} if isinstance(providers, dict) else {}
        server = codex.get("app_server") or {} if isinstance(codex, dict) else {}
        return server.get("request_fields") or {} if isinstance(server, dict) else {}

    def _connection_changed(self, state, detail):
        self._publish(Message("connection_state", {"state": state, "detail": detail}))

    async def _pump_input(self):
        while self.running:
            prompt = await self.input_queue.get()
            if prompt:
                await self.agent.send_message(prompt)

    async def _pump_events(self):
        async for event in self.agent.receive_messages():
            if not self.running:
                return
            self._publish(event)

    def enqueue(self, text):
        if not self.running:
            return False
        self._startup_inputs.send(text)
        return True

    def steer(self, text, proceed_plan=False):
        return self._submit(
            self.agent.steer(text, proceed_plan=proceed_plan) if self.agent else None,
            "steer",
        )

    def cancel_turn(self):
        return self._submit(self.agent.interrupt() if self.agent else None, "interrupt")

    def fork(self, message_id, on_done=None):
        async def operation():
            fork_id = None
            try:
                if self.agent and hasattr(self.agent, "rewind"):
                    fork_id = await self.agent.rewind(message_id)
            except Exception:
                LOG.exception("Provider conversation fork failed")
            if on_done:
                sublime.set_timeout(lambda: on_done(fork_id), 0)
        if not self._submit(operation(), "fork") and on_done:
            sublime.set_timeout(lambda: on_done(None), 0)

    def reply_approval(self, request_id, response):
        if self.agent is None or not hasattr(self.agent, "send_approval_response"):
            return False
        return self._submit(
            self.agent.send_approval_response(request_id, response), "approval"
        )

    def reconfigure(self, **changes):
        self.agent_config.update(changes)
        if self.agent is None or self.loop is None:
            return
        def apply():
            if "plan_mode" in changes:
                setter = getattr(self.agent, "set_plan_mode", None)
                result = setter(changes["plan_mode"]) if setter else None
                if setter is None:
                    self.agent.plan_mode = changes["plan_mode"]
                if inspect.isawaitable(result):
                    self.loop.create_task(result)
            if "model" in changes:
                result = self.agent.set_model(changes["model"])
                if inspect.isawaitable(result):
                    self.loop.create_task(result)
        self.loop.call_soon_threadsafe(apply)

    @property
    def session_id(self):
        live = getattr(self.agent, "_session_id", None) or getattr(self.agent, "thread_id", None)
        return live or self.agent_config.get("session_id")

    def stop(self):
        self._intentional_stop = True
        self.running = False
        if self.loop is not None and self._stop_signal is not None:
            self.loop.call_soon_threadsafe(self._stop_signal.set)

    def _submit(self, coroutine, label):
        if coroutine is None or not self.running or self.loop is None:
            if coroutine is not None:
                coroutine.close()
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        except RuntimeError:
            coroutine.close()
            return False
        def complete(result):
            if result.cancelled() or not self.running:
                return
            error = result.exception()
            if error:
                LOG.error("Provider %s failed: %s", label, error)
                self._publish(("error", str(error)))
        future.add_done_callback(complete)
        return True

    def _publish(self, event, force=False):
        sublime.set_timeout(
            lambda: self.on_message(event) if force or self.running else None, 0
        )
