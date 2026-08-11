"""Normalize @file references without exposing local absolute paths."""

import os
import re
from typing import Iterable

REFERENCE_RE = re.compile(r"(?<!\S)@([^\s]+)")


def normalize_local_references(text: str, roots: Iterable[str]) -> str:
    roots = [os.path.realpath(root) for root in roots if root]
    if not roots:
        return text
    changed = False

    def replace(match):
        nonlocal changed
        token = match.group(1)
        path_token, separator, fragment = token.partition("#")
        canonical = re.match(r"^root-(\d+):(.+)$", path_token)
        if canonical:
            root_number = int(canonical.group(1))
            relative = os.path.normpath(canonical.group(2))
            if (
                root_number < 1
                or root_number > len(roots)
                or relative == ".."
                or relative.startswith(".." + os.sep)
            ):
                changed = True
                return "@unavailable-local-path"
            relative = relative.replace(os.sep, "/")
            suffix = "#" + fragment if separator else ""
            return "@root-{}:{}{}".format(
                root_number, relative, suffix
            )
        candidate = os.path.expanduser(path_token)
        root_index = 0
        if os.path.isabs(candidate):
            resolved = os.path.realpath(candidate)
            selected = None
            for index, root in enumerate(roots):
                if resolved == root or resolved.startswith(root + os.sep):
                    selected = (index, root)
                    break
            if selected is None:
                changed = True
                return "@unavailable-local-path"
            root_index, root = selected
            relative = os.path.relpath(resolved, root)
        else:
            relative = os.path.normpath(candidate)
            if relative == ".." or relative.startswith(".." + os.sep):
                changed = True
                return "@unavailable-local-path"
        changed = True
        relative = relative.replace(os.sep, "/")
        suffix = "#" + fragment if separator else ""
        return "@root-{}:{}{}".format(root_index + 1, relative, suffix)

    normalized = REFERENCE_RE.sub(replace, text)
    if changed:
        normalized += (
            "\n\necho local references use @root-N:relative/path. "
            "Use local_workspace tools with the matching root and path."
        )
    return normalized
