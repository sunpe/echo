"""Pure unified-diff document construction."""

import difflib
from typing import NamedTuple


class DiffDocument(NamedTuple):
    title: str
    content: str


def build_diff_document(old_text, new_text, name):
    body = list(difflib.unified_diff(
        old_text.splitlines(True),
        new_text.splitlines(True),
        fromfile="a/" + name,
        tofile="b/" + name,
        n=5,
        lineterm="",
    ))
    if not body:
        return None
    header = [
        "diff a/{0} b/{0}\n".format(name),
        "--- a/{0}\n".format(name),
        "+++ b/{0}\n".format(name),
    ]
    normalized = [
        line if line.endswith("\n") else line + "\n"
        for line in body[2:]
    ]
    return DiffDocument(name, "".join(header + normalized))
