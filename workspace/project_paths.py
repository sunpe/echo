"""Canonical workspace selection for an editor window."""

from pathlib import Path

from ..shared.settings import ECHO_WORKSPACE


class WorkspaceSelection:
    def __init__(self, view):
        self._window = view.window()

    def folders(self):
        return list(self._window.folders()) if self._window is not None else []

    def primary(self):
        if self._window is None:
            return ""
        override = self._window.settings().get(ECHO_WORKSPACE)
        if override and Path(override).is_dir():
            return override
        roots = self.folders()
        return roots[0] if roots else ""


def get_all_folders(view):
    return WorkspaceSelection(view).folders()


def get_best_dir(view):
    return WorkspaceSelection(view).primary()


def additional_workspace_roots(cwd, folders):
    """Preserve display paths while removing duplicate resolved roots."""
    primary = _identity(cwd) if cwd else None
    identities = {primary} if primary else set()
    extras = []
    for candidate in folders or ():
        identity = _identity(candidate)
        if identity not in identities:
            identities.add(identity)
            extras.append(candidate)
    return extras


def _identity(path):
    try:
        return str(Path(path).resolve()).casefold()
    except (OSError, RuntimeError):
        return str(path).casefold()
