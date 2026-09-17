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
from echo.sublime_adapter.presentation.transcript_writer import ReasoningTranscript


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

    def test_reasoning_is_routed_separately_from_reply(self):
        processor = self.make_processor()
        processor.output.reasoning = MagicMock()

        processor.receive(Message("thinking_delta", "checking", id="r1"))
        processor.receive(Message("thinking", "checking", id="r1"))

        processor.output.reasoning.delta.assert_called_once_with(
            "r1", "checking"
        )
        processor.output.reasoning.complete.assert_called_once_with(
            "r1", "checking"
        )
        self.assertEqual(0, self.marker_count(processor))

    def test_missing_thread_fallback_is_persisted_and_explained(self):
        processor = self.make_processor()
        processor.output.notice = MagicMock()

        processor.receive(Message(
            "thread_fallback", {"session_id": "new-thread"}
        ))

        processor.session.set_view_session_id.assert_called_once_with(
            processor.session.chat_view, "new-thread"
        )
        processor.output.notice.assert_called_once_with(
            "远程服务器未找到原会话，已按新会话重新连接。"
        )


class ReasoningTranscriptScenario(unittest.TestCase):
    class Surface:
        def __init__(self):
            self.position = 0
            self.writes = []
            self.fold = MagicMock()

        def append(self, text, on_commit=None):
            start = self.position
            self.position += len(text)
            self.writes.append(text)
            if on_commit:
                on_commit(start, self.position)

    def test_streamed_reasoning_folds_body_without_duplicate_completion(
        self
    ):
        surface = self.Surface()
        reasoning = ReasoningTranscript(surface)

        reasoning.delta("r1", "first")
        reasoning.delta("r1", " second")
        reasoning.complete("r1", "first second")

        self.assertEqual(
            ["\n◇ 思考摘要\n\n", "first", " second", "\n\n"],
            surface.writes,
        )
        body_start = len(surface.writes[0])
        surface.fold.assert_called_once_with(
            body_start, body_start + len("first second")
        )

    def test_completed_only_reasoning_is_still_rendered(self):
        surface = self.Surface()
        reasoning = ReasoningTranscript(surface)

        reasoning.complete("r1", "summary")

        self.assertEqual("summary", surface.writes[1])


if __name__ == "__main__":
    unittest.main()
