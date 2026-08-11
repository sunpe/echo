"""Bridge local workspace tools to Sublime buffers on the main thread."""

import asyncio
import difflib
import fnmatch
import hashlib
import os
from typing import Any, Dict, Iterable

import sublime
import sublime_plugin

from ..workspace.executor import LocalWorkspaceTools
from ..workspace.project_instructions import load_project_instructions


class EchoApplyWorkspaceEditCommand(sublime_plugin.TextCommand):
    def run(self, edit, content):
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)


class SublimeWorkspaceTools:
    def __init__(
        self,
        window,
        roots: Iterable[str],
        enabled: Iterable[str],
        denied_globs=None,
        max_read_bytes: int = 1024 * 1024,
        max_output_bytes: int = 1024 * 1024,
        on_file_change=None,
    ):
        self.window = window
        self.on_file_change = on_file_change
        self.disk = LocalWorkspaceTools(
            roots=roots,
            enabled=enabled,
            denied_globs=denied_globs,
            max_read_bytes=max_read_bytes,
            max_output_bytes=max_output_bytes,
        )

    async def __call__(
        self, namespace: str, tool: str, arguments: Dict[str, Any]
    ):
        if namespace != "local_workspace":
            raise ValueError("Unsupported dynamic tool namespace")
        if tool not in self.disk.enabled:
            raise PermissionError("Local tool is disabled: " + tool)
        if tool in ("read", "stat"):
            result = await self._on_main_thread(tool + "_buffer", arguments)
            if result is None:
                result = await self.disk.execute(
                    tool, arguments, validate_output=False
                )
        elif tool == "search":
            replacements = await self._on_main_thread(
                "search_buffers", arguments
            )
            result = await self.disk.execute(
                tool, arguments, validate_output=False
            )
            result = self._merge_search_results(result, replacements, arguments)
        elif tool == "write":
            result = await self._on_main_thread(
                "write_buffer", arguments
            )
            if result is None:
                path = self.disk.resolve(
                    arguments["path"], root_id=arguments.get("root")
                )
                loop = asyncio.get_running_loop()
                before = await loop.run_in_executor(
                    None, self.disk.read_text, path
                )
                result = await self.disk.execute(tool, arguments, validate_output=False)
                after = await loop.run_in_executor(
                    None, self.disk.read_text, path
                )
                await self._on_main_thread("record_disk_change", {
                    "path": path,
                    "arguments": arguments,
                    "before": before,
                    "after": after,
                })
        elif tool == "create":
            await self._on_main_thread("validate_create", arguments)
            result = await self.disk.execute(
                tool, arguments, validate_output=False
            )
            await self._on_main_thread("finish_create", {
                "path": self.disk.resolve(
                    arguments["path"], root_id=arguments.get("root")
                ),
                "arguments": arguments,
            })
        else:
            result = await self.disk.execute(
                tool, arguments, validate_output=False
            )
        self.disk.validate_output(result)
        return result

    async def _on_main_thread(self, tool: str, arguments: Dict[str, Any]):
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def run():
            try:
                result = getattr(self, "_tool_" + tool)(arguments)
            except Exception as exc:
                loop.call_soon_threadsafe(self._set_future_exception, future, exc)
            else:
                loop.call_soon_threadsafe(self._set_future_result, future, result)

        sublime.set_timeout(run, 0)
        return await future

    @staticmethod
    def _set_future_result(future, result):
        if not future.done():
            future.set_result(result)

    @staticmethod
    def _set_future_exception(future, exception):
        if not future.done():
            future.set_exception(exception)

    async def load_project_instructions(self):
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def run():
            try:
                result = self._load_project_instructions_main()
            except Exception as exc:
                loop.call_soon_threadsafe(self._set_future_exception, future, exc)
            else:
                loop.call_soon_threadsafe(self._set_future_result, future, result)

        sublime.set_timeout(run, 0)
        return await future

    def _load_project_instructions_main(self):
        project_root = self.disk.root_ids["root-1"]
        def read_open_file(path):
            view = self._find_view(path)
            if view is not None:
                return self._view_text(view)
            return None

        return load_project_instructions(
            project_root, read_open_file=read_open_file
        )[0]

    def _find_view(self, path: str):
        view = self.window.find_open_file(path)
        if view and view.is_valid():
            return view
        target = os.path.realpath(path)
        for candidate in self.window.views():
            filename = candidate.file_name()
            if (
                filename
                and candidate.is_valid()
                and os.path.realpath(filename) == target
            ):
                return candidate
        return None

    @staticmethod
    def _view_text(view) -> str:
        return view.substr(sublime.Region(0, view.size()))

    def _tool_read_buffer(self, arguments: Dict[str, Any]):
        path = self.disk.resolve(
            arguments["path"], root_id=arguments.get("root")
        )
        view = self._find_view(path)
        if view is None:
            return None
        text = self._view_text(view)
        encoded = text.encode("utf-8")
        if len(encoded) > self.disk.max_read_bytes:
            raise ValueError("File exceeds local_tools.max_read_bytes")
        lines = text.splitlines(keepends=True)
        start = max(int(arguments.get("startLine", 1)), 1)
        end = min(int(arguments.get("endLine", len(lines) or 1)), len(lines))
        return {
            "path": arguments["path"],
            "root": arguments.get("root") or "root-1",
            "startLine": start,
            "endLine": end,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "changeCount": view.change_count(),
            "dirty": view.is_dirty(),
            "text": "".join(lines[start - 1:end]),
        }

    def _tool_stat_buffer(self, arguments: Dict[str, Any]):
        path = self.disk.resolve(
            arguments["path"], root_id=arguments.get("root")
        )
        view = self._find_view(path)
        if view is None:
            return None
        text = self._view_text(view)
        encoded = text.encode("utf-8")
        result = {
            "path": arguments["path"],
            "root": arguments.get("root") or "root-1",
            "type": "file",
            "size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "changeCount": view.change_count(),
            "dirty": view.is_dirty(),
        }
        if os.path.exists(path):
            result["modifiedAt"] = os.path.getmtime(path)
        return result

    def _tool_search_buffers(self, arguments: Dict[str, Any]):
        root_id = arguments.get("root") or "root-1"
        root = self.disk.root_for(root_id)
        base = self.disk.resolve(
            arguments.get("path", "."), root_id=root_id
        )
        pattern = arguments.get("glob", "*")
        query = arguments["query"]
        replacements = {}

        for view in self.window.views():
            path = view.file_name()
            if not path or not view.is_valid() or not view.is_dirty():
                continue
            real_path = os.path.realpath(path)
            if not (
                real_path == base or real_path.startswith(base + os.sep)
            ):
                continue
            relative = os.path.relpath(real_path, root).replace(os.sep, "/")
            try:
                self.disk.resolve(
                    relative, must_exist=False, root_id=root_id
                )
            except (FileNotFoundError, PermissionError, ValueError):
                continue
            filename = os.path.basename(relative)
            if (
                not fnmatch.fnmatch(relative, pattern)
                and not fnmatch.fnmatch(filename, pattern)
            ):
                continue
            text = self._view_text(view)
            if len(text.encode("utf-8")) > self.disk.max_read_bytes:
                continue
            replacements[relative] = [
                {
                    "path": relative,
                    "line": line_number,
                    "text": line[:500],
                }
                for line_number, line in enumerate(text.splitlines(), 1)
                if query in line
            ]

        return replacements

    @staticmethod
    def _merge_search_results(result, replacements, arguments):
        limit = min(int(arguments.get("maxResults", 100)), 500)
        matches = [
            match for match in result["matches"]
            if match["path"] not in replacements
        ]
        for relative in sorted(replacements):
            matches.extend(replacements[relative])
        result["matches"] = matches[:limit]
        result["truncated"] = result["truncated"] or len(matches) > limit
        return result

    def _tool_write_buffer(self, arguments: Dict[str, Any]):
        path = self.disk.resolve(
            arguments["path"], root_id=arguments.get("root")
        )
        view = self._find_view(path)
        if view is None:
            return None

        text = self._view_text(view)
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual != arguments["expectedSha256"]:
            raise RuntimeError("File changed since it was read; read it again")

        updated = text
        for replacement in arguments["replacements"]:
            old = replacement["old"]
            if not old or updated.count(old) != 1:
                raise ValueError("Each replacement.old must occur exactly once")
            updated = updated.replace(old, replacement["new"], 1)

        view.run_command(
            "echo_apply_workspace_edit",
            {"content": updated},
        )
        result = {
            "path": arguments["path"],
            "root": arguments.get("root") or "root-1",
            "sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
            "changeCount": view.change_count(),
            "dirty": view.is_dirty(),
        }
        self._record_change(path, arguments, text, updated)
        return result

    def _tool_record_disk_change(self, payload):
        self._record_change(
            payload["path"],
            payload["arguments"],
            payload["before"],
            payload["after"],
        )

    def _tool_validate_create(self, arguments):
        path = self.disk.resolve(
            arguments["path"],
            must_exist=False,
            root_id=arguments.get("root"),
        )
        if self._find_view(path) is not None:
            raise FileExistsError(arguments["path"])

    def _tool_finish_create(self, payload):
        self.window.open_file(payload["path"])
        arguments = payload["arguments"]
        self._record_change(
            payload["path"], arguments, "", arguments["content"]
        )

    def _record_change(self, path, arguments, before, after):
        if not self.on_file_change or before == after:
            return
        relative = arguments["path"].replace("\\", "/")
        diff = "\n".join(difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="a/" + relative,
            tofile="b/" + relative,
            lineterm="",
        ))
        self.on_file_change(path, relative, diff)
