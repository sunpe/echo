"""Async WebSocket transport for a remote Codex app-server."""

import asyncio
import json
import os
import ssl
from typing import Any, AsyncIterator, Dict
from urllib.parse import urlparse


def _load_websockets():
    """Prefer the package-pinned runtime over Sublime's shared modules."""
    try:
        from ..vendor import websockets
    except (ImportError, ValueError):
        try:
            from vendor import websockets
        except ImportError:
            try:
                import websockets
            except ImportError as exc:
                raise RuntimeError(
                    "The bundled websockets 10.4 runtime is unavailable."
                ) from exc
    return websockets


class WebSocketTransport:
    def __init__(
        self,
        url: str,
        allow_insecure_ws: bool = False,
        bearer_token_env: str = "",
        tls_verify: bool = True,
        connect_timeout: float = 10.0,
        ping_interval: float = 25.0,
        max_message_bytes: int = 8 * 1024 * 1024,
    ):
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise ValueError("app_server.url must use ws:// or wss://")
        if (
            not allow_insecure_ws
            and parsed.scheme == "ws"
            and parsed.hostname not in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError(
                "Plain remote ws:// requires app_server.allow_insecure_ws=true"
            )
        self.url = url
        self.bearer_token_env = (
            bearer_token_env.strip()
            if isinstance(bearer_token_env, str)
            else ""
        )
        self.tls_verify = tls_verify
        self.connect_timeout = connect_timeout
        self.ping_interval = ping_interval
        self.max_message_bytes = max_message_bytes
        self._socket = None

    @property
    def closed(self) -> bool:
        return self._socket is None or bool(getattr(self._socket, "closed", False))

    async def connect(self) -> None:
        websockets = _load_websockets()

        ssl_context = None
        if self.url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if not self.tls_verify:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        connect_options = {
            "ssl": ssl_context,
            "ping_interval": self.ping_interval,
            "max_size": self.max_message_bytes,
            "open_timeout": self.connect_timeout,
        }
        if self.bearer_token_env:
            token = os.environ.get(self.bearer_token_env)
            if not token:
                raise RuntimeError(
                    "Codex app-server bearer token environment variable is "
                    "not set: {}".format(self.bearer_token_env)
                )
            connect_options["extra_headers"] = {
                "Authorization": "Bearer " + token
            }

        self._socket = await asyncio.wait_for(
            websockets.connect(self.url, **connect_options),
            timeout=self.connect_timeout + 1,
        )

    async def send(self, message: Dict[str, Any]) -> None:
        if self._socket is None:
            raise RuntimeError("WebSocket is not connected")
        await self._socket.send(json.dumps(message, separators=(",", ":")))

    async def messages(self) -> AsyncIterator[Dict[str, Any]]:
        if self._socket is None:
            return
        async for raw in self._socket:
            if not isinstance(raw, str):
                raise RuntimeError("Codex app-server sent a binary WebSocket frame")
            yield json.loads(raw)

    async def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()
