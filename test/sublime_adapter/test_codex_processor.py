"""Transcript rendering and workspace-link scenarios."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.modules.setdefault("sublime", MagicMock())

from echo.domain.messages.message import AssistantMessage, Message, TextBlock
from echo.sublime_adapter.presentation.chat_processor import EchoMessageProcessor
from echo.sublime_adapter.file_navigation import parse_file_target
from echo.sublime_adapter.presentation.transcript_writer import ToolTranscript


class WorkspaceLinkScenario(unittest.TestCase):
    @patch("echo.sublime_adapter.file_navigation.os.path.isfile", return_value=True)
    def test_platform_specific_targets_have_one_canonical_shape(self, _exists):
        examples = {
            r"C:\project\src\app.py:12:5": (r"C:\project\src\app.py", 12, 5),
            "file:///C:/project/src/app.py#L12C5": (
                "C:/project/src/app.py", 12, 5
            ),
            "file://server/share/app.py#L8": (
                "//server/share/app.py", 8, None
            ),
        }
        for target, expected in examples.items():
            with self.subTest(target=target):
                self.assertEqual(
                    expected,
                    parse_file_target(target, "/workspace"),
                )

    def test_workspace_boundary_rejects_existing_external_file(self):
        with tempfile.TemporaryDirectory() as workspace, \
                tempfile.TemporaryDirectory() as external:
            secret = Path(external, "secret.txt")
            secret.write_text("secret", encoding="utf-8")

            resolved = parse_file_target(
                str(secret), workspace, [workspace]
            )

        self.assertIsNone(resolved)


class TranscriptScenario(unittest.TestCase):
    def make_processor(self, cwd="/workspace"):
        session = MagicMock()
        session.cwd = cwd
        session.agent_thread = SimpleNamespace(
            cwd=cwd, agent_config={"plan_mode": False}
        )
        processor = EchoMessageProcessor(session)
        processor.output.write = MagicMock()
        return processor

    @staticmethod
    def marker_count(processor):
        return sum(
            call.args == ("\n●\n\n",)
            for call in processor.output.write.call_args_list
        )

    def test_reply_marker_is_scoped_to_turn_not_message_kind(self):
        processor = self.make_processor()

        processor.receive(Message(
            "turn_started", {"turnId": "one", "turnIndex": 1}
        ))
        processor.receive(Message(
            "tool_use", {"name": "command_execution", "command": "pwd"}
        ))
        processor.receive(AssistantMessage([TextBlock("done")]))
        self.assertEqual(1, self.marker_count(processor))

        processor.receive(Message("stop"))
        processor.receive(AssistantMessage([TextBlock("next")]))
        self.assertEqual(2, self.marker_count(processor))

    def test_tool_renderer_builds_independent_file_sections(self):
        renderer = ToolTranscript(lambda: "/workspace")
        block = {
            "name": "fileChange",
            "changes": [
                {
                    "path": "/workspace/chat/processor.py",
                    "diff": "@@ -4,2 +4,3 @@\n old\n+new\n",
                },
                {
                    "path": "/workspace/chat/view.py",
                    "diff": "@@ -9 +12 @@\n-before\n+after\n",
                },
            ],
        }

        rendered = renderer.format(block)

        self.assertIn("⏺ fileChange chat/processor.py#L4", rendered)
        self.assertIn("⏺ fileChange chat/view.py#L12", rendered)
        self.assertEqual(2, rendered.count("````diff"))

    def test_multiline_command_is_indented_below_heading(self):
        renderer = ToolTranscript(lambda: "/workspace")

        rendered = renderer.format({
            "name": "command_execution",
            "command": "python -m unittest\necho finished",
        })

        self.assertEqual(
            "⏺ command (python -m unittest)\n\n    echo finished\n",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
