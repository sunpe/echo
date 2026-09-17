"""Stateful transcript output and provider tool presentation."""

import os
import re
from functools import partial

import sublime

from .md_render import MarkdownFormatter
from .ui_components import get_input_start


_HUNK_START = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)", re.MULTILINE)


class TranscriptSurface:
    """Serialize transcript mutations onto Sublime's UI thread."""

    def __init__(self, view):
        self._view = view

    def append(self, text, on_commit=None):
        if text:
            sublime.set_timeout(partial(self._commit, text, on_commit), 0)

    def _commit(self, text, on_commit=None):
        start = get_input_start(self._view, 0) - 1
        command, arguments = "echo_chat_output_append", {"text": text}
        self._view.run_command(command, arguments)
        if on_commit is not None:
            on_commit(start, start + len(text))

    def fold(self, start, end):
        self._view.fold(sublime.Region(start, end))


class ReasoningTranscript:
    """Stream reasoning summaries and fold each completed summary body."""

    def __init__(self, surface):
        self._surface = surface
        self._items = {}

    def delta(self, item_id, text):
        if not text:
            return
        state = self._state(item_id)
        self._open(state)
        state["text"] += text
        self._surface.append(text)

    def complete(self, item_id, text):
        state = self._state(item_id)
        if state["finished"]:
            return
        text = text or ""
        if not state["text"] and text:
            self._open(state)
            state["text"] = text
            self._surface.append(text)
        elif text.startswith(state["text"]):
            suffix = text[len(state["text"]):]
            if suffix:
                state["text"] += suffix
                self._surface.append(suffix)
        self._finish(state)

    def finish_all(self):
        for state in self._items.values():
            self._finish(state)

    def reset(self):
        self._items = {}

    def _state(self, item_id):
        key = item_id or "active"
        return self._items.setdefault(key, {
            "text": "",
            "body_start": None,
            "opened": False,
            "finished": False,
        })

    def _open(self, state):
        if state["opened"]:
            return
        state["opened"] = True

        def remember_body_start(_start, end):
            state["body_start"] = end

        self._surface.append("\n◇ 思考摘要\n\n", remember_body_start)

    def _finish(self, state):
        if not state["opened"] or state["finished"]:
            return
        state["finished"] = True

        def fold_body(start, _end):
            body_start = state["body_start"]
            if body_start is not None and start > body_start:
                self._surface.fold(body_start, start)

        self._surface.append("\n\n", fold_body)


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
        self._cwd_provider = lambda: getattr(
            getattr(session, "agent_thread", None), "cwd", None
        ) or getattr(session, "cwd", "")
        self._surface = TranscriptSurface(session.chat_view)
        self._markdown = MarkdownFormatter()
        self._reply_open = False
        self._last_was_tool = False
        self.tools = ToolTranscript(self._cwd_provider)
        self.reasoning = ReasoningTranscript(self._surface)

    def reset_turn(self):
        self._reply_open = False
        self.reasoning.reset()

    def begin_reply(self):
        if not self._reply_open:
            self._reply_open = True
            self.write("\n●\n\n")

    def write(self, text, flush=False):
        rendered = self._markdown.format(text, flush=flush)
        self._surface.append(rendered)

    def error(self, detail):
        self._surface.append("\n\nError: {}\n".format(detail))

    def notice(self, detail):
        self._surface.append("\n\n⚠️ {}\n\n".format(detail))

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
        self.reasoning.finish_all()
        self.write("", flush=True)
        self.write("\n")
