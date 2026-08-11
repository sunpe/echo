"""Build editor previews for app-server file mutations."""

from dataclasses import dataclass
from pathlib import Path
import os
import re


_HUNK = re.compile(r"^@@ -(\d+),?\d* \+(\d+),?\d* @@")


class WorkspaceBoundary:
    def __init__(self, primary, extras=()):
        self._roots = [self._canonical(primary), *(
            self._canonical(root) for root in extras or ()
        )]

    def resolve(self, candidate):
        if not isinstance(candidate, str) or not candidate.strip():
            return None
        path = candidate if os.path.isabs(candidate) \
            else os.path.join(self._roots[0], candidate)
        canonical = self._canonical(path)
        for root in self._roots:
            try:
                if os.path.commonpath((canonical, root)) == root:
                    return canonical
            except ValueError:
                continue
        return None

    @staticmethod
    def _canonical(path):
        return os.path.normcase(os.path.realpath(path))


class UnifiedPatch:
    def __init__(self, patch):
        self._lines = (patch or "").splitlines(keepends=True)

    def apply(self, original):
        source = original.splitlines(keepends=True)
        output, source_index = [], 0
        cursor = next(
            (index for index, line in enumerate(self._lines)
             if line.startswith("@@")),
            None,
        )
        if cursor is None:
            return original
        try:
            while cursor < len(self._lines):
                header = _HUNK.match(self._lines[cursor])
                if header is None:
                    cursor += 1
                    continue
                hunk_start = int(header.group(1)) - 1
                output.extend(source[source_index:hunk_start])
                source_index = hunk_start
                cursor += 1
                while cursor < len(self._lines) and not self._lines[cursor].startswith("@@"):
                    operation, value = self._lines[cursor][:1], self._lines[cursor][1:]
                    if operation == " ":
                        output.append(source[source_index])
                        source_index += 1
                    elif operation == "+":
                        output.append(value)
                    elif operation == "-":
                        source_index += 1
                    cursor += 1
        except (IndexError, TypeError, ValueError):
            return original
        output.extend(source[source_index:])
        return "".join(output)


@dataclass(frozen=True)
class FileMutation:
    path: str
    before: str
    after: str

    @classmethod
    def from_payload(cls, payload, path):
        before = _read_text(path)
        kind = payload.get("kind") or {}
        operation = kind.get("type") if isinstance(kind, dict) else None
        operation = operation or payload.get("type", "update")
        content = payload.get("content") or ""
        patch = payload.get("diff") or payload.get("patch") \
            or payload.get("unified_diff")
        if operation in ("add", "create"):
            after = content
        elif operation == "delete":
            after = ""
        elif patch:
            after = UnifiedPatch(patch).apply(before)
        elif content:
            after = content
        else:
            after = before
        return cls(path, before, after)


def build_change_preview(params, cwd, additional_roots=()):
    boundary = WorkspaceBoundary(cwd, additional_roots)
    mutations = []
    for payload in _change_payloads(params):
        path = boundary.resolve(payload.get("path", ""))
        if path:
            mutations.append(FileMutation.from_payload(payload, path))
    if not mutations:
        return None
    before, after = _combine(mutations)
    names = [Path(mutation.path).name for mutation in mutations]
    return {
        "old_text": before,
        "new_text": after,
        "display_name": names[0] if len(names) == 1 else "{} files".format(len(names)),
        "count": len(names),
        "files": names,
    }


def _change_payloads(params):
    changes = params.get("changes") or ()
    if changes:
        return list(changes)
    mapping = params.get("fileChanges") or {}
    return [dict(payload, path=path) for path, payload in mapping.items()
            if isinstance(payload, dict)] if isinstance(mapping, dict) else []


def _combine(mutations):
    if len(mutations) == 1:
        item = mutations[0]
        return item.before, item.after
    old_sections, new_sections = [], []
    for item in mutations:
        heading = "File: {}\n{}\n".format(item.path, "=" * 40)
        old_sections.append(heading + item.before)
        new_sections.append(heading + item.after)
    return "\n\n".join(old_sections) + "\n\n", \
        "\n\n".join(new_sections) + "\n\n"


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
