import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.modules.setdefault("sublime", MagicMock())
sys.modules.setdefault(
    "sublime_plugin",
    SimpleNamespace(TextCommand=object),
)

from echo.sublime_adapter.workspace_bridge import SublimeWorkspaceTools


class FakeRegion:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class FakeView:
    def __init__(self, path, text, dirty=True):
        self._path = path
        self.text = text
        self._dirty = dirty
        self._change_count = 1

    def file_name(self):
        return self._path

    def is_valid(self):
        return True

    def is_dirty(self):
        return self._dirty

    def size(self):
        return len(self.text)

    def substr(self, region):
        return self.text[region.start:region.end]

    def change_count(self):
        return self._change_count

    def run_command(self, name, arguments):
        if name != "echo_apply_workspace_edit":
            raise AssertionError(name)
        self.text = arguments["content"]
        self._dirty = True
        self._change_count += 1


class FakeWindow:
    def __init__(self, views=None):
        self._views = list(views or [])
        self.opened = []

    def views(self):
        return list(self._views)

    def find_open_file(self, path):
        for view in self._views:
            if view.file_name() == path:
                return view
        return None

    def open_file(self, path):
        self.opened.append(path)


class WorkspaceBridgeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = self.temporary.name
        self.path = os.path.join(self.root, "main.py")
        Path(self.path).write_text("disk value\n", encoding="utf-8")
        self.view = FakeView(self.path, "buffer value\nneedle\n")
        self.window = FakeWindow([self.view])
        self.changes = []
        self.bridge = SublimeWorkspaceTools(
            self.window,
            [self.root],
            ["read", "search", "write", "create"],
            on_file_change=lambda *args: self.changes.append(args),
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _run_immediately(callback, _delay=0):
        callback()

    async def test_read_and_search_prefer_dirty_buffer(self):
        with patch(
            "echo.sublime_adapter.workspace_bridge.sublime.Region", FakeRegion
        ), patch(
            "echo.sublime_adapter.workspace_bridge.sublime.set_timeout",
            side_effect=self._run_immediately,
        ):
            read = await self.bridge(
                "local_workspace", "read", {"path": "main.py"}
            )
            search = await self.bridge("local_workspace", "search", {
                "query": "needle",
                "path": ".",
            })

        self.assertEqual("buffer value\nneedle\n", read["text"])
        self.assertTrue(read["dirty"])
        self.assertEqual(
            hashlib.sha256(b"buffer value\nneedle\n").hexdigest(),
            read["sha256"],
        )
        self.assertEqual(
            [{"path": "main.py", "line": 2, "text": "needle"}],
            search["matches"],
        )

    async def test_write_uses_buffer_undo_command_and_records_diff(self):
        with patch(
            "echo.sublime_adapter.workspace_bridge.sublime.Region", FakeRegion
        ), patch(
            "echo.sublime_adapter.workspace_bridge.sublime.set_timeout",
            side_effect=self._run_immediately,
        ):
            digest = hashlib.sha256(self.view.text.encode("utf-8")).hexdigest()
            result = await self.bridge("local_workspace", "write", {
                "path": "main.py",
                "expectedSha256": digest,
                "replacements": [{"old": "buffer", "new": "updated"}],
            })

        self.assertEqual("updated value\nneedle\n", self.view.text)
        self.assertEqual(2, result["changeCount"])
        self.assertEqual(1, len(self.changes))
        self.assertIn("-buffer value", self.changes[0][2])
        self.assertIn("+updated value", self.changes[0][2])
        self.assertEqual("disk value\n", Path(self.path).read_text())

    async def test_create_opens_file_and_records_diff(self):
        with patch(
            "echo.sublime_adapter.workspace_bridge.sublime.set_timeout",
            side_effect=self._run_immediately,
        ):
            result = await self.bridge("local_workspace", "create", {
                "path": "new.py",
                "content": "new file\n",
            })
        created = os.path.realpath(os.path.join(self.root, "new.py"))
        self.assertEqual([created], self.window.opened)
        self.assertEqual("new file\n", Path(created).read_text())
        self.assertEqual("new.py", result["path"])
        self.assertEqual(1, len(self.changes))
        self.assertIn("+new file", self.changes[0][2])

    def test_project_instructions_prefer_open_buffer(self):
        instructions_path = os.path.join(self.root, "AGENTS.md")
        Path(instructions_path).write_text("disk instruction", encoding="utf-8")
        instructions_view = FakeView(
            instructions_path, "buffer instruction", dirty=True
        )
        self.window._views.append(instructions_view)

        with patch("echo.sublime_adapter.workspace_bridge.sublime.Region", FakeRegion):
            instructions = self.bridge._load_project_instructions_main()

        self.assertIn("buffer instruction", instructions)
        self.assertNotIn("disk instruction", instructions)

    async def test_async_bridge_preserves_buffer_and_disk_write_semantics(self):
        closed = Path(self.root, "closed.py")
        closed.write_text("before\n", encoding="utf-8")
        digest = hashlib.sha256(b"before\n").hexdigest()

        with patch(
            "echo.sublime_adapter.workspace_bridge.sublime.set_timeout",
            side_effect=self._run_immediately,
        ), patch(
            "echo.sublime_adapter.workspace_bridge.sublime.Region", FakeRegion
        ):
            buffered = await self.bridge(
                "local_workspace", "read", {"path": "main.py"}
            )
            updated = await self.bridge("local_workspace", "write", {
                "path": "closed.py",
                "expectedSha256": digest,
                "replacements": [{"old": "before", "new": "after"}],
            })

        self.assertTrue(buffered["dirty"])
        self.assertEqual("buffer value\nneedle\n", buffered["text"])
        self.assertEqual("after\n", closed.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(b"after\n").hexdigest(), updated["sha256"]
        )
        self.assertEqual(1, len(self.changes))


if __name__ == "__main__":
    unittest.main()
