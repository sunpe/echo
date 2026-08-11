"""Persist local routing metadata for provider sessions."""

import sublime

from ..domain.conversation.identity import workspace_fingerprint

SETTINGS_FILE = "echo.sessions.sublime-settings"
REGISTRY_KEY = "session_registry"


def registry_key(provider, endpoint_identity, roots):
    workspace = workspace_fingerprint(roots)
    return "{}:{}:{}".format(provider, endpoint_identity, workspace)


def _settings():
    return sublime.load_settings(SETTINGS_FILE)


def register_session(provider, endpoint_identity, roots, session_id):
    settings = _settings()
    registry = dict(settings.get(REGISTRY_KEY, {}))
    key = registry_key(provider, endpoint_identity, roots)
    ids = list(registry.get(key, []))
    if session_id in ids:
        ids.remove(session_id)
    ids.insert(0, session_id)
    registry[key] = ids[:200]
    settings.set(REGISTRY_KEY, registry)
    sublime.save_settings(SETTINGS_FILE)


def registered_session_ids(provider, endpoint_identity, roots):
    settings = _settings()
    registry = settings.get(REGISTRY_KEY, {})
    return list(registry.get(registry_key(provider, endpoint_identity, roots), []))


def filter_registered_sessions(sessions, registered_ids):
    """Return only sessions explicitly recorded for this server/workspace."""
    allowed = set(registered_ids)
    return [
        session for session in sessions
        if session.get("session_id") in allowed
    ]
