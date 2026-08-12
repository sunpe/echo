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
from .operation_mailbox import OperationMailbox, ProviderOperation


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
        self.operation_queue = None
        self._stop_signal = None
        self._operations = OperationMailbox()
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
            self.operation_queue = None
            self._stop_signal = None
            self.loop = None
        if failure is not None and not self._intentional_stop:
            detail = str(failure)
            self._publish(Message(
                "connection_state", content={"state": "failed", "detail": detail}
            ), force=True)
            self._publish(("error", detail), force=True)

    async def _serve(self):
        if self.operation_queue is None:
            self._activate(asyncio.get_running_loop(), asyncio.Queue())
        provider = self.agent_config.get("provider")
        async with create_agent(provider, self._build_options(provider)) as agent:
            self.agent = agent
            self._publish(Message("connection_state", {"state": "ready"}))
            jobs = {
                "operations": asyncio.create_task(self._dispatch_operations()),
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
                raise RuntimeError("Provider operation dispatcher stopped unexpectedly")
            finally:
                for task in jobs.values():
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*jobs.values(), return_exceptions=True)

    def _activate(self, loop, queue):
        self.loop = loop
        self.operation_queue = queue
        self._stop_signal = asyncio.Event()
        if not self.running:
            self._stop_signal.set()
        self._operations.attach(loop, queue)

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

    async def _dispatch_operations(self):
        while self.running:
            operation = await self.operation_queue.get()
            await self._dispatch_operation(operation)

    async def _dispatch_operation(self, operation):
        try:
            result = await self._perform(operation)
        except Exception as error:
            if operation.fatal:
                raise
            LOG.error("Provider %s failed: %s", operation.label, error)
            self._publish(("error", str(error)))
            # Never strand callers awaiting a result: report failure as None.
            if operation.callback:
                sublime.set_timeout(lambda: operation.callback(None), 0)
        else:
            self._complete(operation, result)

    async def _perform(self, operation):
        if operation.method == "configure":
            return await self._apply_configuration(operation.keywords)
        method = getattr(self.agent, operation.method)
        result = method(*operation.arguments, **operation.keywords)
        return await result if inspect.isawaitable(result) else result

    async def _apply_configuration(self, changes):
        if "plan_mode" in changes:
            setter = getattr(self.agent, "set_plan_mode", None)
            if setter is None:
                self.agent.plan_mode = changes["plan_mode"]
            else:
                result = setter(changes["plan_mode"])
                if inspect.isawaitable(result):
                    await result
        if "model" in changes:
            result = self.agent.set_model(changes["model"])
            if inspect.isawaitable(result):
                await result

    @staticmethod
    def _complete(operation, result):
        if operation.callback:
            sublime.set_timeout(lambda: operation.callback(result), 0)

    async def _pump_events(self):
        async for event in self.agent.receive_messages():
            if not self.running:
                return
            self._publish(event)

    def enqueue(self, text):
        return self._offer(ProviderOperation(
            "send_message", (text,), label="message", fatal=True,
        ))

    def steer(self, text, proceed_plan=False):
        return self._offer(ProviderOperation(
            "steer", (text,), {"proceed_plan": proceed_plan}, "steer",
        ))

    def cancel_turn(self):
        return self._offer(ProviderOperation("interrupt", label="interrupt"))

    def fork(self, message_id, on_done=None):
        operation = ProviderOperation(
            "rewind", (message_id,), label="fork", callback=on_done,
        )
        if not self._offer(operation) and on_done:
            sublime.set_timeout(lambda: on_done(None), 0)

    def reply_approval(self, request_id, response):
        if self.agent is None or not hasattr(self.agent, "send_approval_response"):
            return False
        return self._offer(ProviderOperation(
            "send_approval_response", (request_id, response), label="approval",
        ))

    def reconfigure(self, **changes):
        self.agent_config.update(changes)
        if self.agent is None or self.loop is None:
            return
        self._offer(ProviderOperation(
            "configure", keywords=dict(changes), label="reconfigure",
        ))

    @property
    def session_id(self):
        agent = self.agent
        identifiers = (
            getattr(agent, "_session_id", None),
            getattr(agent, "thread_id", None),
            self.agent_config.get("session_id"),
        )
        return next((value for value in identifiers if value), None)

    def stop(self):
        self._intentional_stop = True
        self.running = False
        if self.loop is not None and self._stop_signal is not None:
            self.loop.call_soon_threadsafe(self._stop_signal.set)

    def _offer(self, operation):
        if not self.running:
            return False
        self._operations.offer(operation)
        return True

    def _publish(self, event, force=False):
        sublime.set_timeout(
            lambda: self.on_message(event) if force or self.running else None, 0
        )
