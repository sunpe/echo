import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import patch

from echo.application import bootstrap
from echo.application.bootstrap import export_plugin_types, load_package_entry


class BootstrapTest(unittest.TestCase):
    def test_package_entry_can_be_imported_twice_without_removing_children(self):
        package_name = "echo_reload_fixture"

        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary, package_name)
            application = package / "application"
            application.mkdir(parents=True)
            package.joinpath("__init__.py").write_text("", encoding="utf-8")
            application.joinpath("__init__.py").write_text("", encoding="utf-8")
            package.joinpath("echo.py").write_text(
                Path(__file__).resolve().parents[2].joinpath("echo.py").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            application.joinpath("bootstrap.py").write_text(
                Path(__file__).resolve().parents[2]
                .joinpath("application", "bootstrap.py")
                .read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            application.joinpath("entry.py").write_text(
                "def plugin_loaded(): pass\n"
                "def plugin_unloaded(): pass\n",
                encoding="utf-8",
            )
            application.joinpath("child.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )
            application.joinpath("__init__.py").write_text(
                "", encoding="utf-8"
            )
            package.joinpath("__init__.py").write_text(
                "", encoding="utf-8"
            )

            original_path = list(sys.path)
            sys.path.insert(0, temporary)
            try:
                child = importlib.import_module(
                    package_name + ".application.child"
                )
                first = importlib.import_module(package_name + ".echo")
                second = importlib.reload(first)
            finally:
                sys.path[:] = original_path
                for name in tuple(sys.modules):
                    if name == package_name or name.startswith(package_name + "."):
                        sys.modules.pop(name, None)

        self.assertTrue(callable(second.plugin_loaded))
        self.assertEqual(1, child.VALUE)

    def test_load_package_entry_reloads_only_existing_entry(self):
        entry = ModuleType("echo.application.entry")
        child = ModuleType("echo.sublime_adapter.presentation.chat_view")
        modules = {
            "echo.application.entry": entry,
            "echo.sublime_adapter.presentation.chat_view": child,
        }

        with patch.object(bootstrap.sys, "modules", modules):
            with patch.object(
                bootstrap.importlib, "reload", return_value=entry
            ) as reload_module:
                loaded = load_package_entry("echo", "application.entry")

        reload_module.assert_called_once_with(entry)
        self.assertIs(entry, loaded)
        self.assertIs(child, modules[
            "echo.sublime_adapter.presentation.chat_view"
        ])

    def test_only_echo_package_types_are_exported(self):
        local = type("EchoCommand", (), {})
        local.__module__ = "echo.application.commands"
        foreign = type("EchoForeign", (), {})
        foreign.__module__ = "other.commands"
        module = SimpleNamespace(
            __package__="echo.application",
            EchoCommand=local,
            EchoForeign=foreign,
            Helper=type("Helper", (), {}),
            value="ignored",
        )

        self.assertEqual(
            {"EchoCommand": local}, export_plugin_types(module, [module])
        )


if __name__ == "__main__":
    unittest.main()
