"""Stable, privacy-preserving identities for provider session routing."""

import hashlib
import os
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit


def server_identity(url: str) -> str:
    parsed = urlsplit(url or "")
    normalized = urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        "",
    ))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def workspace_fingerprint(roots: Iterable[str]) -> str:
    normalized = [
        os.path.normcase(os.path.realpath(root))
        for root in roots
        if root
    ]
    payload = "\n".join(sorted(set(normalized)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def session_reference(
    provider: str,
    endpoint_identity: str,
    roots: Iterable[str],
    session_id: str,
):
    return {
        "schemaVersion": 2,
        "provider": provider,
        "endpointIdentity": endpoint_identity,
        "workspaceFingerprint": workspace_fingerprint(roots),
        "sessionId": session_id,
    }
