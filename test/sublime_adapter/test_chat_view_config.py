import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock


sys.modules.setdefault("sublime", MagicMock())
sys.modules.setdefault(
    "sublime_plugin",
    SimpleNamespace(
        ApplicationCommand=object,
        EventListener=object,
        ListInputHandler=object,
        TextCommand=object,
        TextInputHandler=object,
        WindowCommand=object,
    ),
)
from echo.sublime_adapter.presentation.chat_view import ChatSession
from echo.sublime_adapter.presentation.ui_components import (
    CHAT_CONNECTION_STATE,
    PlanMode,
)
from echo.domain.conversation.session_runtime import RuntimePhase


class AgentConfigTest(unittest.TestCase):
    def test_provider_is_resolved_before_model_is_normalized(self):
        session = ChatSession.__new__(ChatSession)
        session.window = SimpleNamespace(
            settings=lambda: {"echo_model": " gpt-test "}
        )
        settings = {
            "provider": "codex",
            "providers": {
                "codex": {
                    "app_server": {"url": "ws://127.0.0.1:4500"},
                },
            },
        }

        config = session._build_agent_config(
            settings, "session-1", plan_mode=PlanMode.FAST
        )

        self.assertEqual("codex", config["provider"])
        self.assertEqual("gpt-test", config["model"])
        self.assertEqual("session-1", config["session_id"])

    def test_runtime_phase_refreshes_connection_status(self):
        session = ChatSession.__new__(ChatSession)
        session.chat_view = MagicMock()
        session.model_phantom = MagicMock()

        session._on_runtime_phase_change(RuntimePhase.ACTIVE)

        session.chat_view.settings().set.assert_called_once_with(
            CHAT_CONNECTION_STATE, "connecting"
        )
        session.model_phantom.update.assert_called_once_with()

    def test_first_message_hides_welcome_card_once(self):
        session = ChatSession.__new__(ChatSession)
        session.has_sent_message = False
        session.welcome_panel = MagicMock()

        session.mark_conversation_started()
        session.mark_conversation_started()

        self.assertTrue(session.has_sent_message)
        session.welcome_panel.clear.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
