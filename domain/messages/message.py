"""Provider-facing values and the small async contract used by Echo."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import os
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from ..ports.workspace import DEFAULT_CONFIRM_TOOLS


class MessageType(Enum):
    TEXT = "text"
    TOOL_USE = "tool_use"
    ERROR = "error"
    STOP = "stop"
    THINKING = "thinking"
    PLAN_DELTA = "plan_delta"


@dataclass
class Message:
    type: str
    content: Any = None
    id: Optional[str] = None


@dataclass
class TextBlock:
    text: str
    type: str = field(default="text", init=False)


@dataclass
class AssistantMessage:
    content: List[Any]
    id: Optional[str] = None
    role: str = field(default="assistant", init=False)
    type: str = field(default="assistant", init=False)

@dataclass
class CommonAgentOptions:
    cwd: Optional[str] = None
    model: Optional[str] = None
    plan_mode: bool = False
    session_id: Optional[str] = None
    add_dirs: List[str] = field(default_factory=list)
    connection_state_callback: Optional[Callable] = None

    def __post_init__(self):
        self.cwd = self.cwd or os.getcwd()
        self.add_dirs = list(self.add_dirs or ())


@dataclass
class CodexAgentOptions(CommonAgentOptions):
    disallowed_tools: List[str] = field(default_factory=list)
    app_server_url: Optional[str] = None
    allow_insecure_ws: bool = False
    bearer_token_env: str = ""
    tls_verify: bool = True
    connect_timeout: float = 10.0
    request_timeout: float = 60.0
    ping_interval: float = 25.0
    max_message_bytes: int = 8 * 1024 * 1024
    local_tool_handler: Optional[Callable] = None
    dynamic_tools: List[Dict[str, Any]] = field(default_factory=list)
    local_tools_require_approval: Optional[List[str]] = None
    developer_instructions: Optional[str] = None
    developer_instructions_loader: Optional[Callable] = None
    minimum_codex_version: str = "0.141.0"
    reconnect_max_attempts: int = 5
    reconnect_base_delay: float = 1.0
    request_fields: Dict[str, Any] = field(default_factory=dict)
    request_fields_loader: Optional[Callable] = None

    def __post_init__(self):
        super().__post_init__()
        self.disallowed_tools = list(self.disallowed_tools or ())
        self.dynamic_tools = list(self.dynamic_tools or ())
        self.request_fields = dict(self.request_fields or {})
        approval_tools = self.local_tools_require_approval
        self.local_tools_require_approval = list(
            DEFAULT_CONFIRM_TOOLS if approval_tools is None else approval_tools
        )


@dataclass
class PiAgentOptions(CommonAgentOptions):
    cli_path: Optional[str] = None
    system_prompt: Optional[str] = None
    extra_env: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()
        self.extra_env = dict(self.extra_env or {})


class BaseAgent(ABC):
    """Minimal lifecycle implemented by each provider adapter."""

    def __init__(self, options=None):
        self.options = options or CommonAgentOptions()

    @abstractmethod
    async def connect(self, prompt=None):
        raise NotImplementedError

    @abstractmethod
    async def send_message(self, content, parent_tool_use_id=None, proceed_plan=False):
        raise NotImplementedError

    @abstractmethod
    def receive_messages(self) -> AsyncIterator[Message]:
        raise NotImplementedError

    @abstractmethod
    async def steer(self, text, proceed_plan=False):
        raise NotImplementedError

    @abstractmethod
    async def interrupt(self):
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self):
        raise NotImplementedError

    async def __aenter__(self):
        agent = self
        await agent.connect()
        return agent

    async def __aexit__(self, _kind, _error, _traceback):
        await self.disconnect()
