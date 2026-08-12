"""Change summaries and diff navigation for Echo conversations."""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import NamedTuple

import sublime

from .. import diff_view as diff_view_module
from ...shared.settings import ECHO_DIFF_VIEW_PATH


LOG = logging.getLogger('echo')
ARTIFACT_REGION_KEY = 'echo_artifact_files'
ARTIFACT_REGION_SCOPE = 'region.bluish'
ARTIFACT_REGION_FLAGS = sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.HIDDEN
DIFF_VIEW_PATH_KEY = ECHO_DIFF_VIEW_PATH

_HEADER = re.compile(r'^(?:diff [ab]/(.+?) [ab]/.+|[+-]{3} [ab]/(.+))$')
_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')


def _diff_target(line):
    header = _HEADER.match(line)
    if header:
        return "file", header.group(1) or header.group(2)
    hunk = _HUNK.match(line)
    return ("line", int(hunk.group(1))) if hunk else (None, None)


def _resolve_project_file(window, relative):
    candidates = (
        os.path.normpath(os.path.join(root, relative))
        for root in (window.folders() or ())
    )
    return next((path for path in candidates if os.path.isfile(path)), None)


def diff_view_click(view, abs_path, click_point):
    """Open the source file or source line represented by a diff click."""
    line = view.substr(view.line(click_point)).strip()
    target_kind, target = _diff_target(line)
    if target_kind == "file":
        source = _resolve_project_file(view.window(), target)
        if source:
            view.window().open_file(source)
        else:
            sublime.status_message('File not found: ' + target)
        return True
    if target_kind == "line":
        if os.path.isfile(abs_path):
            view.window().open_file(
                '{}:{}'.format(abs_path, target),
                sublime.ENCODED_POSITION,
            )
        else:
            sublime.status_message('File not found')
        return True
    return False


def _private_directories(extra_env=None):
    environment = dict(os.environ)
    environment.update(extra_env or {})
    configured = environment.get('CODEX_HOME') or '~/.codex'
    return [os.path.realpath(os.path.expanduser(configured))]


def is_agent_data_path(path, extra_env=None):
    if not path:
        return False
    try:
        candidate = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    return any(
        candidate == root or candidate.startswith(root + os.sep)
        for root in _private_directories(extra_env)
    )


@dataclass
class _Change:
    relative: str
    diffs: list = field(default_factory=list)
    added: int = 0
    removed: int = 0

    def include(self, diff_text):
        if not diff_text:
            return
        self.diffs.append(diff_text)
        added, removed = _diff_delta(diff_text)
        self.added += added
        self.removed += removed


class _ChangeLedger:
    """Turn-scoped change storage independent of Sublime UI state."""

    def __init__(self):
        self.entries = {}
        self.pending = []

    def add(self, path, relative, diff_text):
        entry = self.entries.setdefault(path, _Change(relative))
        entry.include(diff_text)
        if path not in self.pending:
            self.pending.append(path)

    def take(self):
        paths, self.pending = self.pending, []
        values = [(path, self.entries.pop(path, _Change(path))) for path in paths]
        return values

    def reset(self):
        self.entries.clear()
        self.pending = []


def _diff_delta(diff_text):
    lines = diff_text.splitlines()
    added = sum(line.startswith('+') and not line.startswith('+++')
                for line in lines)
    removed = sum(line.startswith('-') and not line.startswith('---')
                  for line in lines)
    return added, removed


class ArtifactLink(NamedTuple):
    region: object
    absolute: str
    relative: str
    diffs: list


class ArtifactDocument(NamedTuple):
    text: str
    links: list
    fold_region: object


def build_artifact_document(changes, insertion_point, region_factory):
    count = len(changes)
    heading = "▣ {} file{} changed".format(
        count, "" if count == 1 else "s"
    )
    lines = [heading + "\n"]
    links = []
    cursor = insertion_point + len(lines[0])

    for absolute, change in changes:
        statistics = ""
        if change.added or change.removed:
            statistics = "  +{} -{}".format(change.added, change.removed)
        line = "    {}{}\n".format(change.relative, statistics)
        name_start = cursor + 4
        links.append(ArtifactLink(
            region_factory(name_start, name_start + len(change.relative)),
            absolute,
            change.relative,
            list(change.diffs),
        ))
        lines.append(line)
        cursor += len(line)

    lines.append("\N{NO-BREAK SPACE}")
    text = "".join(lines)
    folded = region_factory(
        insertion_point + len(heading),
        insertion_point + len(text) - 1,
    )
    return ArtifactDocument(text, links, folded)


def build_recorded_diff(relative, chunks):
    header = [
        "diff a/{0} b/{0}\n".format(relative),
        "--- a/{0}\n".format(relative),
        "+++ b/{0}\n".format(relative),
    ]
    body = [
        chunk if chunk.endswith("\n") else chunk + "\n"
        for chunk in chunks
    ]
    return "".join(header + body)


class FileChangesArtifact:
    """Collect per-turn changes and render a compact clickable summary."""

    def __init__(self, view, window, input_start_fn):
        self.view, self.window = view, window
        self.input_start_fn, self._ledger = input_start_fn, _ChangeLedger()
        self.file_regions = []

    def record(self, abs_path, rel_path, diff_text, extra_env=None):
        if is_agent_data_path(abs_path, extra_env):
            LOG.debug('Ignoring agent-private change: %s', abs_path)
            return
        self._ledger.add(abs_path, rel_path, diff_text)

    def show(self):
        if not self._ledger.pending:
            return
        changes = self._ledger.take()
        start = self.input_start_fn(self.view) - 1
        document = build_artifact_document(changes, start, sublime.Region)
        self.view.run_command(
            'echo_chat_output_append', {'text': document.text}
        )
        self.file_regions.extend(document.links)
        self._redraw_regions()
        self.view.fold(document.fold_region)

    def _redraw_regions(self):
        if not self.file_regions:
            self.view.erase_regions(ARTIFACT_REGION_KEY)
            return
        self.view.add_regions(
            ARTIFACT_REGION_KEY,
            [link.region for link in self.file_regions],
            ARTIFACT_REGION_SCOPE,
            '',
            ARTIFACT_REGION_FLAGS,
        )

    def open_diff_at(self, point):
        if point is None or self._point_is_folded(point):
            return False
        link = next(
            (item for item in self.file_regions if item.region.contains(point)),
            None,
        )
        if link is None:
            return False
        self._show_diff(link)
        return True

    def _point_is_folded(self, point):
        regions = self.view.folded_regions() \
            if hasattr(self.view, 'folded_regions') else ()
        return any(region.contains(point) for region in regions)

    def _show_diff(self, link):
        if not link.diffs:
            sublime.status_message('No recorded changes for this file')
            return
        content = build_recorded_diff(link.relative, link.diffs)
        previous = next(
            (view for view in self.window.views()
             if view.settings().get(DIFF_VIEW_PATH_KEY) == link.absolute),
            None,
        )
        if previous:
            previous.close()
        diff_view = diff_view_module.show_diff_text(
            self.window, content,
            os.path.basename(link.relative) + ' (changes)',
        )
        diff_view.settings().set(DIFF_VIEW_PATH_KEY, link.absolute)

    def truncate(self, cut_point):
        self._ledger.reset()
        self.file_regions = [
            link for link in self.file_regions if link.region.end() < cut_point
        ]
        self._redraw_regions()

    def clear(self):
        self._ledger.reset()
        self.file_regions = []
        self.view.erase_regions(ARTIFACT_REGION_KEY)
