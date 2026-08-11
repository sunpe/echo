"""Load only project-root AGENTS.md and rules.md for app-server threads."""

import hashlib
import os
from typing import Callable, Optional, Tuple

INSTRUCTION_FILES = ("AGENTS.md", "rules.md")
MAX_INSTRUCTION_BYTES = 64 * 1024

APP_SERVER_WORKSPACE_INSTRUCTIONS = """\
This client exposes the user's local project only through the
local_workspace dynamic tool namespace. The app-server filesystem is not the
project. Use local_workspace for every project file listing, search, read, and
write. Never use built-in filesystem or shell tools for project operations.
Paths passed to local_workspace must be relative to the local project root.
References formatted as @root-N:relative/path identify a local workspace root
and path. Pass root-N as the root argument and the remainder as path.
"""


def load_project_instructions(
    project_root: str,
    max_bytes: int = MAX_INSTRUCTION_BYTES,
    read_open_file: Optional[Callable[[str], Optional[str]]] = None,
) -> Tuple[str, str]:
    sections = [APP_SERVER_WORKSPACE_INSTRUCTIONS.strip()]
    for filename in INSTRUCTION_FILES:
        path = os.path.join(project_root, filename)
        text = read_open_file(path) if read_open_file else None
        if text is not None:
            size = len(text.encode("utf-8"))
        else:
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        if size > max_bytes:
            raise ValueError(
                "{} exceeds the {} byte project instruction limit".format(
                    filename, max_bytes
                )
            )
        sections.append("[Project root {}]\n{}".format(filename, text.strip()))
    merged = "\n\n".join(sections)
    digest = hashlib.sha256(merged.encode("utf-8")).hexdigest()
    return merged, digest

