"""Stateful transcript output and provider tool presentation."""

import os
import re

import sublime

from .md_render import MarkdownFormatter


_HUNK_START = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)", re.MULTILINE)


class ToolTranscript:
    def __init__(self, cwd_provider):
        self._cwd = cwd_provider

    def format(self, payload):
        kind = payload.get("name")
        if kind == "command_execution":
            return self._command(payload.get("command", ""))
        if kind == "fileChange":
            return self._changes(payload.get("changes") or ())
        return "⏺ {}".format(kind) if kind else ""

    @staticmethod
    def _command(command):
        lines = command.rstrip().splitlines()
        if not lines:
            return "⏺ command"
        heading = "⏺ command ({})".format(lines.pop(0))
        return heading if not lines else "{}\n\n    {}\n".format(
            heading, "\n    ".join(lines)
        )

    def _changes(self, changes):
        sections = []
        previous = None
        cwd = self._cwd() or ""
        for change in changes:
            path = change.get("path") or ""
            diff = (change.get("diff") or "").rstrip()
            identity = os.path.normcase(os.path.normpath(
                path if os.path.isabs(path) else os.path.join(cwd, path)
            )) if path else None
            if identity != previous:
                label = os.path.relpath(identity, cwd) if path else ""
                hunk = _HUNK_START.search(diff)
                if hunk:
                    label += "#L" + hunk.group(1)
                sections.append("⏺ fileChange" + (" " + label if label else ""))
            previous = identity
            if diff:
                sections.append("````diff\n{}\n````".format(diff))
        return "\n\n".join(sections) if sections else "⏺ fileChange"


class TranscriptWriter:
    def __init__(self, session):
        self._session = session
        self._markdown = MarkdownFormatter()
        self._reply_open = False
        self._last_was_tool = False
        self.tools = ToolTranscript(self._cwd)

    def reset_turn(self):
        self._reply_open = False

    def begin_reply(self):
        if not self._reply_open:
            self._reply_open = True
            self.write("\n●\n\n")

    def write(self, text, flush=False):
        rendered = self._markdown.format(text, flush=flush)
        if rendered:
            sublime.set_timeout(
                lambda: self._session.chat_view.run_command(
                    "echo_chat_output_append", {"text": rendered}
                ),
                0,
            )

    def error(self, detail):
        sublime.set_timeout(
            lambda: self._session.chat_view.run_command(
                "echo_chat_output_append",
                {"text": "\n\nError: {}\n".format(detail)},
            ),
            0,
        )

    def assistant(self, blocks):
        pieces = [block.text for block in blocks if hasattr(block, "text")]
        if not pieces:
            return
        self.begin_reply()
        prefix = "\n" if self._last_was_tool else ""
        self._last_was_tool = False
        self.write(prefix + "".join(pieces) + "\n")

    def tool(self, payload):
        self.begin_reply()
        if not self._last_was_tool:
            self.write("\n")
        self._last_was_tool = True
        self.write(self.tools.format(payload) + "\n")

    def finish(self):
        self.write("", flush=True)
        self.write("\n")

    def _cwd(self):
        worker = getattr(self._session, "agent_thread", None)
        return getattr(worker, "cwd", None) or getattr(self._session, "cwd", "")
