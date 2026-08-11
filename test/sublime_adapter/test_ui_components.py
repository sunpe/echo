import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.modules.setdefault("sublime", MagicMock())

from echo.runtime.session_registry import filter_registered_sessions
from echo.sublime_adapter.completions import MAX_DIRECTORY_COMPLETIONS, build_file_completions
from echo.sublime_adapter.presentation.ui_components import (
    CHAT_APPROVE_MODE,
    CHAT_CONNECTION_STATE,
    QuestionSequence,
)
from echo.sublime_adapter.presentation.approval_ui import ApprovalCard
from echo.sublime_adapter.presentation.composer_controls import ComposerControls
from echo.sublime_adapter.presentation.welcome_panel import WelcomePanel


class CompletionProviderTest(unittest.TestCase):
    def test_directory_scan_is_single_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(MAX_DIRECTORY_COMPLETIONS + 5):
                root.joinpath("file-{:03d}.txt".format(index)).touch()
            window = SimpleNamespace(
                folders=lambda: [temporary],
                views=lambda: [],
            )
            from sublime_adapter import completions
            original_scandir = completions.os.scandir
            with patch(
                "echo.sublime_adapter.completions.os.scandir",
                wraps=original_scandir,
            ) as scandir:
                values = build_file_completions(window, "echo_chat")

        self.assertEqual(1, scandir.call_count)
        self.assertEqual(MAX_DIRECTORY_COMPLETIONS, len(values))


class ComposerControlsTest(unittest.TestCase):
    def test_accepts_session_lookup_from_chat_session(self):
        lookup = lambda: None
        panel = ComposerControls(MagicMock(), MagicMock(), session_lookup=lookup)

        self.assertIs(lookup, panel.session_lookup)

    def test_update_renders_model_panel(self):
        view = MagicMock()
        view.size.return_value = 0
        view_settings = MagicMock()
        view_settings.has.return_value = False
        view.settings.return_value = view_settings
        window = MagicMock()

        panel = ComposerControls(view, window)
        panel.phantom_set = MagicMock()
        with patch("echo.sublime_adapter.presentation.composer_controls.sublime.load_settings") as load_settings:
            load_settings.return_value.get.return_value = {
                "providers": {"codex": {"app_server": {"url": ""}}},
            }
            panel.update()

        panel.phantom_set.update.assert_called_once()

    def test_update_shows_current_approve_mode(self):
        view = MagicMock()
        view.size.return_value = 0
        view.get_regions.return_value = []
        view_settings = MagicMock()
        view_settings.has.return_value = False
        view_settings.get.side_effect = lambda _key, default=None: default
        view.settings.return_value = view_settings

        window = MagicMock()
        window.settings.return_value.get.side_effect = (
            lambda key, default=None: "accept-all"
            if key == CHAT_APPROVE_MODE else default
        )
        rendered = []

        def capture_phantom(_region, markup, _layout, _on_navigate):
            rendered.append(markup)
            return object()

        with patch("echo.sublime_adapter.presentation.composer_controls.sublime.load_settings") as load_settings, \
                patch(
                    "echo.sublime_adapter.presentation.composer_controls.sublime.Phantom",
                    side_effect=capture_phantom,
                ):
            load_settings.return_value.get.side_effect = (
                lambda key, default=None: "codex"
                if key == "provider" else default
            )
            ComposerControls(view, window).update()

        self.assertIn('href="approve"', rendered[0])
        self.assertIn("Approval</span> All", rendered[0])
        self.assertIn("codex&nbsp;&nbsp;·&nbsp;&nbsp;", rendered[0])
        self.assertNotIn("MESSAGE", rendered[0])
        self.assertNotIn("type below", rendered[0])

    def test_approve_control_opens_mode_selector(self):
        window = MagicMock()
        panel = ComposerControls(MagicMock(), window)

        panel.navigate("approve")

        window.run_command.assert_called_once_with(
            "echo_chat_set_approve_mode"
        )

    def test_stop_link_interrupts_the_active_turn(self):
        window = MagicMock()
        panel = ComposerControls(MagicMock(), window)

        panel.navigate("stop_conversation")

        window.run_command.assert_called_once_with(
            "echo_chat_interrupt", {"confirm": True}
        )

    def test_connection_state_uses_success_and_failure_colors(self):
        def render(state):
            view = MagicMock()
            view.size.return_value = 0
            view.get_regions.return_value = []
            view.settings.return_value.has.return_value = False
            view.settings.return_value.get.side_effect = (
                lambda key, default=None: state
                if key == CHAT_CONNECTION_STATE else default
            )
            window = MagicMock()
            window.settings.return_value.get.side_effect = (
                lambda _key, default=None: default
            )
            rendered = []

            def capture(_region, markup, _layout, _navigate):
                rendered.append(markup)
                return object()

            with patch(
                "echo.sublime_adapter.presentation.composer_controls.sublime.load_settings"
            ) as load_settings, patch(
                "echo.sublime_adapter.presentation.composer_controls.sublime.Phantom",
                side_effect=capture,
            ):
                load_settings.return_value.get.side_effect = (
                    lambda key, default=None: "codex"
                    if key == "provider" else default
                )
                panel = ComposerControls(view, window)
                panel.phantom_set = MagicMock()
                panel.update()
            return rendered[0]

        connected = render("ready")
        failed = render("failed")

        self.assertIn(".connection-ok{color:var(--greenish)}", connected)
        self.assertIn(
            'class="state connection-ok">● Connected</span>', connected
        )
        self.assertIn(".connection-error{color:var(--redish)}", failed)
        self.assertIn(
            'class="state connection-error">● Unavailable</span>', failed
        )

class WelcomePanelTest(unittest.TestCase):
    def test_card_presents_workspace_without_writing_to_transcript(self):
        rendered = []

        def capture(_region, markup, _layout):
            rendered.append(markup)
            return object()

        workspace = "/Users/example/workspace/echo"
        panel = WelcomePanel(MagicMock(), workspace)
        panel._phantoms = MagicMock()
        with patch(
            "echo.sublime_adapter.presentation.welcome_panel.sublime.Phantom",
            side_effect=capture,
        ), patch(
            "echo.sublime_adapter.presentation.welcome_panel.sublime.platform",
            return_value="osx",
        ):
            panel.update()

        self.assertIn("workspace assistant", rendered[0])
        self.assertIn("…/example/workspace/echo", rendered[0])
        self.assertIn('title="{}"'.format(workspace), rendered[0])
        self.assertIn("⌘↵", rendered[0])
        self.assertIn("⌘Esc", rendered[0])
        panel._phantoms.update.assert_called_once()


class SessionFilteringTest(unittest.TestCase):
    def test_empty_registry_does_not_expose_server_sessions(self):
        sessions = [{"session_id": "one"}, {"session_id": "two"}]

        self.assertEqual([], filter_registered_sessions(sessions, []))
        self.assertEqual(
            [{"session_id": "two"}],
            filter_registered_sessions(sessions, ["two"]),
        )


class QuestionSequenceTest(unittest.TestCase):
    def test_all_questions_are_collected_before_response(self):
        responses = []
        cancelled = []
        window = object()
        input_data = {"questions": [
            {
                "id": "language",
                "question": "Language?",
                "options": [
                    {"label": "Python"},
                    {"label": "JavaScript"},
                ],
            },
            {
                "id": "checks",
                "question": "Checks?",
                "multiSelect": True,
                "options": [
                    {"label": "Tests"},
                    {"label": "Lint"},
                ],
            },
        ]}
        selections = {
            "Language?": [0],
            "Checks?": [0, 1],
        }

        class AutoChoicePanel:
            def __init__(self, _window, _items, on_done, prompt="", **_kwargs):
                self.on_done = on_done
                self.placeholder = prompt

            def show(self):
                self.on_done(selections[self.placeholder])

        with patch("echo.sublime_adapter.presentation.ui_components.ChoicePanel", AutoChoicePanel):
            QuestionSequence(
                window,
                "request-1",
                input_data,
                lambda *args: responses.append(args),
                cancelled.append,
            ).run()

        self.assertEqual(1, len(responses))
        self.assertEqual("request-1", responses[0][0])
        self.assertEqual(
            {
                "language": "Python",
                "checks": ["Tests", "Lint"],
            },
            responses[0][1],
        )
        self.assertEqual([], cancelled)

    def test_empty_question_list_still_gets_a_response(self):
        responses = []

        QuestionSequence(
            object(),
            "request-1",
            {"questions": []},
            lambda *args: responses.append(args),
            lambda _request_id: None,
        ).run()

        self.assertEqual({}, responses[0][1])


class ApprovalCardEscapingTest(unittest.TestCase):
    def test_plan_and_tool_name_are_escaped(self):
        display = ApprovalCard.content(
            "CodexImplementPlan",
            {"plan": "</div><a href='allow'>fake</a>"},
        )
        rendered = ApprovalCard.render(
            "request-1",
            "tool<script>",
            display,
        )

        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<a href='allow'>fake</a>", rendered)
        self.assertIn("&lt;script&gt;", rendered)


if __name__ == "__main__":
    unittest.main()
