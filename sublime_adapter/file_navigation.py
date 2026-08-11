"""Resolve clickable transcript targets inside the active workspace."""

import os
import re
from urllib.parse import unquote, urlsplit

import sublime


_WINDOWS_ROOT = re.compile(r"^[A-Za-z]:[\\/]")
_MARKDOWN_TARGET = re.compile(
    r"(?<!!)\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)\n]+))"
)
_DIFF_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _absolute(path, cwd):
    rooted = os.path.isabs(path) or _WINDOWS_ROOT.match(path) \
        or path.startswith(("\\\\", "//"))
    return path if rooted else os.path.normpath(os.path.join(cwd, path))


def _inside_roots(path, roots):
    candidate = os.path.normcase(os.path.realpath(path))
    for root in roots:
        try:
            common = os.path.commonpath((candidate, os.path.realpath(root)))
        except ValueError:
            continue
        if os.path.normcase(common) == os.path.normcase(os.path.realpath(root)):
            return True
    return False


def parse_file_target(target, cwd, roots=None):
    """Decode Markdown/file-URI targets into path, line and column."""
    value = unquote(target.strip())
    if value.lower().startswith("file://"):
        uri = urlsplit(value)
        if uri.netloc and uri.netloc.lower() != "localhost":
            value = "//{}{}".format(uri.netloc, uri.path)
        else:
            value = uri.path
            if re.match(r"^/[A-Za-z]:[\\/]", value):
                value = value[1:]
        if uri.fragment:
            value += "#" + uri.fragment
    elif not _WINDOWS_ROOT.match(value) and re.match(
        r"^[a-z][a-z0-9+.-]*:", value, re.IGNORECASE
    ):
        return None

    value, line, column = _split_position(value)
    path = _absolute(value, cwd)
    if not os.path.isfile(path):
        return None
    if roots is not None and not _inside_roots(path, roots):
        return None
    return path, line, column


def _split_position(value):
    fragment = re.search(r"#L(\d+)(?:C(\d+)|-L\d+)?$", value)
    if fragment:
        return (
            value[:fragment.start()],
            int(fragment.group(1)),
            int(fragment.group(2)) if fragment.group(2) else None,
        )
    suffix = re.match(r"^(.*?):(\d+)(?::(\d+))?$", value)
    if suffix:
        return (
            suffix.group(1),
            int(suffix.group(2)),
            int(suffix.group(3)) if suffix.group(3) else None,
        )
    return value, None, None


class TranscriptFileNavigator:
    def __init__(self, session, tool_names):
        self._session = session
        labels = "|".join(map(re.escape, tool_names))
        self._tool_line = re.compile(
            r"^⏺ (?:{}) (.+?)(?:#L(\d+)(?:-L\d+)?)?(?:,.*)?$".format(
                labels
            )
        )

    def open_tool_target(self, line_text, window, view=None, point=None):
        cwd = self._cwd()
        if not cwd:
            return False
        location = self._tool_location(line_text, cwd)
        if location is None and view is not None and point is not None:
            location = self._hunk_location(view, point, line_text, cwd)
        if location is None:
            return False
        self._open(window, location[0], location[1], None)
        return True

    def open_markdown_target(self, line_text, window, view, point):
        if view is None or point is None or not self._cwd():
            return False
        line_region = view.line(point)
        offset = point - line_region.begin()
        link = next(
            (
                match for match in _MARKDOWN_TARGET.finditer(line_text)
                if match.start() <= offset < match.end()
            ),
            None,
        )
        if link is None:
            return False
        location = parse_file_target(
            link.group(1) or link.group(2), self._cwd(), self._roots()
        )
        if location is None:
            return False
        self._open(window, *location)
        return True

    def _tool_location(self, line_text, cwd):
        match = self._tool_line.match(line_text.strip())
        if match is None:
            return None
        path = _absolute(match.group(1).strip(), cwd)
        if not _inside_roots(path, self._roots()):
            return None
        return path, int(match.group(2)) if match.group(2) else None

    def _hunk_location(self, view, point, line_text, cwd):
        hunk = _DIFF_HUNK.match(line_text.strip())
        if hunk is None:
            return None
        for row in range(view.rowcol(point)[0] - 1, -1, -1):
            previous = view.substr(view.line(view.text_point(row, 0))).strip()
            location = self._tool_location(previous, cwd)
            if location is not None:
                return location[0], int(hunk.group(1))
            if previous.startswith("⏺"):
                break
        return None

    def _cwd(self):
        worker = getattr(self._session, "agent_thread", None)
        return getattr(worker, "cwd", None) or getattr(self._session, "cwd", "")

    def _roots(self):
        worker = getattr(self._session, "agent_thread", None)
        extras = getattr(worker, "add_dirs", None)
        if extras is None:
            extras = getattr(self._session, "add_dirs", ())
        return [self._cwd(), *list(extras or ())]

    @staticmethod
    def _open(window, path, line, column):
        if line is None:
            window.open_file(path)
            return
        window.open_file(
            "{}:{}:{}".format(path, line, column or 0),
            sublime.ENCODED_POSITION,
        )
