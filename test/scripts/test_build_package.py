import tempfile
import unittest
from pathlib import Path

from scripts.build_package import package_files


class BuildPackageTest(unittest.TestCase):
    def test_requested_sublime_entry_points_are_packaged(self):
        names = {path.name for path in package_files()}

        self.assertTrue({
            ".python-version",
            "Default (OSX).sublime-keymap",
            "Default.sublime-keymap",
            "Side Bar.sublime-menu",
        }.issubset(names))
        self.assertNotIn("Context.sublime-menu", names)

    def test_only_explicit_package_paths_are_included(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "providers").mkdir()
            (root / "providers" / "client.py").write_text("safe")
            (root / "workspace").mkdir()
            (root / "workspace" / "executor.py").write_text("safe")
            (root / "README.md").write_text("safe")
            (root / ".python-version").write_text("3.8")
            (root / ".env").write_text("secret")
            (root / "notes.txt").write_text("private")
            output = root / "echo.sublime-package"
            output.write_text("old archive")

            included = {
                path.relative_to(root).as_posix()
                for path in package_files(root=root, output=output)
            }

        self.assertEqual(
            {
                ".python-version",
                "README.md",
                "providers/client.py",
                "workspace/executor.py",
            },
            included,
        )


if __name__ == "__main__":
    unittest.main()
