import unittest

from echo.providers import (
    build_agent_options,
    ProviderConfigurationError,
    create_agent,
    provider_identity,
    resolve_provider,
)
from echo.domain.messages.message import CodexAgentOptions, PiAgentOptions


class ProviderConfigurationTest(unittest.TestCase):
    def test_codex_requires_nested_url(self):
        with self.assertRaises(ProviderConfigurationError):
            resolve_provider({"provider": "codex", "providers": {}})

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(ProviderConfigurationError):
            resolve_provider({"provider": "unknown", "providers": {}})

    def test_pi_can_be_selected_without_codex(self):
        provider = resolve_provider({
            "provider": "pi",
            "providers": {"pi": {"enabled": True}},
        })
        self.assertEqual("pi", provider.name)
        self.assertFalse(provider.capabilities.resume)
        self.assertTrue(provider_identity(provider))

    def test_unknown_provider_cannot_create_agent(self):
        with self.assertRaises(ProviderConfigurationError):
            create_agent("unknown", object())

    def test_provider_options_are_narrow_and_provider_specific(self):
        codex = build_agent_options("codex", {
            "app_server": {"url": "ws://127.0.0.1:4500"},
        })
        pi = build_agent_options("pi", {"cli_path": "/usr/bin/pi"})

        self.assertIsInstance(codex, CodexAgentOptions)
        self.assertIsInstance(pi, PiAgentOptions)
        self.assertFalse(hasattr(codex, "cli_path"))
        self.assertFalse(hasattr(pi, "app_server_url"))
