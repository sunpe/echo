"""In-process WebSocket app-server integration test."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2])
)

try:
    import websockets
except ImportError:
    try:
        from echo.vendor import websockets
    except ImportError:
        websockets = None

from echo.domain.messages.message import CodexAgentOptions, MessageType
from echo.providers.codex.client import CodexAgent
from echo.workspace import LocalWorkspaceTools, dynamic_tool_specs


@unittest.skipIf(websockets is None, "websockets dependency is unavailable")
class TestWebSocketProtocolE2E(unittest.IsolatedAsyncioTestCase):
    async def test_real_socket_dynamic_read_without_server_workspace(self):
        observed = {
            "thread_params": None,
            "tool_result": None,
            "tool_response": None,
        }
        marker = "socket-local-only-a913"

        async def handler(socket, _path=None):
            async for raw in socket:
                message = json.loads(raw)
                method = message.get("method")
                request_id = message.get("id")
                if method == "initialize":
                    await socket.send(json.dumps({
                        "id": request_id,
                        "result": {"userAgent": "codex-cli/0.141.0"},
                    }))
                elif method == "model/list":
                    await socket.send(json.dumps({
                        "id": request_id, "result": {"data": []},
                    }))
                elif method == "thread/start":
                    observed["thread_params"] = message["params"]
                    await socket.send(json.dumps({
                        "id": request_id,
                        "result": {"thread": {"id": "thread-e2e"}},
                    }))
                elif method == "turn/start":
                    await socket.send(json.dumps({
                        "id": request_id,
                        "result": {"turn": {"id": "turn-e2e"}},
                    }))
                    await socket.send(json.dumps({
                        "id": 900,
                        "method": "item/tool/call",
                        "params": {
                            "threadId": "thread-e2e",
                            "turnId": "turn-e2e",
                            "callId": "read-once",
                            "namespace": "local_workspace",
                            "tool": "read",
                            "arguments": {"path": "local-only.txt"},
                        },
                    }))
                elif request_id == 900 and "result" in message:
                    observed["tool_result"] = message["result"]
                    observed["tool_response"] = message
                    payload = json.loads(
                        message["result"]["contentItems"][0]["text"]
                    )
                    await socket.send(json.dumps({
                        "method": "item/agentMessage/delta",
                        "params": {
                            "itemId": "message-e2e",
                            "delta": payload["text"].strip(),
                        },
                    }))
                    await socket.send(json.dumps({
                        "method": "turn/completed",
                        "params": {"turnId": "turn-e2e"},
                    }))

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as root:
                Path(root, "local-only.txt").write_text(
                    marker + "\n", encoding="utf-8"
                )
                enabled = ["read"]
                local_tools = LocalWorkspaceTools([root], enabled)
                agent = CodexAgent(CodexAgentOptions(
                    cwd=root,
                    app_server_url="ws://127.0.0.1:{}".format(port),
                    local_tool_handler=local_tools,
                    dynamic_tools=dynamic_tool_specs(enabled),
                ))
                try:
                    await agent.connect()
                    await agent.send_message("read the local-only file")
                    response = ""
                    async for message in agent.receive_messages():
                        if message.type == MessageType.TEXT.value:
                            response += message.content
                        if message.type == MessageType.STOP.value:
                            break
                finally:
                    await agent.disconnect()
        finally:
            server.close()
            await server.wait_closed()

        self.assertIn(marker, response)
        self.assertTrue(observed["tool_result"]["success"])
        self.assertNotIn("cwd", observed["thread_params"])
        self.assertTrue(observed["thread_params"]["dynamicTools"])
        self.assertNotIn("params", observed["tool_response"])


if __name__ == "__main__":
    unittest.main()
