"""Read provider history off-thread and paint a compact resume summary."""

import datetime
import logging
import threading

import sublime

from ...providers import get_provider_session_info
from .ui_components import get_input_start


LOG = logging.getLogger("echo")


class ResumePresenter:
    def __init__(self, session, prompt_prefix):
        self._session = session
        self._prompt_prefix = prompt_prefix

    def schedule(self, provider, session_id):
        threading.Thread(
            target=self._load,
            args=(provider, session_id),
            daemon=True,
        ).start()

    def _load(self, provider, session_id):
        try:
            snapshot = get_provider_session_info(provider, session_id)
        except Exception as error:
            LOG.warning("Unable to read provider session: %s", error)
            sublime.set_timeout(
                lambda error=error: sublime.status_message(
                    "Unable to read session: {}".format(error)
                ),
                0,
            )
            return
        sublime.set_timeout(
            lambda: self._paint(provider.name, session_id, snapshot or {}),
            0,
        )

    def _paint(self, provider_name, session_id, snapshot):
        view = self._session.chat_view
        append = lambda text: view.run_command(
            "echo_chat_output_append", {"text": text}
        )
        append("\n[Resuming session for {}]\n\n".format(provider_name))
        prompt = snapshot.get("prompt")
        if prompt:
            self._paint_prompt(prompt)
            append("\n")
        response = snapshot.get("response")
        if response:
            append(response + "\n")
        timestamp = snapshot.get("updated_at") or snapshot.get("mtime")
        suffix = ""
        if timestamp:
            suffix = " : " + datetime.datetime.fromtimestamp(timestamp).strftime(
                "%Y-%m-%d %H:%M"
            )
        append("\n■ ResumeConversation ({}{})\n\n".format(
            session_id[:8], suffix
        ))

    def _paint_prompt(self, prompt):
        session = self._session
        before = get_input_start(session.chat_view, 0) - 1
        session.chat_view.run_command(
            "echo_chat_output_append",
            {"text": "{}{}\n".format(self._prompt_prefix, prompt)},
        )
        after = get_input_start(session.chat_view, 0) - 1
        start = before + len(self._prompt_prefix)
        if after - 1 > start:
            session.add_prompt_highlight(sublime.Region(start, after - 1))
