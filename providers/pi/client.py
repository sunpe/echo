"""Small standard-library client for Pi's line-oriented RPC protocol."""

import asyncio
import json
import logging
import os
import shutil
import sys
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from ...domain.messages.message import PiAgentOptions, AssistantMessage, BaseAgent, Message

LOG = logging.getLogger("echo")
_MESSAGE_STREAM_END = object()
# Pi emits one JSON object per line. Tool results and conversation state can be
# much larger than asyncio's 64 KiB default StreamReader limit.
_RPC_STREAM_LIMIT = 16 * 1024 * 1024


def _fallback_pi_commands():
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return [os.path.join(appdata, "npm", "pi.cmd")] if appdata else []
    home = os.path.expanduser("~")
    roots = (
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".npm-global", "bin"),
        os.path.join(home, ".bun", "bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    )
    return [os.path.join(root, "pi") for root in roots]


def find_pi_cli() -> str:
    discovered = shutil.which("pi")
    if discovered:
        return discovered
    executable = lambda path: os.path.isfile(path) and os.access(path, os.X_OK)
    return next(filter(executable, _fallback_pi_commands()), "pi")


class PiAgent(BaseAgent):
    """Interactive Pi CLI client using ``pi --mode rpc``."""

    def __init__(self, options: Optional[PiAgentOptions] = None):
        if options is None:
            options = PiAgentOptions()
        super().__init__(options)
        self.cli_path = self.options.cli_path or find_pi_cli()
        self.process = None
        self.is_connected = False
        self._read_task = None
        self._stderr_task = None
        self._messages = asyncio.Queue()
        self._messages_closed = False
        self.stream_end_reason = None
        self._stderr_tail = []
        self._session_id = self.options.session_id
        self.plan_mode = self.options.plan_mode

    def _set_state(self, state, detail=None):
        callback = self.options.connection_state_callback
        if callback:
            callback(state, detail)

    def _command(self):
        command = [self.cli_path, '--mode', 'rpc']
        if self.options.session_id:
            # Current Pi RPC CLI uses --session for a session path or id.
            command += ['--session', self.options.session_id]
        if self.options.model:
            command += ['--model', self.options.model.replace(':', '/', 1)]
        if self.options.system_prompt:
            command += ['--append-system-prompt', self.options.system_prompt]
        return command

    async def connect(self, prompt: Optional[str] = None) -> None:
        if self.is_connected:
            raise RuntimeError("Pi is already connected")
        self._set_state('connecting')
        command = self._command()
        env = os.environ.copy()
        env.update(self.options.extra_env)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=self.options.cwd, env=env, limit=_RPC_STREAM_LIMIT,
            )
        except Exception as exc:
            self._set_state('failed', str(exc))
            raise
        self.is_connected = True
        self._messages = asyncio.Queue()
        self._messages_closed = False
        self.stream_end_reason = None
        self._stderr_tail = []
        self._read_task = asyncio.create_task(self._read_messages())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        # Pi chooses a concrete default model at startup. Fetch it so Echo's
        # model panel reflects the active model instead of a generic default.
        await self._transmit({"type": "get_state", "id": str(uuid.uuid4())})
        if self.plan_mode:
            await self.set_plan_mode(True)
        self._set_state('ready')
        if prompt:
            await self.send_message(prompt)

    async def _transmit(self, payload: Dict[str, Any]) -> None:
        stream = self.process.stdin if self.process else None
        if stream is None:
            raise RuntimeError("Pi process is unavailable")
        wire_data = json.dumps(payload).encode("utf-8") + b"\n"
        stream.write(wire_data)
        await stream.drain()

    async def _send_action(self, action, include_id=False, **fields):
        envelope = {"type": action}
        envelope.update(fields)
        if include_id:
            envelope["id"] = str(uuid.uuid4())
        await self._transmit(envelope)

    async def send_message(self, content: str, parent_tool_use_id=None, proceed_plan=False) -> None:
        if not self.is_connected:
            raise RuntimeError("Pi is not connected")
        message = "/plan implement" if proceed_plan else content
        await self._send_action("prompt", include_id=True, message=message)

    async def steer(self, text: str, proceed_plan=False) -> None:
        if not self.is_connected:
            raise RuntimeError("Pi is not connected")
        action, message = ("prompt", "/plan implement") \
            if proceed_plan else ("steer", text)
        await self._send_action(
            action, include_id=proceed_plan, message=message
        )

    async def interrupt(self) -> None:
        if self.is_connected:
            await self._send_action("abort")

    async def set_model(self, model: str) -> None:
        if self.is_connected:
            provider, _, model_id = model.replace(":", "/", 1).partition("/")
            await self._send_action(
                "set_model",
                include_id=True,
                provider=provider,
                modelId=model_id or provider,
            )

    async def set_plan_mode(self, plan_mode: bool) -> None:
        self.plan_mode = plan_mode
        if self.is_connected:
            await self._send_action(
                "prompt",
                include_id=True,
                message="/plan" if plan_mode else "/plan exit",
            )

    def _parse(self, data: Dict[str, Any]) -> Optional[Message]:
        kind = data.get("type")
        if (
            kind == "response"
            and data.get("success")
            and data.get("command") == "get_state"
        ):
            state = data.get("data", {})
            model = state.get("model", {}) if isinstance(state, dict) else {}
            if isinstance(model, dict):
                provider = model.get("provider")
                model_id = model.get("id")
                if isinstance(provider, str) and isinstance(model_id, str):
                    value = "{}/{}".format(provider, model_id)
                    self.options.model = value
                    return Message("model_update", {"model": value}, data.get("id"))
        if kind == "message_update":
            event = data.get("assistantMessageEvent", {})
            event_type = event.get("type")
            if event_type == "text_delta":
                return Message("text_delta", event.get("delta", ""), data.get("id"))
            if event_type == "thinking_delta":
                return Message("thinking_delta", event.get("delta", ""), data.get("id"))
        if kind == "message_end" and data.get("message", {}).get("role") == "assistant":
            blocks = [b for b in data["message"].get("content", []) if b.get("type") == "toolCall"]
            return AssistantMessage(blocks, id=data["message"].get("id"))
        if kind == "agent_end":
            return Message("result", {"success": not data.get("errorMessage")}, data.get("id"))
        if kind == "response" and not data.get("success"):
            return Message("error", data.get("error", "Pi RPC failed"), data.get("id"))
        if kind in ("auto_retry_start", "auto_retry_end"):
            return Message("error", data.get("errorMessage") or data.get("finalError"), data.get("id"))
        return None

    @staticmethod
    def _session_id_from(data: Dict[str, Any]) -> Optional[str]:
        """Extract a Pi session identifier from known RPC envelope shapes."""
        candidates = (
            data,
            data.get("data"),
            data.get("session"),
            data.get("result"),
        )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("sessionId", "session_id", "sessionPath", "session_path"):
                value = candidate.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    async def _capture_session_id(self, data: Dict[str, Any]) -> None:
        session_id = self._session_id_from(data)
        if not session_id or session_id == self._session_id:
            return
        self._session_id = session_id
        self.options.session_id = session_id
        await self._messages.put(Message(
            "thread_started", content={"session_id": session_id}
        ))

    async def _read_messages(self) -> None:
        try:
            while self.is_connected and self.process and self.process.stdout:
                line = await self.process.stdout.readline()
                if not line:
                    if self.is_connected:
                        self.is_connected = False
                        self.stream_end_reason = self._exit_reason()
                        self._set_state('failed', self.stream_end_reason)
                    break
                try:
                    data = json.loads(line)
                    await self._capture_session_id(data)
                    message = self._parse(data)
                except (json.JSONDecodeError, TypeError) as exc:
                    LOG.warning("Ignoring invalid Pi message: %s", exc)
                    continue
                if message:
                    await self._messages.put(message)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if self.is_connected:
                self.is_connected = False
                self.stream_end_reason = "Pi RPC read failed: {}".format(exc)
                self._set_state('failed', self.stream_end_reason)
        finally:
            await self._close_message_stream()

    def _exit_reason(self):
        code = getattr(self.process, "returncode", None)
        reason = "Pi RPC stdout closed" if code is None \
            else "Pi process exited with code {}".format(code)
        if self._stderr_tail:
            reason += ": " + self._stderr_tail[-1]
        return reason

    async def _close_message_stream(self) -> None:
        """Unblock consumers exactly once when Pi's stdout stream ends."""
        if self._messages_closed:
            return
        self._messages_closed = True
        await self._messages.put(_MESSAGE_STREAM_END)

    async def _read_stderr(self) -> None:
        try:
            while self.is_connected and self.process and self.process.stderr:
                line = await self.process.stderr.readline()
                if not line:
                    break
                detail = line.decode(errors="replace").rstrip()
                self._stderr_tail.append(detail)
                del self._stderr_tail[:-5]
                LOG.debug("Pi: %s", detail)
        except asyncio.CancelledError:
            pass

    async def receive_messages(self) -> AsyncIterator[Message]:
        while True:
            message = await self._messages.get()
            if message is _MESSAGE_STREAM_END:
                if self.stream_end_reason:
                    raise RuntimeError(self.stream_end_reason)
                break
            yield message

    async def disconnect(self) -> None:
        self.is_connected = False
        self.stream_end_reason = None
        self._set_state('disconnected')
        tasks = [task for task in (self._read_task, self._stderr_task) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._close_message_stream()
        self._read_task = None
        self._stderr_task = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
