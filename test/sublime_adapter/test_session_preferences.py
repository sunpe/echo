import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.modules.setdefault("sublime", MagicMock())

from echo.application.session_preferences import SessionPreferences
from echo.sublime_adapter.presentation.ui_components import PlanMode
from echo.shared.settings import ECHO_MODEL, ECHO_PLAN_MODE


class SessionPreferencesTest(unittest.TestCase):
    def setUp(self):
        self.settings = MagicMock()
        self.window = SimpleNamespace(settings=lambda: self.settings)
        self.session = MagicMock()
        self.context = patch(
            "echo.application.session_preferences.EchoWindowContext",
            return_value=SimpleNamespace(session=self.session),
        )
        self.context.start()
        self.addCleanup(self.context.stop)

    def test_model_updates_storage_controls_and_live_worker(self):
        SessionPreferences(self.window).select_model(" gpt-test ")

        self.settings.set.assert_called_once_with(ECHO_MODEL, "gpt-test")
        self.session.model_phantom.update.assert_called_once_with()
        self.session.agent_thread.reconfigure.assert_called_once_with(
            model="gpt-test"
        )

    def test_empty_model_erases_window_override(self):
        SessionPreferences(self.window).select_model(" default ")

        self.settings.erase.assert_called_once_with(ECHO_MODEL)

    def test_plan_setting_reaches_session_controller(self):
        SessionPreferences(self.window).select_plan("planning")

        self.settings.set.assert_called_once_with(
            ECHO_PLAN_MODE, PlanMode.PLANNING.value
        )
        self.session.apply_plan_mode.assert_called_once_with(PlanMode.PLANNING)


if __name__ == "__main__":
    unittest.main()
