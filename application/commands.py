"""Sublime commands and event listeners for the chat view."""

import logging
import os
import threading

import sublime
import sublime_plugin

from ..providers import (
    ProviderConfigurationError,
    list_provider_sessions,
    normalize_provider_model,
    provider_identity,
    resolve_provider,
)
from ..workspace.context_composer import path_context
from ..sublime_adapter.editor_router import EditorRouter
from ..sublime_adapter.layout import place_in_dedicated_pane
from ..sublime_adapter.prompt_editor import PromptEditor
from ..sublime_adapter.input_area import DEFAULT_LEADING_NEWLINES
from ..runtime.session_registry import filter_registered_sessions, registered_session_ids
from ..shared.settings import ECHO_VIEW_FLAG, ECHO_WORKSPACE
from ..runtime.session_store import echo_clients
from ..sublime_adapter.presentation.ui_components import (
    ApproveMode,
    CHAT_APPROVE_MODE,
    CHAT_MODEL,
    CHAT_PLAN_MODE,
    PlanMode,
)
from ..sublime_adapter.view_service import ChatViewService
from ..workspace.project_paths import get_best_dir
from ..sublime_adapter.window_context import EchoWindowContext


LOG = logging.getLogger("echo")
PACKAGE_NAME = "echo"
CHAT_VIEW_FLAG = ECHO_VIEW_FLAG
CHAT_WORKSPACE = ECHO_WORKSPACE


def _workspace_candidate(path):
    expanded = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(expanded):
        return expanded
    parent = os.path.dirname(expanded)
    return parent if os.path.isfile(expanded) and os.path.isdir(parent) else None


def _reconnect_chat_view(view):
    window = view.window()
    return ChatViewService(window).reconnect(view) if window else None


def _session_for(window):
    return EchoWindowContext(window).session


def _view_from_command_target(window, group, index):
    try:
        group_number = int(group)
        view_number = int(index)
    except (TypeError, ValueError):
        return None
    if group_number < 0 or view_number < 0:
        return None
    group_views = window.views_in_group(group_number)
    return group_views[view_number] if view_number < len(group_views) else None


def _mode_input(args, window, setting, default, handler):
    if "mode" in args:
        return None
    return handler(window.settings().get(setting, default))

class EchoChatCliCommand(sublime_plugin.WindowCommand):
    def run(self, initial_msg=""):
        ChatViewService(self.window).open(initial_msg)


class EchoChatSplitChatCommand(sublime_plugin.WindowCommand):
    """Place Echo in a dedicated right-hand group."""
    def is_visible(self, group=-1, index=-1):
        target = _view_from_command_target(self.window, group, index)
        if target is not None:
            return bool(target.settings().get(CHAT_VIEW_FLAG, False))
        return EchoWindowContext(self.window).echo_view() is not None

    def run(self, group=-1, index=-1):
        echo_view = EchoWindowContext(self.window).echo_view()
        if echo_view is None:
            sublime.status_message("No active Echo view to split")
            return
        place_in_dedicated_pane(self.window, echo_view)


class EchoChatSendInputCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        ChatViewService.submit(self.view, edit)


class EchoChatHistoryUpCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        window = self.view.window()
        if not window or window.id() not in echo_clients:
            return

        session = echo_clients[window.id()]
        prompt = PromptEditor(self.view)

        value = session.prompt_history.older(prompt.text())
        if value is not None:
            prompt.replace(edit, value)


class EchoChatHistoryDownCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        window = self.view.window()
        if not window or window.id() not in echo_clients:
            return

        session = echo_clients[window.id()]

        value = session.prompt_history.newer()
        if value is not None:
            PromptEditor(self.view).replace(edit, value)



class EchoListener(sublime_plugin.EventListener):
    _router = EditorRouter()

    def on_load(self, view):
        self._router.loaded(view, _reconnect_chat_view)

    def on_close(self, view):
        try:
            stopped = self._router.closed(view)
        except Exception:
            LOG.exception("Unable to close Echo session")
            return
        if stopped is not None:
            LOG.info("Echo session closed")

    def on_selection_modified(self, view):
        self._router.selection_changed(view)

    def on_text_command(self, view, command_name, args):
        return self._router.before_text_command(view, command_name, args)

    def on_query_completions(self, view, prefix, locations):
        return self._router.completions(view, prefix, locations)

    def on_hover(self, view, point, hover_zone):
        self._router.hover(view, point, hover_zone)

    def on_modified_async(self, view):
        self._router.modified(view)


class EchoChatRewindTruncateCommand(sublime_plugin.TextCommand):
    """
    Erase everything from cut_point to end of the view, then set up a
    fresh prompt area. rewind_text, when provided, is placed in the prompt
    area (the original user input at the rewind point); otherwise the current
    in-progress input is preserved.
    """
    def run(self, edit, cut_point, rewind_text=None):
        prompt = PromptEditor(self.view)
        if rewind_text is None:
            rewind_text = prompt.text(strip=True)

        if cut_point < self.view.size():
            self.view.erase(edit, sublime.Region(cut_point, self.view.size()))

        prompt.create_area(edit, 2, rewind_text or "")

        window = self.view.window()
        if window and window.id() in echo_clients:
            echo_clients[window.id()].model_phantom.update()

        prompt.move_to_end()

        if window and window.id() in echo_clients:
            session = echo_clients[window.id()]
            session.input_marker.update()


class EchoChatOutputAppendCommand(sublime_plugin.TextCommand):

    def run(self, edit, text):
        PromptEditor(self.view).append_output(edit, text)


class EchoChatInputPromptCommand(sublime_plugin.TextCommand):

    def run(self, edit, text):
        prompt = PromptEditor(self.view)
        prompt.trim_reserved_lines(edit)
        prompt.create_area(edit, DEFAULT_LEADING_NEWLINES, text)

        # Update model phantom at new position
        window = self.view.window()
        if window and window.id() in echo_clients:
            session = echo_clients[window.id()]
            session.model_phantom.update()

        # The prompt glyph itself is a phantom and remains outside the buffer.
        prompt.move_to_end()

        if window and window.id() in echo_clients:
            session = echo_clients[window.id()]
            session.input_marker.update()


class EchoChatAddContextCommand(sublime_plugin.WindowCommand):
    """Insert side-bar file and directory references into an Echo prompt."""
    def run(self, files=[], dirs=[]):
        draft = path_context(list(files) + list(dirs))
        if draft.empty:
            return
        destination = EchoWindowContext(self.window).echo_view()
        if destination is None:
            self.window.run_command(
                "echo_chat_cli", {"initial_msg": draft.initial_message}
            )
            return
        self.window.focus_view(destination)
        PromptEditor(destination).insert(draft.insertion)


class EchoChatPromptHandler(sublime_plugin.TextInputHandler):
    def name(self):
        return "prompt"

    def placeholder(self):
        return "Enter your prompt for Echo..."

    def description(self, text):
        return "{}: {}".format(PACKAGE_NAME, text) \
            if text else "{} prompt".format(PACKAGE_NAME)


class EchoChatPromptCommand(sublime_plugin.WindowCommand):
    def run(self, prompt):
        if not prompt:
            return
        transcript = ChatViewService(self.window).open(prompt)
        transcript.run_command("echo_chat_send_input")

    def input(self, args):
        return EchoChatPromptHandler()


class EchoChatSetWorkspaceInputHandler(sublime_plugin.TextInputHandler):
    def name(self):
        return "path"

    def placeholder(self):
        return "Enter workspace path..."

    def description(self, text):
        return "Workspace: {}".format(text) \
            if text else "Choose a workspace directory"

    def validate(self, text):
        candidate = os.path.abspath(os.path.expanduser(text.strip()))
        return bool(text.strip()) and os.path.isdir(candidate)


class EchoChatSetWorkspaceInputCommand(sublime_plugin.WindowCommand):
    """Ask for a working directory, then use the shared workspace command."""
    def run(self, path):
        if path:
            self.window.run_command(
                "echo_chat_set_workspace", {"dirs": [os.path.expanduser(path)]}
            )

    def input(self, args):
        return EchoChatSetWorkspaceInputHandler()


class EchoChatSetWorkspaceCommand(sublime_plugin.WindowCommand):
    def run(self, files=[], dirs=[]):
        target = next(
            (
                resolved
                for resolved in map(_workspace_candidate, list(files) + list(dirs))
                if resolved
            ),
            None,
        )
        if target is None:
            sublime.status_message("No usable Echo workspace in selection")
            return
        self.window.settings().set(CHAT_WORKSPACE, target)
        sublime.status_message("Echo workspace: {}".format(target))

    def is_visible(self, files=[], dirs=[]):
        return any((files, dirs))


class EchoChatClearSessionCommand(sublime_plugin.WindowCommand):
    """
    Clears the current chat session by disconnecting and reconnecting the agent.
    This starts a fresh conversation for the active provider.
    """
    def run(self):
        context = EchoWindowContext(self.window)
        session = context.session
        if session is None:
            sublime.status_message("No active Echo session found")
            return

        session.reset_conversation()
        sublime.status_message("Resetting chat session...")
        LOG.info("Resetting chat session via disconnect/reconnect")

    def is_enabled(self):
        return EchoWindowContext(self.window).session is not None


class EchoChatResumeSessionCommand(sublime_plugin.WindowCommand):
    """
    Shows a quick-panel listing Codex sessions for the active connection.
    Works whether or not Echo is already open: when none exists, opening
    the selected session creates one first.
    """

    _PREVIEW_LEN = 80

    def _get_cwd(self, session):
        view = getattr(session, "chat_view", None)
        if view is not None:
            return get_best_dir(view)
        configured = self.window.settings().get(CHAT_WORKSPACE)
        candidates = [configured] + list(self.window.folders())
        return next(
            (path for path in candidates if path and os.path.isdir(path)),
            "",
        )

    def run(self):
        context = EchoWindowContext(self.window)
        window_id = context.key
        session = context.session
        cwd = self._get_cwd(session)
        settings = sublime.load_settings(f"{PACKAGE_NAME}.sublime-settings")
        try:
            provider = resolve_provider(settings)
        except ProviderConfigurationError as exc:
            sublime.status_message(str(exc))
            return
        if not provider.capabilities.resume:
            sublime.status_message(
                "{} does not support listing resumable sessions".format(
                    provider.name
                )
            )
            return
        roots = [cwd] + (
            self.window.folders()
            if settings.get("share_workspace_folders", False)
            else []
        )
        known_session_ids = registered_session_ids(
            provider.name, provider_identity(provider), roots
        )
        sublime.status_message("Loading Codex sessions…")

        def load():
            try:
                raw = list_provider_sessions(provider)
            except Exception as exc:
                LOG.warning("Unable to list app-server sessions: %s", exc)
                sublime.set_timeout(
                    lambda detail=str(exc): sublime.status_message(
                        "Unable to load Codex sessions: " + detail
                    ),
                    0,
                )
                return
            raw = filter_registered_sessions(raw, known_session_ids)
            placeholder = "Resume Codex app-server session"
            sessions = [
                {
                    "session_id": item["session_id"],
                    "summary": item["summary"],
                    "mtime": item["updated_at"],
                }
                for item in raw
            ]
            sublime.set_timeout(
                lambda: self._show_sessions(
                    window_id, session, sessions, placeholder
                ),
                0,
            )

        threading.Thread(target=load, daemon=True).start()

    def _show_sessions(self, window_id, session, sessions, placeholder):
        import datetime
        if self.window.id() != window_id:
            return
        if not sessions:
            sublime.status_message("No past sessions found for this workspace")
            return
        current_session_id = (
            session.agent_thread.session_id
            if session and session.agent_thread
            else None
        )
        items = []
        for value in sessions:
            session_id = value["session_id"]
            summary = value["summary"] or "(empty)"
            if len(summary) > self._PREVIEW_LEN:
                summary = summary[:self._PREVIEW_LEN] + "…"
            timestamp = datetime.datetime.fromtimestamp(
                value["mtime"]
            ).strftime("%Y-%m-%d %H:%M")
            marker = " ●" if session_id == current_session_id else ""
            items.append([
                "{}{}".format(summary, marker),
                "{}  {}".format(session_id[:8], timestamp),
            ])

        def on_select(index):
            if index < 0:
                return
            chosen_id = sessions[index]["session_id"]
            if chosen_id == current_session_id:
                sublime.status_message("Already on that session")
                return
            active = EchoWindowContext(self.window).session
            if active is not None:
                active.mark_conversation_started()
                active.set_view_session_id(active.chat_view, chosen_id)
                active.restart_provider(
                    session_id_override=chosen_id, quiet=False
                )
                return
            self.window.run_command("echo_chat_cli")

            def resume_after_open():
                new_session = EchoWindowContext(self.window).session
                if new_session is None:
                    LOG.warning("Chat view was not ready for session resume")
                    return
                new_session.mark_conversation_started()
                new_session.set_view_session_id(
                    new_session.chat_view, chosen_id
                )
                new_session.restart_provider(
                    session_id_override=chosen_id, quiet=False
                )

            sublime.set_timeout(resume_after_open, 0)

        self.window.show_quick_panel(
            items, on_select, placeholder=placeholder
        )

    def is_enabled(self):
        return True


class EchoChatInterruptCommand(sublime_plugin.WindowCommand):
    def run(self, confirm=False):
        context = EchoWindowContext(self.window)
        session = context.session
        if session is None:
            sublime.status_message("No active Echo session found")
            return

        if not session.loading_animation.is_loading:
            sublime.status_message("No active conversation to interrupt")
            return

        if confirm and not sublime.ok_cancel_dialog("Stop the running conversation?", "Stop"):
            return

        worker = session.agent_thread
        if worker is None or not worker.cancel_turn():
            sublime.status_message("Echo agent is not ready to interrupt")
            return
        session.model_phantom.set_stopping(True)
        sublime.set_timeout(
            lambda: session.chat_view.run_command(
                "echo_chat_output_append",
                {"text": "\n■ Conversation interrupted\n"},
            ),
            1000,
        )
        sublime.status_message("Interrupting agent")
        LOG.info("Interrupt requested for window %s", context.key)

    def is_enabled(self):
        return EchoWindowContext(self.window).session is not None


class EchoChatSetModelListHandler(sublime_plugin.ListInputHandler):
    def __init__(self, current_model=None):
        self.current_model = current_model or ""

    def name(self):
        return "model"

    def list_items(self):
        window = sublime.active_window()
        session = echo_clients.get(window.id()) if window else None
        models = session.available_models if session else ()

        def model_item(model):
            return sublime.ListInputItem(
                model["displayName"], model["value"],
                details=model["description"], annotation=model["value"],
            )

        selected = [model for model in models
                    if model["value"] == self.current_model]
        remaining = [model for model in models
                     if model["value"] != self.current_model]
        return [model_item(model) for model in selected + remaining]

    def placeholder(self):
        return "Select a model"

    def description(self, value, text):
        return "Use model {}".format(value)


class EchoChatSetModelTextHandler(sublime_plugin.TextInputHandler):
    def name(self):
        return "model"

    def placeholder(self):
        return "Enter a model id"

    def description(self, text):
        return "Use model {}".format(text) if text else "Enter model id"

    def validate(self, text):
        return len(text.strip()) > 0


class EchoChatSetModelCommand(sublime_plugin.WindowCommand):
    """
    Sets the model for Echo sessions in the current window.
    """
    def run(self, model):
        model = normalize_provider_model(model)
        if model:
            self.window.settings().set(CHAT_MODEL, model)
        else:
            self.window.settings().erase(CHAT_MODEL)
        sublime.status_message(
            f"{PACKAGE_NAME} model set to: {model or 'default'}"
        )

        # Update the model phantom if session exists
        session = EchoWindowContext(self.window).session
        if session is not None:
            session.model_phantom.update()
            # Update the running agent directly
            if session.agent_thread:
                session.agent_thread.reconfigure(model=model)

    def input(self, args):
        session = _session_for(self.window)
        if session is not None and session.available_models:
            return EchoChatSetModelListHandler(
                self.window.settings().get(CHAT_MODEL)
            )
        return EchoChatSetModelTextHandler()


class _ModeChoiceHandler(sublime_plugin.ListInputHandler):
    choices = ()
    prompt = "Select mode"

    def __init__(self, current_mode=None):
        self.current_mode = current_mode or ""

    def name(self):
        return "mode"

    def list_items(self):
        return sorted(
            self.choices,
            key=lambda item: item[1] != self.current_mode,
        )

    def placeholder(self):
        return self.prompt


class EchoChatPlanModeInputHandler(_ModeChoiceHandler):
    choices = (
        ("off: execute the task directly", PlanMode.FAST.value),
        ("on: create a plan before execution", PlanMode.PLANNING.value),
    )

    def description(self, mode, text):
        return "Plan mode will be {}".format(mode)


class EchoChatApproveModeInputHandler(_ModeChoiceHandler):
    choices = (
        ("default: ask before each tool", ApproveMode.DEFAULT.value),
        ("allow-edit: approve file edits", ApproveMode.ALLOW_EDIT.value),
        ("accept-all: approve this chat", ApproveMode.ACCEPT_ALL.value),
    )
    prompt = "Select tool approval policy"

    def placeholder(self):
        return "Current: {} — {}".format(
            self.current_mode or "allow-edit", self.prompt
        )


class EchoChatSetApproveModeCommand(sublime_plugin.WindowCommand):
    """Set permission approve mode for the current Echo session."""
    def run(self, mode):
        try:
            approve_mode = ApproveMode(mode)
        except (TypeError, ValueError):
            sublime.status_message("Unknown approve mode: {}".format(mode))
            return

        self.window.settings().set(CHAT_APPROVE_MODE, approve_mode.value)
        sublime.status_message(
            "Approve mode set to: {}".format(approve_mode.value)
        )

        session = EchoWindowContext(self.window).session
        if session is not None:
            session.model_phantom.update()

    def input(self, args):
        return _mode_input(
            args, self.window, CHAT_APPROVE_MODE,
            ApproveMode.ALLOW_EDIT.value, EchoChatApproveModeInputHandler,
        )


class EchoChatTogglePlanModeCommand(sublime_plugin.WindowCommand):
    """
    Toggle plan mode for the current Echo session.
    """
    def run(self, mode):
        plan_mode_enum = (
            PlanMode.PLANNING
            if mode == PlanMode.PLANNING.value else PlanMode.FAST
        )

        self.window.settings().set(CHAT_PLAN_MODE, plan_mode_enum.value)

        session = EchoWindowContext(self.window).session
        if session is not None:
            session.model_phantom.update()
            # Codex supports changing plan mode without reconnecting.
            session.apply_plan_mode(plan_mode_enum)

        status = "enabled" if plan_mode_enum == PlanMode.PLANNING else "disabled"
        sublime.status_message(f"Plan mode {status}")

    def input(self, args):
        return _mode_input(
            args, self.window, CHAT_PLAN_MODE,
            PlanMode.FAST.value, EchoChatPlanModeInputHandler,
        )


class EchoChatImplementPlanCommand(sublime_plugin.WindowCommand):
    """
    Trigger the 'Implement the plan.' steering message.
    """
    def run(self):
        session = _session_for(self.window)
        if session is None:
            return
        sublime.status_message("Implementing plan...")
        session.implement_plan()
        self.window.run_command(
            "echo_chat_toggle_plan_mode", {"mode": PlanMode.FAST.value}
        )
