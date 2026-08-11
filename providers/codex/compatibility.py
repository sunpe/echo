"""Pure normalization for variant Codex app-server payloads."""

import json
import re

from ...domain.messages.errors import CodexCompatibilityError, CodexRPCError


def visible_models(result):
    catalog = result.get("data") or () if isinstance(result, dict) else ()
    return [
        {
            "displayName": entry.get("displayName") or entry["id"],
            "description": entry.get("description", ""),
            "value": entry["id"],
        }
        for entry in catalog
        if entry.get("id") and not entry.get("hidden")
    ]


def turn_id(payload):
    if not isinstance(payload, dict):
        return None
    direct = payload.get("turnId")
    nested = payload.get("turn")
    return direct or (nested.get("id") if isinstance(nested, dict) else None)


def error_message(error):
    if not error:
        return "Unknown error"
    message = error.get("message", "") if isinstance(error, dict) else str(error)
    try:
        decoded = json.loads(message)
    except (ValueError, TypeError):
        return message
    if not isinstance(decoded, dict):
        return message
    nested = decoded.get("error")
    return (
        nested.get("message", message)
        if isinstance(nested, dict) else decoded.get("message", message)
    )


def validate_version(initialize_result, minimum_version):
    if not isinstance(initialize_result, dict):
        return
    advertised = initialize_result.get("userAgent") or (
        initialize_result.get("serverInfo") or {}
    ).get("version", "")
    actual, minimum = _version(advertised), _version(minimum_version)
    if actual and any(actual) and minimum and actual < minimum:
        raise CodexCompatibilityError(
            "Codex {} is older than required {}".format(
                ".".join(map(str, actual)), minimum_version
            )
        )


def needs_flat_tools(error):
    if not isinstance(error, CodexRPCError):
        return False
    detail = error.error
    message = detail.get("message", "") if isinstance(detail, dict) else detail
    return "inputSchema" in str(message) and "missing field" in str(message)


def flatten_tools(specifications):
    tools, aliases = [], {}
    empty_schema = {"type": "object", "properties": {}}
    for entry in specifications or ():
        if entry.get("type") != "namespace":
            tool = dict(entry)
            tool.setdefault("inputSchema", empty_schema)
            tools.append(tool)
            continue
        namespace = entry.get("name") or ""
        for function in entry.get("tools") or ():
            if function.get("type") != "function":
                continue
            original = function.get("name") or ""
            alias = "{}__{}".format(namespace, original)
            flattened = {
                "type": "function",
                "name": alias,
                "description": function.get("description", ""),
                "inputSchema": function.get("inputSchema") or empty_schema,
            }
            if "deferLoading" in function:
                flattened["deferLoading"] = function["deferLoading"]
            tools.append(flattened)
            aliases[alias] = namespace, original
    return tools, aliases


def _version(value):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(map(int, match.groups())) if match else None
