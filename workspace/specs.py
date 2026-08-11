"""Dynamic tool declarations for the local workspace namespace."""

from typing import Any, Dict, Iterable, List

DEFAULT_DENIED_GLOBS = (
    ".env", ".env.*", "**/.env", "**/.env.*",
    ".ssh/**", "**/.ssh/**", ".aws/**", "**/.aws/**",
    "*credentials*", "**/*credentials*", "*secret*", "**/*secret*",
    ".git/**", "**/.git/**",
)
DEFAULT_ENABLED_TOOLS = (
    "pwd", "list", "stat", "read", "search", "write", "create",
)


def _path_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "root": {"type": "string", "default": "root-1"},
            "path": {"type": "string"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }


def dynamic_tool_specs(enabled: Iterable[str]) -> List[Dict[str, Any]]:
    definitions = {
        "pwd": (
            "Return the local Sublime workspace roots and their identifiers.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        "list": (
            "List files below a local project-relative directory.",
            {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "default": "root-1"},
                    "path": {"type": "string", "default": "."},
                    "recursive": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        ),
        "stat": (
            "Return size, modification time and sha256 for a local project file.",
            _path_schema(),
        ),
        "read": (
            "Read a UTF-8 local project file, optionally by one-based line range.",
            {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "default": "root-1"},
                    "path": {"type": "string"},
                    "startLine": {"type": "integer", "minimum": 1},
                    "endLine": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        "search": (
            "Search literal text in local project files.",
            {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "default": "root-1"},
                    "query": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "default": "."},
                    "glob": {"type": "string", "default": "*"},
                    "maxResults": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        "write": (
            "Apply exact replacements to a local UTF-8 file. expectedSha256 "
            "must match the current file and each old string must occur once.",
            {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "default": "root-1"},
                    "path": {"type": "string"},
                    "expectedSha256": {"type": "string"},
                    "replacements": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                            "required": ["old", "new"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["path", "expectedSha256", "replacements"],
                "additionalProperties": False,
            },
        ),
        "create": (
            "Create a new UTF-8 file. Fails if the path already exists.",
            {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "default": "root-1"},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        ),
    }
    tools = []
    for name in enabled:
        if name in definitions:
            description, schema = definitions[name]
            tools.append({
                "type": "function", "name": name,
                "description": description, "inputSchema": schema,
            })
    return [{
        "type": "namespace",
        "name": "local_workspace",
        "description": "Access the project opened by the local Sublime client.",
        "tools": tools,
    }]
