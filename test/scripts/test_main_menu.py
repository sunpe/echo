import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class MainMenuTest(unittest.TestCase):
    def test_key_binding_editor_is_exposed(self):
        source = ROOT.joinpath("Main.sublime-menu").read_text(encoding="utf-8")

        self.assertIn("echo_edit_key_bindings", source)

    def test_package_settings_creates_or_extends_public_menu_nodes(self):
        menu = json.loads(
            ROOT.joinpath("Main.sublime-menu").read_text(encoding="utf-8")
        )
        self.assertLess(
            next(i for i, item in enumerate(menu) if item.get("id") == "preferences"),
            next(i for i, item in enumerate(menu) if item.get("id") == "echo-workspace"),
        )
        preferences = next(item for item in menu if item.get("id") == "preferences")
        package_settings = next(
            item for item in preferences["children"]
            if item.get("id") == "package-settings"
        )

        self.assertNotIn("caption", preferences)
        self.assertEqual("Package Settings", package_settings["caption"])
        self.assertEqual("P", package_settings["mnemonic"])
        echo_settings = package_settings["children"][0]
        self.assertEqual("Echo", echo_settings["caption"])
        self.assertEqual(
            "echo-package-settings",
            echo_settings["id"],
        )
        self.assertEqual(
            ["echo_edit_settings", "echo_edit_key_bindings"],
            [item["command"] for item in echo_settings["children"]],
        )

    def test_new_chat_stays_in_current_column(self):
        menu = json.loads(
            ROOT.joinpath("Main.sublime-menu").read_text(encoding="utf-8")
        )
        echo_menu = next(
            item for item in menu if item.get("id") == "echo-workspace"
        )
        new_chat = next(
            item for item in echo_menu["children"]
            if item.get("caption") == "new chat"
        )

        self.assertEqual("echo_chat_cli", new_chat["command"])
        self.assertEqual({"dedicated_pane": False}, new_chat["args"])

        commands = json.loads(
            ROOT.joinpath("Echo.sublime-commands").read_text(encoding="utf-8")
        )
        palette_new_chat = next(
            item for item in commands if item.get("command") == "echo_chat_cli"
        )
        self.assertEqual(
            {"dedicated_pane": False}, palette_new_chat["args"]
        )


if __name__ == "__main__":
    unittest.main()
