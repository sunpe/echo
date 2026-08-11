"""Display unified-diff documents in Sublime views."""

import sublime

from ..domain.changes.diff_document import DiffDocument, build_diff_document

_RESOURCE_ROOT = __package__.split(".", 1)[0]


def open_diff_document(window, document):
    view = window.new_file()
    view.set_name(document.title)
    view.set_scratch(True)
    view.settings().set("line_numbers", False)
    view.assign_syntax(
        "Packages/{}/chat_diff.sublime-syntax".format(_RESOURCE_ROOT)
    )
    view.run_command(
        "append",
        {"characters": document.content, "disable_tab_translation": True},
    )
    view.set_read_only(True)
    return view


def show_diff(window, old_text, new_text, name):
    """Open a read-only unified diff for two text values."""
    document = build_diff_document(old_text, new_text, name)
    if document is None:
        sublime.status_message("No changes")
        return None
    return open_diff_document(window, document)


def show_diff_text(window, diff_text, name):
    """Open an existing diff in a read-only scratch view."""
    return open_diff_document(window, DiffDocument(name, diff_text))
