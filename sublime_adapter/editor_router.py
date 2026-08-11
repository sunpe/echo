"""Translate Sublime editor callbacks into Echo navigation actions."""

import sublime

from .presentation.artifact import DIFF_VIEW_PATH_KEY, diff_view_click
from .completions import build_file_completions
from .editor_events import click_point, history_move, is_plain_word_click
from .editor_policy import (
    clamp_carets,
    deletion_crosses_boundary,
    mutation_starts_in_history,
)
from .prompt_editor import PromptEditor
from ..shared.settings import ECHO_VIEW_FLAG
from .presentation.ui_components import CHAT_INPUT_START, input_editable_start
from .window_context import EchoWindowContext


class EditorRouter:
    def loaded(self, view, reconnect):
        window = view.window()
        if window is None:
            return
        context = EchoWindowContext(window)
        if view.settings().get(ECHO_VIEW_FLAG, False):
            if context.session is None:
                sublime.set_timeout(lambda: reconnect(view), 100)
            return
        if not sublime.load_settings("echo.sublime-settings").get(
            "dedicated_chat_pane", True
        ):
            return
        if context.session is None or window.num_groups() < 2:
            return
        if view.settings().get("is_widget"):
            return
        occupied_group, _ = window.get_view_index(view)
        if occupied_group < 0 or not any(
            candidate.settings().get(ECHO_VIEW_FLAG, False)
            for candidate in window.views_in_group(occupied_group)
        ):
            return
        destination = next(
            (group for group in range(window.num_groups()) if group != occupied_group),
            None,
        )
        if destination is not None:
            window.set_view_index(view, destination, 0)
            window.focus_view(view)

    @staticmethod
    def closed(view):
        if view.name() != "Echo":
            return None
        window = view.window() or sublime.active_window()
        if window is None:
            return None
        return EchoWindowContext(window).release(stop=True)

    @staticmethod
    def selection_changed(view):
        if not EditorRouter._is_chat(view) or not view.settings().has(
            CHAT_INPUT_START
        ):
            return
        changed, selections = clamp_carets(
            view.sel(), input_editable_start(view), sublime.Region
        )
        if changed:
            view.sel().clear()
            view.sel().add_all(selections)

    def before_text_command(self, view, command, arguments):
        point = click_point(view, arguments) \
            if is_plain_word_click(command, arguments) else None
        diff_path = view.settings().get(DIFF_VIEW_PATH_KEY)
        if point is not None and diff_path and diff_view_click(
            view, diff_path, point
        ):
            return "noop", {}
        if not self._is_chat(view):
            return None
        if point is not None and self._open_target(view, point):
            return "noop", {}

        boundary = input_editable_start(view)
        history_command = history_move(
            view, command, arguments, boundary, sublime.Region
        )
        if history_command:
            return history_command, {}
        blocked = deletion_crosses_boundary(command, view.sel(), boundary) \
            or mutation_starts_in_history(command, view.sel(), boundary)
        if blocked:
            PromptEditor(view).move_to_end()
            return "noop", {}
        return None

    @staticmethod
    def completions(view, prefix, locations):
        if not view.settings().get(ECHO_VIEW_FLAG, False) or not locations:
            return None
        position = locations[0]
        trigger = position - len(prefix) - 1
        if position < input_editable_start(view) or trigger < 0:
            return None
        if view.substr(trigger) != "@" or view.window() is None:
            return None
        return sublime.CompletionList(
            build_file_completions(view.window(), ECHO_VIEW_FLAG),
            flags=sublime.INHIBIT_WORD_COMPLETIONS,
        )

    @staticmethod
    def hover(view, point, zone):
        if zone != sublime.HOVER_GUTTER or not view.settings().get(
            ECHO_VIEW_FLAG, False
        ):
            return
        window = view.window()
        session = EchoWindowContext(window).session if window else None
        if session is None or session.rewind_confirm_panel.visible:
            return
        row = view.rowcol(point)[0]
        for index, (region, message_id, _phantom) in enumerate(
            session.prompt_regions
        ):
            first = view.rowcol(region.begin())[0]
            last = view.rowcol(region.end())[0]
            if first <= row <= last and message_id:
                session.rewind_confirm_panel.show(
                    region,
                    lambda value=index: session.rewind.request(value),
                )
                return

    @staticmethod
    def modified(view):
        if not view.settings().get(ECHO_VIEW_FLAG, False) or not view.sel():
            return
        position = view.sel()[0].begin()
        if position > input_editable_start(view) and view.substr(position - 1) == "@":
            view.run_command("auto_complete", {
                "disable_auto_insert": True,
                "api_completions_only": True,
                "next_completion_if_showing": False,
            })

    @staticmethod
    def _is_chat(view):
        return view.settings().get(ECHO_VIEW_FLAG, False) or view.name() == "Echo"

    @staticmethod
    def _open_target(view, point):
        window = view.window()
        session = EchoWindowContext(window).session if window else None
        if session is None:
            return False
        line = view.substr(view.line(point))
        navigator = session.message_processor.files
        actions = (
            lambda: navigator.open_tool_target(
                line, window, view=view, point=point
            ),
            lambda: navigator.open_markdown_target(
                line, window, view, point
            ),
            lambda: session.open_change_at(point),
        )
        return any(action() for action in actions)
