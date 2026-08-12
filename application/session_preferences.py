"""Apply per-window conversation preferences to storage and live sessions."""

import sublime

from ..providers import normalize_provider_model
from ..shared.settings import ECHO_APPROVE_MODE, ECHO_MODEL, ECHO_PLAN_MODE
from ..sublime_adapter.presentation.ui_components import ApproveMode, PlanMode
from ..sublime_adapter.window_context import EchoWindowContext


class SessionPreferences:
    def __init__(self, window):
        self._window = window

    def select_model(self, requested):
        model = normalize_provider_model(requested)
        self._store(ECHO_MODEL, model, erase_empty=True)
        session = self._refresh_session()
        worker = session.agent_thread if session else None
        if worker:
            worker.reconfigure(model=model)
        sublime.status_message("Echo model: {}".format(model or "default"))

    def select_approval(self, requested):
        try:
            mode = ApproveMode(requested)
        except (TypeError, ValueError):
            sublime.status_message("Unknown approve mode: {}".format(requested))
            return False
        self._store(ECHO_APPROVE_MODE, mode.value)
        self._refresh_session()
        sublime.status_message("Approval policy: {}".format(mode.value))
        return True

    def select_plan(self, requested):
        mode = PlanMode.PLANNING \
            if requested == PlanMode.PLANNING.value else PlanMode.FAST
        self._store(ECHO_PLAN_MODE, mode.value)
        session = self._refresh_session()
        if session:
            session.apply_plan_mode(mode)
        label = "enabled" if mode is PlanMode.PLANNING else "disabled"
        sublime.status_message("Plan mode {}".format(label))

    def _store(self, key, value, erase_empty=False):
        settings = self._window.settings()
        if erase_empty and not value:
            settings.erase(key)
        else:
            settings.set(key, value)

    def _refresh_session(self):
        session = EchoWindowContext(self._window).session
        if session:
            session.model_phantom.update()
        return session
