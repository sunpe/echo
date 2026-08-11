"""Safe local workspace tools exposed to a remote Codex app-server."""

import asyncio
import fnmatch
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable

from .specs import DEFAULT_DENIED_GLOBS


class LocalWorkspaceTools:
    def __init__(
        self,
        roots: Iterable[str],
        enabled: Iterable[str],
        denied_globs: Iterable[str] = None,
        max_read_bytes: int = 1024 * 1024,
        max_output_bytes: int = 1024 * 1024,
    ):
        roots = [os.path.realpath(root) for root in roots if root]
        if not roots:
            raise ValueError("At least one local workspace root is required")
        self.roots = roots
        self.root_ids = {
            "root-{}".format(index + 1): root
            for index, root in enumerate(roots)
        }
        self.enabled = set(enabled)
        self.denied_globs = tuple(
            denied_globs if denied_globs is not None else DEFAULT_DENIED_GLOBS
        )
        self.max_read_bytes = int(max_read_bytes)
        self.max_output_bytes = int(max_output_bytes)

    async def __call__(self, namespace: str, tool: str, arguments: Dict[str, Any]):
        if namespace != "local_workspace":
            raise ValueError("Unsupported dynamic tool namespace")
        return await self.execute(tool, arguments)

    async def execute(
        self,
        tool: str,
        arguments: Dict[str, Any],
        validate_output: bool = True,
    ):
        """Execute one approved tool without exposing implementation details."""
        self._check_tool(tool)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self.execute_sync, tool, arguments, False
        )
        if validate_output:
            self.validate_output(result)
        return result

    def execute_sync(
        self,
        tool: str,
        arguments: Dict[str, Any],
        validate_output: bool = True,
    ) -> Dict[str, Any]:
        """Synchronous counterpart for Sublime's main-thread bridge."""
        method = self._check_tool(tool)
        result = method(arguments)
        if validate_output:
            self.validate_output(result)
        return result

    def _check_tool(self, tool):
        """Return the concrete tool method after policy validation."""
        if tool not in self.enabled:
            raise PermissionError("Local tool is disabled: " + tool)
        method = getattr(self, "_tool_" + tool, None)
        if method is None:
            raise ValueError("Unknown local tool: " + tool)
        return method

    def validate_output(self, result: Dict[str, Any]) -> None:
        encoded = json.dumps(
            result, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > self.max_output_bytes:
            raise ValueError("Tool result exceeds local_tools.max_output_bytes")

    def resolve(
        self,
        relative: str,
        must_exist: bool = True,
        root_id: str = None,
    ) -> str:
        """Resolve a workspace-relative path after policy validation."""
        return self._resolve(relative, must_exist=must_exist, root_id=root_id)

    def root_for(self, root_id: str = None) -> str:
        """Return a configured workspace root by its public root id."""
        return self._root_for(root_id)

    def read_text(self, path: str) -> str:
        """Read a policy-validated local text file within the configured limit."""
        return self._read_text(path)

    def _root_for(self, root_id: str = None) -> str:
        root_id = root_id or "root-1"
        try:
            return self.root_ids[root_id]
        except KeyError:
            raise ValueError("Unknown workspace root: " + str(root_id))

    def _resolve(
        self,
        relative: str,
        must_exist: bool = True,
        root_id: str = None,
    ) -> str:
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("path must be a non-empty string")
        if os.path.isabs(relative):
            raise PermissionError("Absolute paths are not allowed")
        normalized = os.path.normpath(relative).replace("\\", "/")
        if normalized == ".." or normalized.startswith("../"):
            raise PermissionError("Path escapes the local workspace")
        if self._is_denied(normalized):
            raise PermissionError("Path is denied by local workspace policy")
        root = self._root_for(root_id)
        candidate = os.path.realpath(os.path.join(root, normalized))
        if not (candidate == root or candidate.startswith(root + os.sep)):
            raise PermissionError("Path escapes the local workspace")
        if must_exist and not os.path.exists(candidate):
            raise FileNotFoundError(relative)
        return candidate

    def _is_denied(self, relative: str) -> bool:
        normalized = relative.replace("\\", "/").strip("/")
        for pattern in self.denied_globs:
            if fnmatch.fnmatch(normalized, pattern):
                return True
            if pattern.endswith("/**") and normalized == pattern[:-3]:
                return True
        return False

    @staticmethod
    def _sha256_file(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _read_text(self, path: str) -> str:
        if os.path.getsize(path) > self.max_read_bytes:
            raise ValueError("File exceeds local_tools.max_read_bytes")
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def _tool_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root_id = args.get("root")
        root = self._root_for(root_id)
        base = self._resolve(args.get("path", "."), root_id=root_id)
        recursive = bool(args.get("recursive", False))
        entries = []

        def append_entry(path, entry_type):
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                self._resolve(relative, root_id=root_id)
            except (FileNotFoundError, PermissionError, ValueError):
                return False
            entries.append({"path": relative, "type": entry_type})
            return len(entries) >= 2000

        if recursive:
            stop = False
            for directory, dirnames, filenames in os.walk(base):
                allowed_dirs = []
                for name in sorted(dirnames):
                    path = os.path.join(directory, name)
                    relative = os.path.relpath(path, root).replace(os.sep, "/")
                    if self._is_denied(relative):
                        continue
                    try:
                        self._resolve(relative, root_id=root_id)
                    except (FileNotFoundError, PermissionError, ValueError):
                        continue
                    allowed_dirs.append(name)
                    if append_entry(path, "directory"):
                        stop = True
                        break
                dirnames[:] = allowed_dirs
                if stop:
                    break
                for name in sorted(filenames):
                    if append_entry(os.path.join(directory, name), "file"):
                        stop = True
                        break
                if stop:
                    break
        else:
            for entry in sorted(Path(base).iterdir(), key=lambda value: value.name):
                if append_entry(
                    str(entry), "directory" if entry.is_dir() else "file"
                ):
                    break
        return {
            "root": root_id or "root-1",
            "entries": entries,
            "truncated": len(entries) >= 2000,
        }

    def _tool_pwd(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "roots": [
                {
                    "id": root_id,
                    "name": os.path.basename(root) or root_id,
                    "primary": index == 0,
                }
                for index, (root_id, root) in enumerate(self.root_ids.items())
            ]
        }

    def _tool_stat(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args["path"], root_id=args.get("root"))
        stat = os.stat(path)
        result = {
            "path": args["path"],
            "root": args.get("root") or "root-1",
            "type": "directory" if os.path.isdir(path) else "file",
            "size": stat.st_size,
            "modifiedAt": stat.st_mtime,
        }
        if os.path.isfile(path):
            result["sha256"] = self._sha256_file(path)
        return result

    def _tool_read(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args["path"], root_id=args.get("root"))
        text = self._read_text(path)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        lines = text.splitlines(keepends=True)
        start = max(int(args.get("startLine", 1)), 1)
        end = min(int(args.get("endLine", len(lines) or 1)), len(lines))
        return {
            "path": args["path"],
            "root": args.get("root") or "root-1",
            "startLine": start,
            "endLine": end,
            "sha256": digest,
            "text": "".join(lines[start - 1:end]),
        }

    def _tool_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = args["query"]
        root_id = args.get("root")
        root = self._root_for(root_id)
        base = self._resolve(args.get("path", "."), root_id=root_id)
        pattern = args.get("glob", "*")
        limit = min(int(args.get("maxResults", 100)), 500)
        matches = []
        for directory, dirnames, filenames in os.walk(base):
            allowed_dirs = []
            for name in sorted(dirnames):
                relative = os.path.relpath(
                    os.path.join(directory, name), root
                ).replace(os.sep, "/")
                if self._is_denied(relative):
                    continue
                try:
                    self._resolve(relative, root_id=root_id)
                except (FileNotFoundError, PermissionError, ValueError):
                    continue
                allowed_dirs.append(name)
            dirnames[:] = allowed_dirs
            for filename in sorted(filenames):
                full_path = os.path.join(directory, filename)
                relative = os.path.relpath(full_path, root).replace(os.sep, "/")
                if not fnmatch.fnmatch(relative, pattern) and not fnmatch.fnmatch(filename, pattern):
                    continue
                try:
                    self._resolve(relative, root_id=root_id)
                    if os.path.getsize(full_path) > self.max_read_bytes:
                        continue
                    handle = open(full_path, "r", encoding="utf-8")
                except (UnicodeDecodeError, OSError, ValueError, PermissionError):
                    continue
                try:
                    for line_number, line in enumerate(handle, 1):
                        if query in line:
                            matches.append({
                                "path": relative,
                                "line": line_number,
                                "text": line.rstrip("\r\n")[:500],
                            })
                            if len(matches) >= limit:
                                return {
                                    "root": root_id or "root-1",
                                    "matches": matches,
                                    "truncated": True,
                                }
                except UnicodeDecodeError:
                    continue
                finally:
                    handle.close()
        return {
            "root": root_id or "root-1",
            "matches": matches,
            "truncated": False,
        }

    def _tool_write(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args["path"], root_id=args.get("root"))
        text = self._read_text(path)
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual != args["expectedSha256"]:
            raise RuntimeError("File changed since it was read; read it again")
        updated = text
        for replacement in args["replacements"]:
            old = replacement["old"]
            if not old or updated.count(old) != 1:
                raise ValueError("Each replacement.old must occur exactly once")
            updated = updated.replace(old, replacement["new"], 1)
        self._atomic_write(path, updated)
        return {
            "path": args["path"],
            "root": args.get("root") or "root-1",
            "sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
        }

    def _tool_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(
            args["path"],
            must_exist=False,
            root_id=args.get("root"),
        )
        if os.path.exists(path):
            raise FileExistsError(args["path"])
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise FileNotFoundError("Parent directory does not exist")
        self._atomic_write(path, args["content"])
        return {
            "path": args["path"],
            "root": args.get("root") or "root-1",
            "sha256": hashlib.sha256(args["content"].encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        directory = os.path.dirname(path)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".echo-",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(
                descriptor, "w", encoding="utf-8", newline=""
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if os.path.exists(path):
                os.chmod(temporary, os.stat(path).st_mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
