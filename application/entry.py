"""Sublime package lifecycle and command discovery for Echo."""

import importlib
import logging

import sublime

from ..sublime_adapter.presentation.chat_view import ChatSession
from ..shared.settings import ECHO_VIEW_FLAG
from ..runtime.session_store import echo_clients, register_chat_session_type


LOG = logging.getLogger("echo")
_PREFERENCES_KEY = "echo.ui.refresh"

# Command classes are discovered from imported modules by echo.export_plugin_types.
for _command_module in (".commands", ".settings_commands"):
    importlib.import_module(_command_module, __package__)


class _PackageLifecycle:
    def __init__(self):
        self._refresh_pending = False

    def start(self):
        register_chat_session_type(ChatSession)
        package_settings = sublime.load_settings("echo.sublime-settings")
        from ..shared.logging import update_log_level
        update_log_level(package_settings)

        preferences = sublime.load_settings("Preferences.sublime-settings")
        preferences.clear_on_change(_PREFERENCES_KEY)
        preferences.add_on_change(_PREFERENCES_KEY, self.schedule_refresh)
        sublime.set_timeout(self.restore_views, 500)
        LOG.info("Echo package ready")

    def stop(self):
        sublime.load_settings("Preferences.sublime-settings").clear_on_change(
            _PREFERENCES_KEY
        )
        for window_id, session in tuple(echo_clients.items()):
            try:
                session.stop()
            except Exception:
                LOG.exception("Unable to stop Echo session for window %s", window_id)
        echo_clients.clear()

    def schedule_refresh(self):
        if self._refresh_pending:
            return
        self._refresh_pending = True
        sublime.set_timeout(self.refresh_controls, 500)

    def refresh_controls(self):
        try:
            for session in tuple(echo_clients.values()):
                session.model_phantom.update()
                session.input_marker.update()
        finally:
            self._refresh_pending = False

    @staticmethod
    def restore_views():
        from .commands import _reconnect_chat_view

        attached_windows = set(echo_clients)
        for window in sublime.windows():
            if window.id() in attached_windows:
                continue
            orphan = next(
                (
                    view for view in window.views()
                    if view.settings().get(ECHO_VIEW_FLAG, False)
                ),
                None,
            )
            if orphan is not None:
                _reconnect_chat_view(orphan)


_lifecycle = _PackageLifecycle()


def plugin_loaded():
    _lifecycle.start()


def plugin_unloaded():
    _lifecycle.stop()
