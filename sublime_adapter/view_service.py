"""Creation and restoration of Echo transcript views."""

import logging

import sublime

from .prompt_editor import PromptEditor
from .layout import place_in_dedicated_pane
from ..shared.settings import ECHO_VIEW_FLAG
from ..runtime.session_store import chat_session_type, create_chat_session, echo_clients
from .presentation.ui_components import set_input_start
from .window_context import EchoWindowContext
from ..workspace.project_paths import get_all_folders, get_best_dir


LOG = logging.getLogger("echo")


class ChatViewService:
    def __init__(self, window):
        self.window = window
        self.context = EchoWindowContext(window)

    def reconnect(self, view):
        if self.context.session is not None:
            return self.context.session
        self._present(view)
        roots = self._shared_roots(view)
        session = create_chat_session(
            self.window,
            view,
            get_best_dir(view),
            add_dirs=roots,
            session_id=chat_session_type().get_view_session_id(view),
        )
        self.context.bind(session)
        view.settings().set("draw_unicode_white_space", "none")
        session.model_phantom.update()
        sublime.status_message("Echo reconnected")
        return session

    def open(self, initial_text=""):
        existing = self.context.echo_view()
        if existing is not None:
            self._present(existing)
            if self.context.session is None:
                self.reconnect(existing)
            if initial_text:
                existing.run_command(
                    "echo_chat_input_prompt", {"text": initial_text}
                )
            return existing

        transcript = self.window.new_file()
        self._configure(transcript)
        self._present(transcript)
        cwd = get_best_dir(transcript)
        set_input_start(transcript, transcript.size())
        session = create_chat_session(
            self.window,
            transcript,
            cwd,
            add_dirs=self._shared_roots(transcript),
        )
        self.context.bind(session)
        transcript.run_command(
            "echo_chat_input_prompt", {"text": initial_text}
        )
        return transcript

    def _present(self, view):
        settings = sublime.load_settings("echo.sublime-settings")
        if settings.get("dedicated_chat_pane", True):
            place_in_dedicated_pane(self.window, view)
        else:
            self.window.focus_view(view)

    @staticmethod
    def submit(view, edit):
        window = view.window()
        session = echo_clients.get(window.id()) if window is not None else None
        if session is None:
            sublime.status_message("No active Echo session found")
            return False
        editor = PromptEditor(view)
        text = editor.text(strip=True)
        if not text:
            return False
        stop_key = "Cmd+Esc" if sublime.platform() == "osx" else "Shift+Esc"
        sublime.status_message("Sending... ({} to stop)".format(stop_key))
        transcript_region = editor.materialize_prompt(edit)
        view.run_command("echo_chat_input_prompt", {"text": ""})
        session.prompt_history.record(text)
        session.send_input(text, region=transcript_region)
        LOG.info("Submitted Echo prompt (%d characters)", len(text))
        return True

    @staticmethod
    def _configure(view):
        view.set_name("Echo")
        view.set_scratch(True)
        view.set_syntax_file("Packages/echo/chat_md.sublime-syntax")
        settings = view.settings()
        for key, value in {
            "draw_minimap": False,
            "line_numbers": False,
            "word_wrap": True,
            "fold_buttons": True,
            "draw_unicode_white_space": "none",
            ECHO_VIEW_FLAG: True,
        }.items():
            settings.set(key, value)

    @staticmethod
    def _shared_roots(view):
        settings = sublime.load_settings("echo.sublime-settings")
        return get_all_folders(view) if settings.get(
            "share_workspace_folders", False
        ) else []
