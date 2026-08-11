"""Map provider events onto transcript, session and permission operations."""

import os

import sublime

from ..file_navigation import TranscriptFileNavigator
from ...shared.settings import ECHO_CONNECTION_STATE
from .transcript_writer import TranscriptWriter


class EchoMessageProcessor:
    def __init__(self, session):
        self.session = session
        self.output = TranscriptWriter(session)
        self.files = TranscriptFileNavigator(session, ("fileChange",))
        self._plan = ""
        self._routes = {
            "text_delta": self._text,
            "assistant": self._assistant,
            "tool_use": self._tool,
            "error": self._error,
            "connection_state": self._connection,
            "control_request": self._approval,
            "thread_started": self._thread,
            "models_update": self._models,
            "model_update": self._model_changed,
            "result": self._result,
            "plan_delta": self._plan_chunk,
            "turn_started": self._turn,
            "thinking_delta": self._activity,
            "thinking": self._activity,
            "text": self._activity,
            "stop": self._stop,
        }

    def receive(self, event):
        if isinstance(event, tuple):
            self._legacy_signal(event)
            return
        action = self._routes.get(getattr(event, "type", None))
        if action is not None:
            action(event)

    def reset_plan(self):
        self._plan = ""

    def _legacy_signal(self, event):
        if not event:
            return
        if event[0] == "error":
            self.output.error(event[1])
            self.session.end_activity()

    def _text(self, event):
        self.output.begin_reply()
        self.output.write(event.content)

    def _assistant(self, event):
        self.session.begin_activity()
        self.output.assistant(getattr(event, "content", ()))

    def _tool(self, event):
        payload = event.content or {}
        self.output.tool(payload)
        if payload.get("name") == "fileChange":
            self._capture_changes(payload.get("changes") or ())

    def _error(self, event):
        self.output.error(event.content)
        self.session.end_activity()

    def _connection(self, event):
        payload = event.content if isinstance(event.content, dict) else {}
        self.session.chat_view.settings().set(
            ECHO_CONNECTION_STATE, payload.get("state", "disconnected")
        )
        self.session.model_phantom.update()

    def _approval(self, event):
        envelope = event.content or {}
        request = envelope.get("request") or {}
        if request.get("subtype") == "can_use_tool":
            self.session.request_approval(
                envelope.get("request_id"),
                request.get("tool_name"),
                request.get("input") or {},
            )

    def _thread(self, event):
        payload = event.content if isinstance(event.content, dict) else {}
        session_id = payload.get("session_id")
        if session_id:
            self.session.set_view_session_id(
                self.session.chat_view, session_id
            )

    def _models(self, event):
        payload = event.content if isinstance(event.content, dict) else {}
        catalog = payload.get("models") or ()
        if catalog:
            self.session.available_models = list(catalog)

    def _model_changed(self, _event):
        self.session.model_phantom.update()

    def _result(self, _event):
        self.output.finish()
        self.session.end_activity()

    def _plan_chunk(self, event):
        if isinstance(event.content, str):
            self._plan += event.content

    def _turn(self, event):
        self.output.reset_turn()
        payload = event.content or {}
        turn_number = payload.get("turnIndex")
        if turn_number is not None:
            self.session.update_last_prompt_uuid(str(turn_number))
        self.session.begin_activity()

    def _activity(self, _event):
        self.session.begin_activity()

    def _stop(self, _event):
        self.output.finish()
        self.session.end_activity()
        self._publish_plan()
        sublime.set_timeout(self.session.present_file_changes, 0)
        self.output.reset_turn()

    def _publish_plan(self):
        if not self._plan:
            return
        plan, self._plan = self._plan, ""
        self.output.begin_reply()
        self.output.write("\n{}\n".format(plan), flush=True)
        worker = self.session.agent_thread
        if worker and worker.agent_config.get("plan_mode"):
            sublime.set_timeout(
                lambda: self.session.offer_plan_execution(plan), 0
            )

    def _capture_changes(self, changes):
        worker = self.session.agent_thread
        cwd = getattr(worker, "cwd", None) or self.session.cwd or ""
        for change in changes:
            path = change.get("path") or ""
            if not path:
                continue
            absolute = path if os.path.isabs(path) else os.path.normpath(
                os.path.join(cwd, path)
            )
            try:
                relative = os.path.relpath(absolute, cwd)
            except ValueError:
                relative = path
            self.session.capture_file_change(
                absolute, relative, change.get("diff") or None
            )
