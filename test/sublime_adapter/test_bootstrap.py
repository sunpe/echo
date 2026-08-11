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
    def test_package_entry_replaces_stale_bootstrap_before_calling_it(self):
        package_name = "echo_reload_fixture"
        stale_bootstrap_name = package_name + ".application.bootstrap"
        stale_bootstrap = ModuleType(stale_bootstrap_name)

        def stale_load_package_entry(_package_name, _relative_module):
            raise AssertionError("stale two-argument bootstrap was called")

        stale_bootstrap.load_package_entry = stale_load_package_entry

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
                "from types import SimpleNamespace\n"
                "def load_package_entry(package_name, relative_module, "
                "preserved_modules=()):\n"
                "    return SimpleNamespace(plugin_loaded=lambda: None, "
                "plugin_unloaded=lambda: None)\n"
                "def export_plugin_types(entry_module):\n"
                "    return {}\n",
                encoding="utf-8",
            )

            original_path = list(sys.path)
            sys.path.insert(0, temporary)
            sys.modules[stale_bootstrap_name] = stale_bootstrap
            try:
                entry = importlib.import_module(package_name + ".echo")
            finally:
                sys.path[:] = original_path
                for name in tuple(sys.modules):
                    if name == package_name or name.startswith(package_name + "."):
                        sys.modules.pop(name, None)

        self.assertTrue(callable(entry.plugin_loaded))

    def test_load_package_entry_preserves_active_plugin_module(self):
        modules = {
            "echo.echo": object(),
            "echo.application.old": object(),
        }
        entry = object()

        with patch.object(bootstrap.sys, "modules", modules):
            with patch.object(
                bootstrap.importlib, "import_module", return_value=entry
            ):
                loaded = load_package_entry(
                    "echo", "application.entry", ("echo.echo",)
                )

            self.assertIn("echo.echo", modules)
            self.assertNotIn("echo.application.old", modules)
            self.assertIs(entry, loaded)

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
