"""
Codex Agent SDK Client - App Server Implementation
A client for interacting with Codex via the "codex app-server" JSON-RPC protocol.

The app-server provides a persistent bidirectional WebSocket connection with support
for interactive approval, multi-turn conversations, and streaming events.
"""

import asyncio
import logging
import inspect
from collections import OrderedDict
from typing import Optional, Dict, Any, AsyncIterator, List

LOG = logging.getLogger("echo")

from ...domain.messages.message import CodexAgentOptions, BaseAgent, Message, MessageType
from .protocol import CodexProtocolHandlersMixin, ProtocolRouter
from ...transport.rpc_exchange import RPCExchange
from .approvals import ApprovalExchange
from .compatibility import flatten_tools, needs_flat_tools, turn_id, validate_version, visible_models
from ...transport.websocket import WebSocketTransport
from ...transport.request_fields import merge_request_fields
from .turn_request import TurnContext, build_turn_params, rollback_count
from ...shared.version import VERSION
from ...transport import session_client as _session_client
from ...domain.messages.errors import CodexCompatibilityError, CodexConfigurationError, CodexConnectionError, CodexRPCError


def normalize_codex_model(model: Optional[str]) -> Optional[str]:
    """Return a concrete model id, or None for the UI's default placeholder."""
    if not isinstance(model, str):
        return None
    model = model.strip()
    if not model or model.lower() == "default":
        return None
    return model


async def _app_server_rpc(
    connection: Dict[str, Any], method: str, params: Dict[str, Any]
) -> Any:
    """Keep the established test/extension seam for auxiliary RPC calls."""
    return await _session_client._app_server_rpc(
        connection,
        method,
        params,
        transport_factory=WebSocketTransport,
    )


class CodexAgent(CodexProtocolHandlersMixin, BaseAgent):
    """
    Client for interacting with Codex via app-server JSON-RPC over WebSocket.

    echo connects to an app-server managed by the user. The connection is
    bidirectional and persists across turns.

    Key features:
    - Persistent WebSocket connection to the configured app-server
    - Bidirectional: send and receive JSON-RPC messages at any time
    - Approval support: handle command/file-change approval requests
    - Multi-turn: reuse the same thread across multiple messages
    """

    def __init__(self, options: Optional[CodexAgentOptions] = None):
        if options is None:
            options = CodexAgentOptions()
        super().__init__(options)
        self.options.model = normalize_codex_model(self.options.model)
        self.thread_id: Optional[str] = None
        if not self.options.app_server_url:
            raise CodexConfigurationError(
                "Codex requires an app_server.url WebSocket address"
            )
        self._is_connected = False
        self.available_models: List[Dict[str, Any]] = []

        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        self._transport = None
        self._read_task: Optional[asyncio.Task] = None
        self._model_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._closing = False
        self.connection_state = "disconnected"
        self._rpc = RPCExchange(
            send=lambda envelope: self._write_json(envelope),
            prepare_params=lambda params: self._client_params(params),
            default_timeout=lambda: self.options.request_timeout,
        )
        self._protocol_router = ProtocolRouter(self)
        self._approvals = ApprovalExchange(self._message_queue)
        # Track active turn so we know when it completes
        self._active_turn_id: Optional[str] = None
        # Cache item data from item/started, keyed by itemId
        self._item_cache: Dict[str, Dict[str, Any]] = {}
        # Plan mode: mutable at runtime (separate from options.plan_mode snapshot)
        self.plan_mode: bool = self.options.plan_mode
        # Accumulates item/plan/delta content in plan mode
        self._plan_text: str = ""
        # Counts user turns sent (1-based); used as rewind handle
        self._turn_count: int = 0
        self._tool_call_results = OrderedDict()
        self._tool_call_futures: Dict[str, asyncio.Future] = {}
        self._tool_result_cache_limit = 512
        self._server_request_tasks = set()
        # Older app-server gateways do not understand namespace dynamic tools
        # and expect each function at the top level.  Keep aliases so calls
        # received in that compatibility shape still reach the local handler.
        self._dynamic_tool_aliases: Dict[str, Any] = {}

    def _set_connection_state(self, state: str, detail: Optional[str] = None):
        self.connection_state = state
        callback = self.options.connection_state_callback
        if callback:
            try:
                callback(state, detail)
            except Exception:
                LOG.exception("Connection state callback failed")

    async def _fetch_models(self) -> None:
        try:
            models = visible_models(await self._rpc_request("model/list", {}))
            if not models:
                return
            self.available_models = models
            LOG.info("Model catalog updated (%d visible)", len(models))
            await self._message_queue.put(Message(
                "models_update", content={"models": models}
            ))
        except Exception as exc:
            LOG.warning("Unable to refresh model catalog: %s", exc)

    def _new_transport(self):
        return WebSocketTransport(
            self.options.app_server_url,
            allow_insecure_ws=self.options.allow_insecure_ws,
            bearer_token_env=self.options.bearer_token_env,
            tls_verify=self.options.tls_verify,
            connect_timeout=self.options.connect_timeout,
            ping_interval=self.options.ping_interval,
            max_message_bytes=self.options.max_message_bytes,
        )

    async def _initialize_protocol(self):
        self._set_connection_state("initializing")
        initialize_result = await self._rpc_request("initialize", {
            "clientInfo": {
                "name": "echo",
                "title": "echo",
                "version": VERSION,
            },
            "capabilities": {"experimentalApi": True},
        })
        validate_version(initialize_result, self.options.minimum_codex_version)
        await self._rpc.notify("initialized", None)
        return initialize_result

    async def _refresh_developer_instructions(self):
        loader = self.options.developer_instructions_loader
        if not loader:
            return self.options.developer_instructions
        value = loader()
        if inspect.isawaitable(value):
            value = await value
        self.options.developer_instructions = value
        return value

    def _fail_pending_requests(self, message: str):
        self._rpc.fail(message)

    async def _abort_connect(self, exc: Exception) -> None:
        """Release every resource allocated by a partially completed connect."""
        self._closing = True
        self._is_connected = False
        self._fail_pending_requests("Codex connection initialization failed")
        await self._cancel_server_request_tasks()

        for task in (self._model_task, self._read_task):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._model_task = None
        self._read_task = None

        transport, self._transport = self._transport, None
        if transport:
            try:
                await transport.close()
            except Exception:
                LOG.exception("Failed to close transport after connect error")
        self._set_connection_state("failed", str(exc))

    async def _reconnect_remote(self):
        last_error = None
        self._set_connection_state("reconnecting")
        self._fail_pending_requests("Codex app-server connection was interrupted")
        await self._cancel_server_request_tasks()
        old_transport, self._transport = self._transport, None
        if old_transport:
            try:
                await old_transport.close()
            except Exception:
                LOG.exception("Failed to close interrupted transport")

        attempts = max(int(self.options.reconnect_max_attempts), 1)
        for attempt in range(attempts):
            if self._closing:
                return
            if attempt:
                delay = min(
                    self.options.reconnect_base_delay * (2 ** (attempt - 1)),
                    30.0,
                )
                await asyncio.sleep(delay)
            try:
                self._transport = self._new_transport()
                await self._transport.connect()
                self._is_connected = True
                self._read_task = asyncio.create_task(self._read_messages())
                await self._initialize_protocol()
                instructions = await self._refresh_developer_instructions()
                params = {
                    "threadId": self.thread_id,
                    "approvalPolicy": "untrusted",
                    "sandbox": "read-only",
                }
                if self.options.model:
                    params["model"] = self.options.model
                if instructions:
                    params["developerInstructions"] = instructions
                result = await self._rpc_request("thread/resume", params)
                thread = result.get("thread", {}) if isinstance(result, dict) else {}
                if thread.get("id") != self.thread_id:
                    raise CodexCompatibilityError(
                        "Remote app-server resumed an unexpected thread"
                    )
                self._set_connection_state("ready")
                await self._message_queue.put(Message(
                    "connection_state",
                    content={"state": "ready", "reconnected": True},
                ))
                return
            except Exception as exc:
                last_error = exc
                self._is_connected = False
                read_task, self._read_task = self._read_task, None
                if (
                    read_task
                    and not read_task.done()
                    and read_task is not asyncio.current_task()
                ):
                    read_task.cancel()
                    try:
                        await read_task
                    except asyncio.CancelledError:
                        pass
                transport, self._transport = self._transport, None
                if transport:
                    try:
                        await transport.close()
                    except Exception:
                        pass
                LOG.warning(
                    "Remote reconnect attempt %s/%s failed: %s",
                    attempt + 1,
                    attempts,
                    exc,
                )

        self._set_connection_state("failed", str(last_error))
        await self._message_queue.put(Message(
            MessageType.ERROR.value,
            content="Codex app-server reconnect failed: {}".format(last_error),
        ))

    def _begin_connection(self):
        if self._is_connected:
            raise RuntimeError("Client is already connected")
        self._closing = False
        self._set_connection_state("connecting")

    async def _open_transport(self):
        self._transport = self._new_transport()
        try:
            await self._transport.connect()
        except Exception as exc:
            raise CodexConnectionError(
                "Unable to connect to Codex app-server: {}".format(exc)
            ) from exc
        self._is_connected = True
        self._read_task = asyncio.create_task(self._read_messages())
        LOG.info("Connected to Codex app-server")

    def _thread_request(self):
        params = {}
        if self.options.session_id:
            params["threadId"] = self.options.session_id
        if self.options.model:
            params["model"] = self.options.model
        if "AskUserQuestion" in set(self.options.disallowed_tools or ()):
            params["config"] = {
                "features.default_mode_request_user_input": False
            }
        params.update({
            "approvalPolicy": "untrusted",
            "sandbox": "read-only",
        })
        if not self.options.session_id:
            params["environments"] = []
        if self.options.developer_instructions:
            params["developerInstructions"] = (
                self.options.developer_instructions
            )
        if self.options.dynamic_tools and not self.options.session_id:
            params["dynamicTools"] = self.options.dynamic_tools
        method = "thread/resume" if self.options.session_id \
            else "thread/start"
        return method, params

    async def _request_thread(self, method, params):
        try:
            return await self._rpc_request(method, params)
        except Exception as exc:
            if (
                method == "thread/start"
                and self.options.dynamic_tools
                and needs_flat_tools(exc)
            ):
                flattened, aliases = flatten_tools(
                    self.options.dynamic_tools
                )
                retry_params = dict(params)
                retry_params["dynamicTools"] = flattened
                result = await self._rpc_request(method, retry_params)
                self._dynamic_tool_aliases = aliases
                return result
            incompatible = (
                method == "thread/start"
                and self.options.dynamic_tools
                and isinstance(exc, CodexRPCError)
                and exc.code in (-32601, -32602)
            )
            if incompatible:
                raise CodexCompatibilityError(
                    "Codex app-server rejected the required experimental "
                    "dynamicTools protocol: {}".format(exc)
                ) from exc
            raise

    def _accept_thread(self, method, result):
        response = result if isinstance(result, dict) else {}
        thread = response.get("thread") or {}
        self.thread_id = thread.get("id")
        if not self.thread_id:
            raise CodexCompatibilityError(
                "Codex app-server did not return a thread id"
            )
        if not self.options.model and "model" in response:
            self.options.model = normalize_codex_model(
                response.get("model")
            )
        turns = thread.get("turns") or ()
        self._turn_count = len(turns)
        LOG.info(
            "Codex %s established thread %s with %d prior turn(s)",
            method,
            self.thread_id,
            self._turn_count,
        )

    async def connect(self, prompt: Optional[str] = None) -> None:
        """Open the transport, negotiate capabilities, and select a thread."""
        self._begin_connection()
        try:
            await self._open_transport()
            await self._initialize_protocol()
            self._model_task = asyncio.create_task(self._fetch_models())
            await self._refresh_developer_instructions()
            method, params = self._thread_request()
            result = await self._request_thread(method, params)
            self._accept_thread(method, result)
            self._set_connection_state("ready")
            if prompt:
                await self.send_message(prompt)
        except Exception as exc:
            await self._abort_connect(exc)
            raise

    def set_model(self, model: Optional[str]) -> None:
        """Dynamically switch the model; takes effect on the next turn."""
        selected = normalize_codex_model(model)
        self.options.model = selected
        LOG.info("Codex model switched to: %s", selected or "default")

    async def send_message(self, content: str, parent_tool_use_id: Optional[str] = None, proceed_plan: bool = False) -> None:
        """Send a user message to Codex by starting a new turn on the thread."""
        self._require_thread("send a message")
        del parent_tool_use_id
        self._plan_text = ""
        if self.options.developer_instructions_loader:
            await self._refresh_developer_instructions()
        context = TurnContext(
            self.thread_id,
            self._active_turn_id,
            self.options.model,
            self.options.developer_instructions,
            self.plan_mode,
        )
        result = await self._rpc_request(
            "turn/start", build_turn_params(context, content, proceed_plan)
        )
        active_id = turn_id(result) \
            if isinstance(result, dict) else None
        if active_id:
            self._active_turn_id = active_id
            LOG.debug("Active Codex turn: %s", active_id)

    def _require_thread(self, operation):
        if self._is_connected and self.thread_id:
            return self.thread_id
        raise RuntimeError(
            "Cannot {} without a connected Codex thread".format(operation)
        )

    async def steer(self, text: str, proceed_plan: bool = False) -> None:
        """Submit follow-up guidance against the current thread."""
        self._require_thread("steer")
        LOG.info(
            "Steering agent (%d characters, proceed_plan=%s)",
            len(text),
            proceed_plan,
        )
        await self.send_message(text, proceed_plan=proceed_plan)

    async def interrupt(self) -> None:
        if not (self._is_connected and self.thread_id and self._active_turn_id):
            LOG.warning("Interrupt ignored because no turn is active")
            return
        reference = {
            "threadId": self.thread_id,
            "turnId": self._active_turn_id,
        }
        LOG.info("Interrupting active turn %s", self._active_turn_id)
        await self._rpc_request("turn/interrupt", reference)

    async def rewind(self, turn_identifier: str) -> Optional[str]:
        """Discard the selected turn and all turns that follow it."""
        thread_id = self._require_thread("rewind")
        target_index = int(turn_identifier)
        remove_count = rollback_count(self._turn_count, target_index)
        if remove_count:
            await self._rollback_tail(thread_id, target_index, remove_count)
        return thread_id

    async def _rollback_tail(self, thread_id, target_index, remove_count):
        LOG.info("Removing %d turn(s) from index %d", remove_count, target_index)
        response = await self._rpc_request(
            "thread/rollback",
            {"threadId": thread_id, "numTurns": remove_count},
        )
        if response is None:
            raise RuntimeError("Codex did not acknowledge thread rollback")
        self._turn_count = target_index - 1
        self._active_turn_id = None

    def _client_params(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        configured_fields = self.options.request_fields
        if self.options.request_fields_loader:
            try:
                configured_fields = self.options.request_fields_loader()
            except Exception:
                LOG.exception("Unable to refresh app-server request fields")
        return merge_request_fields(params, configured_fields)

    async def _write_json(self, data: Dict[str, Any]) -> None:
        if not self._transport:
            raise RuntimeError("Codex app-server transport is not available")
        await self._transport.send(data)

    async def _rpc_request(self, method: str, params: Dict[str, Any], timeout: Optional[float] = None) -> Any:
        return await self._rpc.request(method, params, timeout)

    async def _read_messages(self) -> None:
        """Background task to read messages from the selected transport."""
        if not self._transport:
            return

        try:
            async for data in self._transport.messages():
                if not self._is_connected:
                    break
                LOG.debug("codex receive transport message")
                await self._dispatch(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            LOG.error(f"read_messages error: {e}")
        finally:
            if (
                self._is_connected
                and not self._closing
            ):
                self._is_connected = False
                if not self._reconnect_task or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(
                        self._reconnect_remote()
                    )

    def _spawn_server_request(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._server_request_tasks.add(task)
        task.add_done_callback(self._server_request_tasks.discard)

    async def _cancel_server_request_tasks(self) -> None:
        tasks = [
            task for task in self._server_request_tasks
            if not task.done() and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._server_request_tasks.difference_update(tasks)

    # ── Message dispatch ────────────────────────────────────────────────

    async def receive_messages(self) -> AsyncIterator[Message]:
        """
        Yields messages from the agent.
        Keeps running until disconnect.
        """
        if not self._is_connected:
            raise RuntimeError("Client is not connected. Call connect() first.")

        while not self._closing:
            message = await self._message_queue.get()
            if self._closing:
                break
            yield message

    async def disconnect(self) -> None:
        """Disconnect and cleanup resources."""
        self._closing = True
        self._set_connection_state("closing")
        self._is_connected = False
        await self._cancel_server_request_tasks()

        # Cancel background tasks
        for task in (self._model_task, self._read_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._model_task = None
        self._read_task = None
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        # Cancel pending RPC futures
        self._rpc.cancel()

        self._approvals.cancel_all()

        if self._transport:
            await self._transport.close()
            self._transport = None
        self._set_connection_state("disconnected")


async def _one_shot_messages(client, prompt):
    if prompt:
        await client.send_message(prompt)
    async for event in client.receive_messages():
        yield event
        if event.type == MessageType.STOP.value:
            return


async def query(
    prompt: str,
    options: Optional[CodexAgentOptions] = None,
) -> AsyncIterator[Message]:
    """Query Codex for one-shot interactions."""
    configuration = options or CodexAgentOptions()
    async with CodexAgent(options=configuration) as client:
        async for event in _one_shot_messages(client, prompt):
            yield event
