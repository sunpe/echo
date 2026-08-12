"""Small editor adapter for Echo's protected prompt tail."""

import sublime

from .input_area import build_input_area_layout
from .presentation.ui_components import (
    get_input_start,
    input_editable_start,
    set_input_start,
)


class PromptEditor:
    def __init__(self, view):
        self.view = view

    @property
    def start(self):
        return input_editable_start(self.view)

    def text(self, strip=False):
        value = self.view.substr(sublime.Region(self.start, self.view.size()))
        return value.strip() if strip else value

    def move_to_end(self):
        end = self.view.size()
        caret = sublime.Region(end, end)
        selection = self.view.sel()
        selection.clear()
        selection.add(caret)
        self.view.show(caret)
        return caret.end()

    def replace(self, edit, text):
        self.view.replace(
            edit, sublime.Region(self.start, self.view.size()), text
        )
        self.move_to_end()

    def trim_reserved_lines(self, edit):
        start = self.start
        if start > self.view.size():
            return
        current = self.view.substr(sublime.Region(start, self.view.size()))
        content = current.rstrip("\n")
        if content != current:
            self.view.erase(
                edit,
                sublime.Region(start + len(content), self.view.size()),
            )

    def create_area(self, edit, leading_newlines, text=""):
        origin = self.view.size()
        layout = build_input_area_layout(leading_newlines, text)
        self.view.insert(edit, origin, layout.padding)
        anchor = origin + layout.input_start_offset
        set_input_start(self.view, anchor)
        if layout.initial_text:
            self.view.insert(edit, anchor + 1, layout.initial_text)
        return origin + layout.cursor_offset

    def materialize_prompt(self, edit, marker="❯ "):
        start = self.start
        self.view.insert(edit, start, marker)
        return sublime.Region(start + len(marker), self.view.size())

    def append_output(self, edit, text):
        boundary = get_input_start(self.view, 0)
        inserted = self.view.insert(edit, boundary - 1, text)
        self._move_boundary(boundary + inserted)

    def _move_boundary(self, position):
        set_input_start(self.view, position)
        self.view.show(self.view.size())

    def prepare_programmatic_insert(self):
        selections = list(self.view.sel())
        if len(selections) != 1 or selections[0].begin() < self.start:
            self.move_to_end()

    def insert(self, text):
        if not text:
            return
        self.prepare_programmatic_insert()
        self.view.run_command("insert", {"characters": text})
        self.view.show(self.view.sel()[0].end())
