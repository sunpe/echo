"""Conversation actions independent of Sublime command registration."""

import logging

import sublime

from ...workspace.local_references import normalize_local_references
from .ui_components import PlanMode, get_input_start


LOG = logging.getLogger("echo")


class ConversationController:
    def __init__(self, session):
        self._session = session

    def submit(self, text, region=None):
        session = self._session
        session.rewind_confirm_panel.clear()
        worker = session.agent_thread
        if worker is None:
            self._report_unavailable()
            return
        if region is not None:
            session.add_prompt_highlight(region)
        session.message_processor.reset_plan()
        session.mark_conversation_started()
        if worker.session_id:
            session.set_view_session_id(session.chat_view, worker.session_id)
        roots = [session.cwd] + list(session.add_dirs)
        message = normalize_local_references(text, roots)
        if not worker.enqueue(message):
            self._report_closed()

    def steer(self, text, proceed_plan=False):
        worker = self._session.agent_thread
        return bool(worker and worker.steer(
            text, proceed_plan=proceed_plan
        ))

    def implement_plan(self):
        session = self._session
        boundary = get_input_start(session.chat_view, 0)
        session.chat_view.run_command(
            "echo_chat_output_append", {"text": "\nimplement the plan\n\n"}
        )
        session.add_prompt_highlight(sublime.Region(boundary, boundary))
        self.steer("Implement the plan.", proceed_plan=True)

    def record_change(self, absolute, relative, diff_text):
        session = self._session
        worker = session.agent_thread
        environment = worker.agent_config.get("env") if worker else None
        session.artifact.record(
            absolute, relative, diff_text, extra_env=environment
        )

    def set_plan_mode(self, mode):
        self._session.end_activity()
        worker = self._session.agent_thread
        if worker:
            worker.reconfigure(plan_mode=mode is PlanMode.PLANNING)
        LOG.info("Updated plan mode to %s", mode.value)

    def _report_unavailable(self):
        session = self._session
        provider = getattr(session, "provider", None)
        label = getattr(provider, "name", "Agent")
        self._append_error("{} is unavailable.".format(label))

    def _report_closed(self):
        self._append_error(
            "Agent connection is no longer active. Restart or reconnect the chat session."
        )

    def _append_error(self, detail):
        session = self._session
        session.chat_view.run_command(
            "echo_chat_output_append",
            {"text": "\n\n⚠️ Error: {}\n\n".format(detail)},
        )
        session.end_activity()
