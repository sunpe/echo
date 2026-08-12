import asyncio
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from echo.domain.messages.message import CodexAgentOptions
from echo.providers.codex.client import CodexAgent, _app_server_rpc
from echo.providers.codex.compatibility import flatten_tools, validate_version
from echo.workspace import LocalWorkspaceTools, dynamic_tool_specs
from echo.workspace.project_instructions import load_project_instructions
from echo.transport.session_client import _app_server_rpc as session_app_server_rpc
from echo.transport.websocket import WebSocketTransport, _load_websockets
from echo.workspace.local_references import normalize_local_references
from echo.domain.conversation.identity import server_identity, session_reference
from echo.domain.messages.errors import CodexCompatibilityError, CodexRPCError
from echo.vendor import websockets as vendored_websockets


class WebSocketTransportTest(unittest.TestCase):
    def test_bundled_runtime_is_preferred(self):
        self.assertIs(vendored_websockets, _load_websockets())


class LocalWorkspaceToolsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = self.temporary.name
        Path(self.root, "src").mkdir()
        Path(self.root, "src", "main.py").write_text(
            "alpha\nbeta\n", encoding="utf-8"
        )
        self.tools = LocalWorkspaceTools(
            [self.root],
            ["list", "stat", "read", "search", "write", "create"],
        )

    def tearDown(self):
        self.temporary.cleanup()

    async def test_read_returns_text_and_hash(self):
        result = await self.tools(
            "local_workspace", "read", {"path": "src/main.py", "startLine": 2}
        )
        self.assertEqual("beta\n", result["text"])
        self.assertEqual(
            hashlib.sha256(b"alpha\nbeta\n").hexdigest(),
            result["sha256"],
        )

    async def test_disk_tools_run_outside_event_loop_thread(self):
        event_loop_thread = threading.get_ident()
        worker_threads = []
        original = self.tools._tool_stat

        def capture(arguments):
            worker_threads.append(threading.get_ident())
            return original(arguments)

        self.tools._tool_stat = capture
        await self.tools("local_workspace", "stat", {"path": "src/main.py"})
        self.assertNotEqual(event_loop_thread, worker_threads[0])

    async def test_stat_hash_does_not_use_path_read_bytes(self):
        with patch.object(Path, "read_bytes", side_effect=AssertionError):
            result = await self.tools(
                "local_workspace", "stat", {"path": "src/main.py"}
            )
        self.assertEqual(
            hashlib.sha256(b"alpha\nbeta\n").hexdigest(),
            result["sha256"],
        )

    async def test_search_and_list_prune_denied_directories(self):
        denied = Path(self.root, ".git")
        denied.mkdir()
        denied.joinpath("config").write_text("needle\n", encoding="utf-8")

        search = await self.tools(
            "local_workspace", "search", {"query": "needle"}
        )
        listing = await self.tools(
            "local_workspace", "list", {"path": ".", "recursive": True}
        )

        self.assertEqual([], search["matches"])
        self.assertFalse(any(
            entry["path"].startswith(".git") for entry in listing["entries"]
        ))

    async def test_rejects_absolute_escape_and_sensitive_paths(self):
        for path in (
            "/etc/passwd",
            "../outside",
            ".env",
            "sub/../.env",
            ".git/config",
            ".ssh/id_rsa",
            "secret.txt",
        ):
            with self.subTest(path=path):
                with self.assertRaises(PermissionError):
                    await self.tools("local_workspace", "read", {"path": path})

    async def test_rejects_symlink_escape(self):
        outside = tempfile.NamedTemporaryFile(delete=False)
        try:
            outside.write(b"secret")
            outside.close()
            link = Path(self.root, "src", "outside.txt")
            try:
                link.symlink_to(outside.name)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable")
            with self.assertRaises(PermissionError):
                await self.tools(
                    "local_workspace", "read", {"path": "src/outside.txt"}
                )
        finally:
            os.unlink(outside.name)

    async def test_write_requires_current_hash(self):
        path = Path(self.root, "src", "main.py")
        with self.assertRaises(RuntimeError):
            await self.tools(
                "local_workspace",
                "write",
                {
                    "path": "src/main.py",
                    "expectedSha256": "stale",
                    "replacements": [{"old": "beta", "new": "gamma"}],
                },
            )
        self.assertEqual("alpha\nbeta\n", path.read_text(encoding="utf-8"))

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result = await self.tools(
            "local_workspace",
            "write",
            {
                "path": "src/main.py",
                "expectedSha256": digest,
                "replacements": [{"old": "beta", "new": "gamma"}],
            },
        )
        self.assertEqual("alpha\ngamma\n", path.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(), result["sha256"]
        )

    async def test_create_does_not_overwrite(self):
        await self.tools(
            "local_workspace",
            "create",
            {"path": "src/new.py", "content": "new\n"},
        )
        with self.assertRaises(FileExistsError):
            await self.tools(
                "local_workspace",
                "create",
                {"path": "src/new.py", "content": "overwrite\n"},
            )
        self.assertEqual(
            "new\n", Path(self.root, "src", "new.py").read_text(encoding="utf-8")
        )

    async def test_explicit_secondary_workspace_root(self):
        with tempfile.TemporaryDirectory() as secondary:
            Path(secondary, "other.txt").write_text("secondary", encoding="utf-8")
            tools = LocalWorkspaceTools(
                [self.root, secondary], ["pwd", "read"]
            )
            roots = await tools("local_workspace", "pwd", {})
            self.assertEqual(["root-1", "root-2"], [
                item["id"] for item in roots["roots"]
            ])
            with self.assertRaises(PermissionError):
                await tools("local_workspace", "roots", {})
            result = await tools(
                "local_workspace",
                "read",
                {"root": "root-2", "path": "other.txt"},
            )
            self.assertEqual("secondary", result["text"])


class ProjectInstructionsTest(unittest.TestCase):
    def test_loads_only_fixed_root_files(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "AGENTS.md").write_text("agents", encoding="utf-8")
            Path(root, "rules.md").write_text("rules", encoding="utf-8")
            Path(root, "src").mkdir()
            Path(root, "src", "AGENTS.md").write_text(
                "nested must not load", encoding="utf-8"
            )
            merged, digest = load_project_instructions(root)
            self.assertIn("[Project root AGENTS.md]\nagents", merged)
            self.assertIn("[Project root rules.md]\nrules", merged)
            self.assertNotIn("nested must not load", merged)
            self.assertEqual(64, len(digest))
            self.assertLess(merged.index("AGENTS.md"), merged.index("rules.md"))

    def test_rejects_oversized_instruction(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "AGENTS.md").write_text("x" * 20, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_project_instructions(root, max_bytes=10)


class DynamicProtocolTest(unittest.IsolatedAsyncioTestCase):
    def test_specs_use_canonical_namespace_shape(self):
        specs = dynamic_tool_specs(["read", "create"])
        self.assertEqual("namespace", specs[0]["type"])
        self.assertEqual("local_workspace", specs[0]["name"])
        self.assertEqual(
            ["read", "create"],
            [tool["name"] for tool in specs[0]["tools"]],
        )
        self.assertTrue(
            all(tool["type"] == "function" for tool in specs[0]["tools"])
        )
        self.assertTrue(
            all("inputSchema" in tool for tool in specs[0]["tools"])
        )

    def test_flatten_dynamic_tools_preserves_input_schema_and_aliases(self):
        specs = dynamic_tool_specs(["read", "create"])
        flattened, aliases = flatten_tools(specs)
        self.assertEqual(
            ["local_workspace__read", "local_workspace__create"],
            [tool["name"] for tool in flattened],
        )
        self.assertTrue(all(tool["type"] == "function" for tool in flattened))
        self.assertTrue(all("inputSchema" in tool for tool in flattened))
        self.assertEqual(
            ("local_workspace", "read"), aliases["local_workspace__read"]
        )

    async def test_missing_input_schema_retries_with_flattened_tools(self):
        class FallbackTransport:
            instances = []

            def __init__(self, *args, **kwargs):
                self.sent = []
                self.queue = asyncio.Queue()
                self.start_attempts = 0
                self.__class__.instances.append(self)

            async def connect(self):
                return None

            async def send(self, message):
                self.sent.append(message)
                request_id = message.get("id")
                if request_id is None:
                    return
                method = message.get("method")
                if method == "initialize":
                    result = {"userAgent": "codex-test"}
                elif method == "model/list":
                    result = {"data": []}
                elif method == "thread/start":
                    self.start_attempts += 1
                    if self.start_attempts == 1:
                        await self.queue.put({
                            "id": request_id,
                            "error": {
                                "code": -32600,
                                "message": "Invalid request: missing field `inputSchema`",
                            },
                        })
                        return
                    result = {"thread": {"id": "thread-fallback", "turns": []}}
                else:
                    result = {}
                await self.queue.put({"id": request_id, "result": result})

            async def messages(self):
                while True:
                    yield await self.queue.get()

            async def close(self):
                return None

        specs = dynamic_tool_specs(["read"])
        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            dynamic_tools=specs,
        ))
        with patch("echo.providers.codex.client.WebSocketTransport", FallbackTransport):
            with patch("echo.providers.codex.client.LOG.error") as log_error:
                await agent.connect()
        log_error.assert_not_called()
        try:
            starts = [
                item for item in FallbackTransport.instances[-1].sent
                if item.get("method") == "thread/start"
            ]
            self.assertEqual(2, len(starts))
            self.assertEqual("namespace", starts[0]["params"]["dynamicTools"][0]["type"])
            self.assertEqual("function", starts[1]["params"]["dynamicTools"][0]["type"])
            self.assertIn("inputSchema", starts[1]["params"]["dynamicTools"][0])
            self.assertEqual("thread-fallback", agent.thread_id)
            self.assertEqual(
                ("local_workspace", "read"),
                agent._dynamic_tool_aliases["local_workspace__read"],
            )
        finally:
            await agent.disconnect()

    async def test_flattened_tool_call_uses_namespace_alias(self):
        calls = []

        async def handler(namespace, tool, arguments):
            calls.append((namespace, tool, arguments))
            return {"ok": True}

        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            local_tool_handler=handler,
        ))
        agent._dynamic_tool_aliases = {
            "local_workspace__read": ("local_workspace", "read")
        }
        responses = []

        async def respond(request_id, result):
            responses.append(result)

        agent._rpc.respond = respond
        await agent._handle_dynamic_tool_call(1, {
            "tool": "local_workspace__read",
            "arguments": {"path": "README.md"},
        })
        self.assertEqual(
            [("local_workspace", "read", {"path": "README.md"})], calls
        )
        self.assertTrue(responses[0]["success"])

    def test_remote_plain_websocket_requires_explicit_opt_in(self):
        WebSocketTransport("ws://127.0.0.1:4500")
        with self.assertRaises(ValueError):
            WebSocketTransport("ws://example.com:4500")
        WebSocketTransport(
            "ws://example.com:4500", allow_insecure_ws=True
        )
        blocked_agent = CodexAgent(CodexAgentOptions(
            app_server_url="ws://example.com:4500"
        ))
        with self.assertRaises(ValueError):
            blocked_agent._new_transport()
        allowed_agent = CodexAgent(CodexAgentOptions(
            app_server_url="ws://example.com:4500",
            allow_insecure_ws=True,
            bearer_token_env="ECHO_REMOTE_BEARER",
        ))
        transport = allowed_agent._new_transport()
        self.assertIsInstance(transport, WebSocketTransport)
        self.assertEqual(
            "ECHO_REMOTE_BEARER", transport.bearer_token_env
        )

    async def test_wss_uses_tls_without_authentication_headers(self):
        class FakeSocket:
            closed = False

            async def close(self):
                self.closed = True

        captured = {}

        async def connect(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeSocket()

        transport = WebSocketTransport(
            "wss://codex.example.com",
        )
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                vendored_websockets, "connect", side_effect=connect
            ):
                await transport.connect()
        try:
            self.assertNotIn("extra_headers", captured)
            self.assertIsNotNone(captured["ssl"])
            self.assertTrue(captured["ssl"].check_hostname)
        finally:
            await transport.close()

    async def test_bearer_token_is_loaded_from_environment(self):
        class FakeSocket:
            closed = False

            async def close(self):
                self.closed = True

        captured = {}

        async def connect(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeSocket()

        transport = WebSocketTransport(
            "wss://codex.example.com",
            bearer_token_env="ECHO_TEST_BEARER",
        )
        with patch.dict(
            os.environ,
            {"ECHO_TEST_BEARER": "test-secret"},
            clear=True,
        ):
            with patch.object(
                vendored_websockets, "connect", side_effect=connect
            ):
                await transport.connect()
        try:
            self.assertEqual(
                {"Authorization": "Bearer test-secret"},
                captured["extra_headers"],
            )
        finally:
            await transport.close()

    async def test_configured_bearer_token_environment_must_exist(self):
        transport = WebSocketTransport(
            "wss://codex.example.com",
            bearer_token_env="ECHO_MISSING_BEARER",
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                RuntimeError, "ECHO_MISSING_BEARER"
            ):
                await transport.connect()

    def test_rejects_old_remote_codex_version(self):
        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            minimum_codex_version="0.141.0",
        ))
        with self.assertRaises(CodexCompatibilityError):
            validate_version(
                {"userAgent": "codex-cli/0.140.0"},
                agent.options.minimum_codex_version,
            )

    def test_accepts_unknown_zero_remote_codex_version(self):
        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            minimum_codex_version="0.141.0",
        ))
        validate_version(
            {"userAgent": "codex-cli/0.0.0"},
            agent.options.minimum_codex_version,
        )

    def test_remote_references_hide_absolute_paths(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "src", "main.py")
            path.parent.mkdir()
            path.write_text("x", encoding="utf-8")
            normalized = normalize_local_references(
                "check @{}#L1".format(path), [root]
            )
            self.assertIn("@root-1:src/main.py#L1", normalized)
            self.assertNotIn(root, normalized)

            canonical = normalize_local_references(
                "@root-1:src/main.py#L1", [root]
            )
            self.assertEqual("@root-1:src/main.py#L1", canonical)

    def test_dynamic_write_tool_uses_the_new_name_only(self):
        namespace = dynamic_tool_specs(["write"])[0]
        tools = namespace["tools"]
        self.assertEqual(["write"], [tool["name"] for tool in tools])
        self.assertEqual(
            ["path", "expectedSha256", "replacements"],
            tools[0]["inputSchema"]["required"],
        )

    def test_sessions_are_isolated_by_server_and_workspace(self):
        with tempfile.TemporaryDirectory() as first:
            with tempfile.TemporaryDirectory() as second:
                local = session_reference(
                    "codex", server_identity("ws://127.0.0.1:4500"),
                    [first], "thread"
                )
                remote = session_reference(
                    "codex", server_identity("wss://codex.example.com"),
                    [first], "thread"
                )
                other_workspace = session_reference(
                    "codex", server_identity("ws://127.0.0.1:4500"),
                    [second], "thread"
                )
        self.assertNotEqual(
            local["endpointIdentity"], remote["endpointIdentity"]
        )
        self.assertNotEqual(
            local["workspaceFingerprint"],
            other_workspace["workspaceFingerprint"],
        )
        self.assertEqual("codex", local["provider"])

    async def test_codex_dispatches_dynamic_tool_response(self):
        calls = []

        async def handler(namespace, tool, arguments):
            calls.append((namespace, tool, arguments))
            return {"text": "ok"}

        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            local_tool_handler=handler,
        ))
        responses = []

        async def respond(request_id, result):
            responses.append((request_id, result))

        agent._rpc.respond = respond
        await agent._handle_dynamic_tool_call(7, {
            "namespace": "local_workspace",
            "tool": "read",
            "arguments": {"path": "README.md"},
        })
        self.assertEqual(
            [("local_workspace", "read", {"path": "README.md"})], calls
        )
        self.assertTrue(responses[0][1]["success"])
        payload = json.loads(responses[0][1]["contentItems"][0]["text"])
        self.assertEqual({"text": "ok"}, payload)

    async def test_configured_read_tool_requires_approval(self):
        calls = []

        async def handler(namespace, tool, arguments):
            calls.append(tool)
            return {"text": "approved"}

        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            local_tool_handler=handler,
            local_tools_require_approval=["read"],
        ))
        approvals = []
        responses = []

        async def approve(approval_id, tool_name, arguments):
            approvals.append((approval_id, tool_name, arguments))
            return True

        async def respond(request_id, result):
            responses.append(result)

        agent._approvals.ask = approve
        agent._rpc.respond = respond
        await agent._handle_dynamic_tool_call(17, {
            "callId": "approval-read",
            "namespace": "local_workspace",
            "tool": "read",
            "arguments": {"path": "README.md"},
        })
        self.assertEqual(["read"], calls)
        self.assertEqual(
            "local_workspace.read", approvals[0][1]
        )
        self.assertTrue(responses[0]["success"])

    async def test_duplicate_call_id_executes_once(self):
        count = 0

        async def handler(namespace, tool, arguments):
            nonlocal count
            count += 1
            return {"count": count}

        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            local_tool_handler=handler,
        ))
        responses = []

        async def respond(request_id, result):
            responses.append(result)

        agent._rpc.respond = respond
        params = {
            "threadId": "thread",
            "turnId": "turn",
            "callId": "stable-call",
            "namespace": "local_workspace",
            "tool": "read",
            "arguments": {"path": "README.md"},
        }
        await agent._handle_dynamic_tool_call(1, params)
        await agent._handle_dynamic_tool_call(2, params)
        self.assertEqual(1, count)
        self.assertEqual(responses[0], responses[1])

    async def test_codex_returns_failed_tool_result(self):
        async def handler(namespace, tool, arguments):
            raise PermissionError("denied")

        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            local_tool_handler=handler,
        ))
        responses = []

        async def respond(request_id, result):
            responses.append(result)

        agent._rpc.respond = respond
        await agent._handle_dynamic_tool_call(8, {
            "namespace": "local_workspace",
            "tool": "read",
            "arguments": {"path": ".env"},
        })
        self.assertFalse(responses[0]["success"])
        payload = json.loads(responses[0]["contentItems"][0]["text"])
        self.assertEqual("PermissionError", payload["type"])

    async def test_remote_handshake_registers_tools_and_refreshes_instructions(self):
        class FakeWebSocketTransport:
            instances = []

            def __init__(self, *args, **kwargs):
                self.sent = []
                self.queue = asyncio.Queue()
                self.closed = False
                self.__class__.instances.append(self)

            async def connect(self):
                return None

            async def send(self, message):
                self.sent.append(message)
                request_id = message.get("id")
                method = message.get("method")
                if request_id is None:
                    return
                if method == "initialize":
                    result = {"userAgent": "codex-test"}
                elif method == "model/list":
                    result = {"data": []}
                elif method == "thread/start":
                    result = {
                        "thread": {"id": "thread-remote", "turns": []},
                        "model": "gpt-test",
                    }
                elif method == "turn/start":
                    result = {"turn": {"id": "turn-remote"}}
                else:
                    result = {}
                await self.queue.put({"id": request_id, "result": result})

            async def messages(self):
                while True:
                    yield await self.queue.get()

            async def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as root:
            rules = Path(root, "rules.md")
            rules.write_text("first rule", encoding="utf-8")

            def load_instructions():
                return load_project_instructions(root)[0]

            specs = dynamic_tool_specs(["read"])
            configured_fields = {
                "employeeId": "A10001",
                "sandbox": "configured-value",
            }
            options = CodexAgentOptions(
                cwd=root,
                app_server_url="wss://example.com",
                dynamic_tools=specs,
                developer_instructions=load_instructions(),
                developer_instructions_loader=load_instructions,
                request_fields=configured_fields,
                request_fields_loader=lambda: configured_fields,
            )
            with patch(
                "echo.providers.codex.client.WebSocketTransport",
                FakeWebSocketTransport,
            ):
                agent = CodexAgent(options)
                await agent.connect()
                try:
                    transport = FakeWebSocketTransport.instances[-1]
                    initialize = next(
                        message for message in transport.sent
                        if message.get("method") == "initialize"
                    )
                    self.assertTrue(
                        initialize["params"]["capabilities"]["experimentalApi"]
                    )
                    self.assertEqual("A10001", initialize["params"]["employeeId"])
                    initialized = next(
                        message for message in transport.sent
                        if message.get("method") == "initialized"
                    )
                    thread_start = next(
                        message for message in transport.sent
                        if message.get("method") == "thread/start"
                    )
                    self.assertEqual(
                        specs, thread_start["params"]["dynamicTools"]
                    )
                    self.assertEqual(
                        "read-only", thread_start["params"]["sandbox"]
                    )
                    self.assertEqual("A10001", thread_start["params"]["employeeId"])
                    self.assertEqual(
                        [], thread_start["params"]["environments"]
                    )
                    self.assertNotIn("cwd", thread_start["params"])
                    self.assertIn(
                        "first rule",
                        thread_start["params"]["developerInstructions"],
                    )

                    rules.write_text("second rule", encoding="utf-8")
                    configured_fields["employeeId"] = "A10002"
                    await agent.send_message("continue")
                    turn_start = [
                        message for message in transport.sent
                        if message.get("method") == "turn/start"
                    ][-1]
                    instructions = turn_start["params"][
                        "collaborationMode"
                    ]["settings"]["developer_instructions"]
                    self.assertIn("second rule", instructions)
                    self.assertNotIn("first rule", instructions)
                    self.assertEqual("A10002", turn_start["params"]["employeeId"])
                    self.assertEqual(
                        "configured-value", initialized["params"]["sandbox"]
                    )
                finally:
                    await agent.disconnect()

    async def test_session_rpc_includes_configured_request_fields(self):
        class FakeTransport:
            instances = []

            def __init__(self, *_args, **_kwargs):
                self.sent = []
                self.queue = asyncio.Queue()
                self.__class__.instances.append(self)

            async def connect(self):
                return None

            async def send(self, message):
                self.sent.append(message)
                if "id" in message:
                    await self.queue.put({"id": message["id"], "result": {}})

            async def messages(self):
                while True:
                    yield await self.queue.get()

            async def close(self):
                return None

        await session_app_server_rpc(
            {
                "url": "wss://example.com",
                "request_fields": {
                    "employeeId": "A10001",
                    "threadId": "configured-thread",
                },
            },
            "thread/read",
            {"threadId": "actual-thread"},
            transport_factory=FakeTransport,
        )

        messages = FakeTransport.instances[-1].sent
        self.assertTrue(all(
            message["params"].get("employeeId") == "A10001"
            for message in messages
        ))
        read = next(message for message in messages if message["method"] == "thread/read")
        self.assertEqual("actual-thread", read["params"]["threadId"])

    async def test_failed_initialize_closes_transport_and_reader(self):
        states = []

        class RejectingTransport:
            instances = []

            def __init__(self, *args, **kwargs):
                self.queue = asyncio.Queue()
                self.closed = False
                self.__class__.instances.append(self)

            async def connect(self):
                return None

            async def send(self, message):
                if message.get("method") == "initialize":
                    await self.queue.put({
                        "id": message["id"],
                        "error": {
                            "code": -32601,
                            "message": "experimentalApi is unavailable",
                        },
                    })

            async def messages(self):
                while True:
                    yield await self.queue.get()

            async def close(self):
                self.closed = True

        options = CodexAgentOptions(
            app_server_url="wss://example.com",
            connection_state_callback=lambda state, detail: states.append(
                (state, detail)
            ),
        )
        with patch(
            "echo.providers.codex.client.WebSocketTransport", RejectingTransport
        ):
            agent = CodexAgent(options)
            with self.assertRaises(CodexRPCError):
                await agent.connect()

        transport = RejectingTransport.instances[-1]
        self.assertTrue(transport.closed)
        self.assertIsNone(agent._transport)
        self.assertIsNone(agent._read_task)
        self.assertFalse(agent._is_connected)
        self.assertEqual("failed", agent.connection_state)
        self.assertEqual("failed", states[-1][0])

    async def test_reconnect_resumes_same_thread_without_replaying_tools(self):
        states = []

        class ReconnectTransport:
            instances = []

            def __init__(self, *args, **kwargs):
                self.sent = []
                self.queue = asyncio.Queue()
                self.closed = False
                self.__class__.instances.append(self)

            async def connect(self):
                return None

            async def send(self, message):
                self.sent.append(message)
                request_id = message.get("id")
                if request_id is None:
                    return
                method = message.get("method")
                if method == "initialize":
                    result = {"userAgent": "codex-cli/0.141.0"}
                elif method == "thread/resume":
                    result = {"thread": {"id": "thread-existing"}}
                else:
                    result = {}
                await self.queue.put({"id": request_id, "result": result})

            async def messages(self):
                while True:
                    yield await self.queue.get()

            async def close(self):
                self.closed = True

        class InterruptedTransport:
            closed = False

            async def close(self):
                self.closed = True

        specs = dynamic_tool_specs(["read", "write"])
        options = CodexAgentOptions(
            app_server_url="wss://example.com",
            dynamic_tools=specs,
            reconnect_max_attempts=1,
            connection_state_callback=lambda state, detail: states.append(
                (state, detail)
            ),
        )
        with patch(
            "echo.providers.codex.client.WebSocketTransport", ReconnectTransport
        ):
            agent = CodexAgent(options)
            interrupted = InterruptedTransport()
            agent._transport = interrupted
            agent._is_connected = True
            agent.thread_id = "thread-existing"

            await agent._reconnect_remote()
            try:
                transport = ReconnectTransport.instances[-1]
                methods = [
                    message.get("method") for message in transport.sent
                    if message.get("method")
                ]
                self.assertEqual(
                    ["initialize", "initialized", "thread/resume"], methods
                )
                resume = next(
                    item for item in transport.sent
                    if item.get("method") == "thread/resume"
                )
                self.assertEqual(
                    "thread-existing", resume["params"]["threadId"]
                )
                self.assertNotIn("dynamicTools", resume["params"])
                self.assertEqual("read-only", resume["params"]["sandbox"])
                self.assertTrue(interrupted.closed)
                self.assertTrue(agent._is_connected)
                self.assertEqual("ready", agent.connection_state)
                self.assertEqual(
                    ["reconnecting", "initializing", "ready"],
                    [state for state, _detail in states],
                )
            finally:
                await agent.disconnect()

    async def test_message_consumer_survives_temporary_disconnect(self):
        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com"
        ))
        agent._is_connected = True
        iterator = agent.receive_messages()
        pending = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0.12)

        agent._is_connected = False
        await asyncio.sleep(0.12)
        self.assertFalse(pending.done())

        expected = object()
        await agent._message_queue.put(expected)
        self.assertIs(expected, await asyncio.wait_for(pending, 1))
        agent._closing = True
        await iterator.aclose()

    async def test_disconnect_cancels_server_request_tasks(self):
        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com"
        ))
        started = asyncio.Event()

        async def request():
            started.set()
            await asyncio.Event().wait()

        agent._spawn_server_request(request())
        await started.wait()
        await agent._cancel_server_request_tasks()
        self.assertFalse(agent._server_request_tasks)

    async def test_cancelled_dynamic_tool_does_not_leave_inflight_cache(self):
        started = asyncio.Event()

        async def handler(_namespace, _tool, _arguments):
            started.set()
            await asyncio.Event().wait()

        agent = CodexAgent(CodexAgentOptions(
            app_server_url="wss://example.com",
            local_tool_handler=handler,
        ))
        agent._spawn_server_request(agent._handle_dynamic_tool_call(7, {
            "callId": "call-7",
            "namespace": "local_workspace",
            "tool": "read",
            "arguments": {"path": "README.md"},
        }))
        await started.wait()
        self.assertTrue(agent._tool_call_futures)

        await agent._cancel_server_request_tasks()
        self.assertFalse(agent._tool_call_futures)

    def test_file_change_diff_rejects_paths_outside_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as outside:
                outside_path = Path(outside, "secret.txt")
                outside_path.write_text("secret", encoding="utf-8")
                agent = CodexAgent(CodexAgentOptions(
                    cwd=root,
                    app_server_url="wss://example.com",
                ))
                self.assertIsNone(agent._generate_file_change_diff({
                    "changes": [{
                        "path": str(outside_path),
                        "type": "update",
                        "content": "changed",
                    }]
                }))


if __name__ == "__main__":
    unittest.main()
