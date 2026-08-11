"""Small editor-independent state values and prompt-boundary UI helpers."""

import enum

import sublime

from ...shared.settings import (
    ECHO_APPROVE_MODE,
    ECHO_CONNECTION_STATE,
    ECHO_INPUT_START,
    ECHO_MODEL,
    ECHO_PLAN_MODE,
)


CHAT_INPUT_START = ECHO_INPUT_START
CHAT_INPUT_ANCHOR = "echo_input_anchor"
CHAT_MODEL = ECHO_MODEL
CHAT_PLAN_MODE = ECHO_PLAN_MODE
CHAT_CONNECTION_STATE = ECHO_CONNECTION_STATE
CHAT_APPROVE_MODE = ECHO_APPROVE_MODE


class PromptAnchor:
    def __init__(self, view):
        self._view = view
        self._settings = view.settings()

    def write(self, position):
        position = int(position)
        self._settings.set(CHAT_INPUT_START, position)
        if position >= self._view.size():
            self._view.erase_regions(CHAT_INPUT_ANCHOR)
        else:
            self._view.add_regions(
                CHAT_INPUT_ANCHOR,
                [sublime.Region(position, position + 1)],
                flags=sublime.HIDDEN | sublime.PERSISTENT,
            )
        return position

    def read(self, fallback=None):
        fallback = self._view.size() if fallback is None else fallback
        marker = next(
            (region for region in self._view.get_regions(CHAT_INPUT_ANCHOR)
             if not region.empty()),
            None,
        )
        if marker is not None:
            position = marker.begin()
            if self._settings.get(CHAT_INPUT_START) != position:
                self._settings.set(CHAT_INPUT_START, position)
            return position
        if not self._settings.has(CHAT_INPUT_START):
            return fallback
        return self.write(min(
            int(self._settings.get(CHAT_INPUT_START)), self._view.size()
        ))

    def editable_start(self):
        return self.read(0) + 1


def set_input_start(view, position):
    return PromptAnchor(view).write(position)


def get_input_start(view, default=None):
    return PromptAnchor(view).read(default)


def input_editable_start(view):
    return PromptAnchor(view).editable_start()


class PlanMode(enum.Enum):
    FAST = "fast"
    PLANNING = "planning"


class ApproveMode(enum.Enum):
    DEFAULT = "default"
    ALLOW_EDIT = "allow-edit"
    ACCEPT_ALL = "accept-all"


class ChoicePanel:
    def __init__(self, window, choices, on_finish, prompt="", multiple=False):
        self._window = window
        self._choices = list(choices)
        self._on_finish = on_finish
        self._prompt = prompt
        self._multiple = multiple
        self._selected = set()

    def show(self):
        self._window.show_quick_panel(
            self._items(),
            self._selected_item,
            flags=sublime.KEEP_OPEN_ON_FOCUS_LOST if self._multiple else 0,
            placeholder=self._prompt,
        )

    def _items(self):
        items = [
            sublime.QuickPanelItem(label, detail)
            for label, detail in self._choices
        ]
        if not self._multiple:
            return items
        done = sublime.QuickPanelItem(
            "Done", "Finish selection", kind=sublime.KIND_ID_AMBIGUOUS
        )
        marked = [
            sublime.QuickPanelItem(
                ("✅ " if index in self._selected else "⬜ ") + item.trigger,
                item.details,
            )
            for index, item in enumerate(items)
        ]
        return [done, *marked]

    def _selected_item(self, index):
        if index < 0:
            self._on_finish(None)
        elif not self._multiple:
            self._on_finish([index])
        elif index == 0:
            self._on_finish(sorted(self._selected))
        else:
            choice = index - 1
            self._selected.symmetric_difference_update({choice})
            sublime.set_timeout(self.show, 0)


class QuestionSequence:
    def __init__(self, window, request_id, input_data, on_submit, on_cancel):
        self._window = window
        self._request_id = request_id
        self._questions = list(input_data.get("questions", ()))
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._answers = {}
        self._index = 0

    def run(self):
        if not self._questions:
            self._on_submit(self._request_id, {})
            return
        self._ask()

    def _ask(self):
        question = self._questions[self._index]
        options = question.get("options") or ()
        choices = [
            (option.get("label", ""), option.get("description", ""))
            for option in options
        ]
        ChoicePanel(
            self._window,
            choices,
            lambda indices: self._answered(question, indices),
            prompt=question.get("question", ""),
            multiple=bool(question.get("multiSelect")),
        ).show()

    def _answered(self, question, indices):
        if indices is None:
            self._on_cancel(self._request_id)
            return
        options = question.get("options") or ()
        labels = [
            options[index].get("label", "")
            for index in indices if index < len(options)
        ]
        if labels:
            key = question.get("id") or question.get("question", "")
            self._answers[key] = labels if question.get("multiSelect") \
                else labels[0]
        self._index += 1
        if self._index < len(self._questions):
            self._ask()
        else:
            self._on_submit(self._request_id, self._answers)
