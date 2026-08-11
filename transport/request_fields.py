"""Configured fields appended to outbound app-server JSON-RPC params."""

from copy import deepcopy
from typing import Any, Dict, Optional


def request_fields(value: Any) -> Dict[str, Any]:
    """Return a safe copy of configured request fields.

    Sublime settings are expected to contain a JSON object.  Ignore invalid or
    empty keys so a malformed preference cannot break an app-server request.
    """
    if not isinstance(value, dict):
        return {}

    result = {}
    for key, field_value in value.items():
        if not isinstance(key, str) or not key.strip():
            continue
        result[key.strip()] = deepcopy(field_value)
    return result


def merge_request_fields(
    params: Optional[Dict[str, Any]], configured_fields: Any
) -> Dict[str, Any]:
    """Merge configured fields without allowing them to replace protocol data."""
    result = request_fields(configured_fields)
    if isinstance(params, dict):
        result.update(params)
    return result
