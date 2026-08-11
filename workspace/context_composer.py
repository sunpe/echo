"""Construct path references inserted from Sublime's side bar."""

from typing import Iterable, NamedTuple


class ContextDraft(NamedTuple):
    initial_message: str
    insertion: str

    @property
    def empty(self):
        return not self.initial_message and not self.insertion


def path_context(paths: Iterable[str]) -> ContextDraft:
    references = ["@" + path for path in paths if path]
    message = " ".join(references)
    return ContextDraft(message, message + " " if message else "")
