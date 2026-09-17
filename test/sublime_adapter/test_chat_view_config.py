import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


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
from echo.sublime_adapter.view_service import ChatViewService
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

    def test_new_chat_defaults_to_existing_session_in_current_column(self):
        window = MagicMock()
        view = MagicMock()
        session = MagicMock()
        service = ChatViewService(window)
        service.context = SimpleNamespace(
            echo_view=lambda: view,
            session=session,
        )
        service._present = MagicMock()

        result = service.open()

        self.assertIs(view, result)
        service._present.assert_called_once_with(
            view, dedicated_pane=False
        )
        session.reset_conversation.assert_not_called()
        window.new_file.assert_not_called()

    def test_disabled_dedicated_pane_focuses_without_creating_column(self):
        window = MagicMock()
        view = MagicMock()
        service = ChatViewService(window)

        with patch(
            "echo.sublime_adapter.view_service.place_in_dedicated_pane"
        ) as dedicated:
            service._present(view, dedicated_pane=False)

        dedicated.assert_not_called()
        window.focus_view.assert_called_once_with(view)


if __name__ == "__main__":
    unittest.main()
