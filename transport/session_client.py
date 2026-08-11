"""Short-lived app-server RPC helpers used by session pickers."""

import asyncio
from typing import Any, Dict, Optional

from ..domain.messages.errors import CodexConnectionError, CodexRPCError, CodexRPCTimeout
from .request_fields import merge_request_fields
from ..shared.version import VERSION
from .websocket import WebSocketTransport


async def _app_server_rpc(
    connection: Dict[str, Any],
    method: str,
    params: Dict[str, Any],
    transport_factory=WebSocketTransport,
) -> Any:
    transport = transport_factory(
        connection.get("url", ""),
        allow_insecure_ws=connection.get("allow_insecure_ws", False),
        bearer_token_env=connection.get("bearer_token_env", ""),
        tls_verify=connection.get("tls_verify", True),
        connect_timeout=connection.get("connect_timeout_seconds", 10),
        ping_interval=connection.get("ping_interval_seconds", 25),
        max_message_bytes=connection.get("max_message_bytes", 8 * 1024 * 1024),
    )
    request_id = 0
    message_iterator = None
    request_timeout = connection.get("request_timeout_seconds", 60)

    def client_params(value):
        return merge_request_fields(value, connection.get("request_fields"))

    async def request(request_method, request_params):
        nonlocal request_id
        nonlocal message_iterator
        request_id += 1
        current_id = request_id
        await transport.send({
            "id": current_id,
            "method": request_method,
            "params": client_params(request_params),
        })
        if message_iterator is None:
            message_iterator = transport.messages().__aiter__()
        while True:
            try:
                message = await asyncio.wait_for(
                    message_iterator.__anext__(),
                    timeout=request_timeout,
                )
            except asyncio.TimeoutError:
                raise CodexRPCTimeout(
                    "{} timed out after {} seconds".format(
                        request_method, request_timeout
                    )
                )
            except StopAsyncIteration:
                break
            if message.get("id") != current_id:
                continue
            if "error" in message:
                raise CodexRPCError(request_method, message["error"])
            return message.get("result")
        raise CodexConnectionError(
            "Codex app-server closed while waiting for " + request_method
        )

    await transport.connect()
    try:
        await request("initialize", {
            "clientInfo": {
                "name": "echo",
                "title": "echo",
                "version": VERSION,
            },
            "capabilities": {"experimentalApi": True},
        })
        await transport.send({
            "method": "initialized",
            "params": client_params({}),
        })
        return await request(method, params)
    finally:
        await transport.close()


async def _list_app_server_sessions_rpc(connection: Dict[str, Any]) -> list:
    result = await _app_server_rpc(connection, "thread/list", {
        "sortKey": "updated_at",
        "sortDirection": "desc",
        "archived": False,
        "useStateDbOnly": True,
    })
    threads = []
    if isinstance(result, dict):
        for thread in result.get("data", []):
            thread_id = thread.get("id", "")
            if not thread_id or thread.get("ephemeral"):
                continue
            threads.append({
                "session_id": thread_id,
                "summary": (
                    thread.get("name")
                    or thread.get("preview")
                    or thread_id[:8]
                ),
                "updated_at": float(thread.get("updatedAt") or 0),
            })
    return threads


def list_app_server_sessions(connection: Dict[str, Any]) -> list:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _list_app_server_sessions_rpc(connection)
        )
    finally:
        loop.close()


async def _get_app_server_session_info_rpc(
    connection: Dict[str, Any],
    session_id: str,
) -> Optional[dict]:
    result = await _app_server_rpc(connection, "thread/read", {
        "threadId": session_id,
        "includeTurns": True,
    })
    thread = result.get("thread", {}) if isinstance(result, dict) else {}
    if not thread:
        return None
    last_prompt = None
    last_response = None
    for turn in reversed(thread.get("turns", [])):
        for item in reversed(turn.get("items", [])):
            if item.get("type") == "agentMessage" and last_response is None:
                last_response = item.get("text") or None
            elif item.get("type") == "userMessage" and last_prompt is None:
                texts = [
                    value.get("text", "")
                    for value in item.get("content", [])
                    if value.get("type") == "text"
                ]
                last_prompt = "".join(texts).strip() or None
        if last_prompt:
            break
    return {
        "summary": thread.get("name") or thread.get("preview"),
        "updated_at": float(thread.get("updatedAt") or 0),
        "prompt": last_prompt,
        "response": last_response,
    }


def get_app_server_session_info(
    connection: Dict[str, Any],
    session_id: str,
) -> Optional[dict]:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _get_app_server_session_info_rpc(connection, session_id)
        )
    finally:
        loop.close()
