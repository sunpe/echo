import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock


sublime = sys.modules.setdefault("sublime", MagicMock())
sys.modules.setdefault(
    "sublime_plugin",
    SimpleNamespace(ApplicationCommand=object),
)

from echo.application.settings_commands import _open_resource


class SettingsCommandTest(unittest.TestCase):
    def test_package_variable_is_passed_without_python_formatting(self):
        sublime.run_command.reset_mock()

        _open_resource("echo.sublime-settings", "{}\n")

        sublime.run_command.assert_called_once_with("edit_settings", {
            "base_file": "${packages}/echo/echo.sublime-settings",
            "default": "{}\n",
        })


if __name__ == "__main__":
    unittest.main()
