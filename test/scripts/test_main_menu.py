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
        preferences = next(item for item in menu if item.get("id") == "preferences")
        package_settings = next(
            item for item in preferences["children"]
            if item.get("id") == "package-settings"
        )

        self.assertNotIn("caption", preferences)
        self.assertEqual("Package Settings", package_settings["caption"])
        self.assertEqual("P", package_settings["mnemonic"])
        self.assertEqual(
            "echo-package-settings",
            package_settings["children"][0]["id"],
        )


if __name__ == "__main__":
    unittest.main()
