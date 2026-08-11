"""Provider configuration validation and capability declarations."""

from dataclasses import dataclass
from typing import Any, Dict

from ..domain.messages.message import CodexAgentOptions, PiAgentOptions
from ..domain.conversation.identity import server_identity


class ProviderConfigurationError(ValueError):
    """Raised when the selected provider cannot be started."""


@dataclass(frozen=True)
class ProviderCapabilities:
    resume: bool = False
    rewind: bool = False
    approvals: bool = False


@dataclass(frozen=True)
class ResolvedProvider:
    name: str
    config: Dict[str, Any]
    capabilities: ProviderCapabilities


_CAPABILITIES = {
    "codex": ProviderCapabilities(
        resume=True,
        rewind=True,
        approvals=True,
    ),
    "pi": ProviderCapabilities(),
}


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def resolve_provider(settings) -> ResolvedProvider:
    """Resolve and validate the provider selected in Sublime settings."""
    providers = _mapping(settings.get("providers", {}))
    name = settings.get("provider", "codex")
    name = name.strip().lower() if isinstance(name, str) else ""
    if name not in _CAPABILITIES:
        raise ProviderConfigurationError(
            "Unsupported provider: {}. Expected codex or pi.".format(name or "<empty>")
        )

    config = _mapping(providers.get(name, {}))
    if name == "codex":
        app_server = _mapping(config.get("app_server", {}))
        url = app_server.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ProviderConfigurationError(
                "providers.codex.app_server.url is not configured."
            )
        config["app_server"] = app_server
    elif not config.get("enabled", True):
        raise ProviderConfigurationError("providers.pi is disabled.")

    return ResolvedProvider(name, config, _CAPABILITIES[name])


def provider_identity(provider: ResolvedProvider) -> str:
    """Return a privacy-preserving identity for session routing."""
    if provider.name == "codex":
        return server_identity(provider.config["app_server"].get("url", ""))
    # Do not persist the user's absolute executable path in session settings.
    return server_identity("pi://" + str(provider.config.get("cli_path") or "pi"))


def create_agent(provider_name: str, options):
    """Create the provider implementation without coupling UI code to it."""
    if provider_name == "codex":
        from .codex.client import CodexAgent
        return CodexAgent(options)
    if provider_name == "pi":
        from .pi.client import PiAgent
        return PiAgent(options)
    raise ProviderConfigurationError(
        "Unsupported provider: {}".format(provider_name)
    )


def build_agent_options(provider_name: str, runtime: Dict[str, Any]):
    """Translate normalized runtime data into provider-specific options."""
    common = {
        "cwd": runtime.get("cwd"),
        "add_dirs": runtime.get("add_dirs", []),
        "model": runtime.get("model"),
        "plan_mode": runtime.get("plan_mode", False),
        "session_id": runtime.get("session_id"),
        "connection_state_callback": runtime.get("connection_state_callback"),
    }
    if provider_name == "codex":
        app_server = _mapping(runtime.get("app_server", {}))
        return CodexAgentOptions(
            **common,
            disallowed_tools=runtime.get("disallowed_tools", []),
            app_server_url=app_server.get("url"),
            allow_insecure_ws=app_server.get("allow_insecure_ws", False),
            bearer_token_env=app_server.get("bearer_token_env", ""),
            tls_verify=app_server.get("tls_verify", True),
            connect_timeout=app_server.get("connect_timeout_seconds", 10),
            request_timeout=app_server.get("request_timeout_seconds", 60),
            ping_interval=app_server.get("ping_interval_seconds", 25),
            max_message_bytes=app_server.get(
                "max_message_bytes", 8 * 1024 * 1024
            ),
            local_tool_handler=runtime.get("local_tool_handler"),
            dynamic_tools=runtime.get("dynamic_tools", []),
            local_tools_require_approval=runtime.get(
                "local_tools_require_approval"
            ),
            developer_instructions_loader=runtime.get(
                "developer_instructions_loader"
            ),
            minimum_codex_version=app_server.get(
                "minimum_codex_version", "0.141.0"
            ),
            reconnect_max_attempts=app_server.get("reconnect_max_attempts", 5),
            reconnect_base_delay=app_server.get(
                "reconnect_base_delay_seconds", 1.0
            ),
            request_fields=app_server.get("request_fields", {}),
            request_fields_loader=runtime.get("request_fields_loader"),
        )
    if provider_name == "pi":
        return PiAgentOptions(
            **common,
            cli_path=runtime.get("cli_path"),
            system_prompt=runtime.get("system_prompt"),
            extra_env=runtime.get("env", {}),
        )
    raise ProviderConfigurationError(
        "Unsupported provider: {}".format(provider_name)
    )


def normalize_provider_model(model):
    """Normalize a model value without exposing a provider implementation."""
    if not isinstance(model, str):
        return None
    model = model.strip()
    if not model or model.lower() == "default":
        return None
    return model


def list_provider_sessions(provider: ResolvedProvider):
    """List resumable sessions through the selected provider boundary."""
    if provider.name == "codex":
        from ..transport.session_client import list_app_server_sessions
        return list_app_server_sessions(provider.config["app_server"])
    return []


def get_provider_session_info(provider: ResolvedProvider, session_id: str):
    """Read provider session metadata in the common UI shape."""
    if provider.name == "codex":
        from ..transport.session_client import get_app_server_session_info
        return get_app_server_session_info(
            provider.config["app_server"], session_id
        )
    return None
