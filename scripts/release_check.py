"""Static release checks that don't require Sublime Text."""

import ast
import json
import re
import sys
from pathlib import Path

from build_package import INCLUDED_DIRECTORIES, package_files


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIRS = (
    "application", "domain", "providers", "transport", "workspace",
    "sublime_adapter", "runtime", "shared", "vendor",
)
PLATFORM_SUFFIXES = (".so", ".pyd", ".dll", ".dylib", ".pyc")


def fail(message):
    print("release-check: " + message, file=sys.stderr)
    raise SystemExit(1)


def main():
    for directory in PYTHON_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if re.search(
                r'["\x27]\$\{packages\}[^"\x27]*["\x27]\.format\(',
                source,
            ):
                fail("Sublime package variable passed through format(): " + str(path))
            try:
                ast.parse(
                    source,
                    filename=str(path),
                    feature_version=(3, 8),
                )
            except SyntaxError as exc:
                fail("Python 3.8 syntax error in {}: {}".format(path, exc))

    for path in (ROOT / "vendor").rglob("*"):
        if "__pycache__" in path.parts:
            continue
        if path.is_file() and path.suffix.lower() in PLATFORM_SUFFIXES:
            fail("platform/generated artifact in vendor: {}".format(path))

    for relative in (
        "Echo.sublime-commands",
        "Main.sublime-menu",
        "messages.json",
    ):
        path = ROOT / relative
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail("invalid JSON {}: {}".format(relative, exc))

    main_menu = json.loads(
        (ROOT / "Main.sublime-menu").read_text(encoding="utf-8")
    )
    preferences = next(
        (item for item in main_menu if item.get("id") == "preferences"),
        None,
    )
    package_settings = next(
        (
            item for item in (preferences or {}).get("children", [])
            if item.get("id") == "package-settings"
        ),
        None,
    )
    if not package_settings or "caption" in preferences or "caption" in package_settings:
        fail("Package Settings must extend existing menu ids without replacing captions")

    required = (
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "echo.py",
        "echo.sublime-settings",
        "chat_diff.sublime-syntax",
        "chat_md.sublime-syntax",
        "install.txt",
        "vendor/websockets/__init__.py",
        "vendor/LICENSE.websockets",
        "domain/messages/message.py",
        "domain/messages/errors.py",
        "providers/registry.py",
        "providers/codex/client.py",
        "providers/codex/protocol.py",
        "providers/pi/client.py",
        "transport/websocket.py",
        "transport/rpc_exchange.py",
        "domain/conversation/identity.py",
        "workspace/specs.py",
        "workspace/executor.py",
        "workspace/context_composer.py",
        "application/commands.py",
        "application/bootstrap.py",
        "runtime/provider_worker.py",
        "runtime/session_store.py",
        "domain/conversation/checkpoints.py",
        "domain/conversation/permission_flow.py",
        "domain/conversation/session_runtime.py",
        "sublime_adapter/editor_policy.py",
        "sublime_adapter/layout.py",
        "sublime_adapter/completions.py",
        "sublime_adapter/editor_events.py",
        "sublime_adapter/prompt_editor.py",
        "sublime_adapter/presentation/ui_components.py",
        "shared/settings.py",
        "sublime_adapter/window_context.py",
        "sublime_adapter/workspace_bridge.py",
    )
    for relative in required:
        if not (ROOT / relative).is_file():
            fail("missing required file: " + relative)

    packaged = {
        path.relative_to(ROOT).as_posix()
        for path in package_files(root=ROOT)
    }
    production_modules = {
        path.relative_to(ROOT).as_posix()
        for directory in INCLUDED_DIRECTORIES
        for path in (ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    }
    omitted_modules = sorted(production_modules - packaged)
    if omitted_modules:
        fail(
            "production Python modules excluded from package: "
            + ", ".join(omitted_modules)
        )

    version_source = (ROOT / "shared" / "version.py").read_text(
        encoding="utf-8"
    )
    if not re.search(r'^VERSION = "\d+\.\d+\.\d+"$', version_source, re.M):
        fail("shared/version.py must define a semantic VERSION")

    echo_source = (ROOT / "sublime_adapter" / "presentation" / "chat_view.py").read_text(
        encoding="utf-8"
    )
    if '"${packages}/{}/{}".format(' in echo_source:
        fail("unescaped Sublime packages variable in chat_view.py")

    entry_source = (ROOT / "echo.py").read_text(encoding="utf-8")
    if "import *" in entry_source:
        fail("echo.py must use explicit plugin exports")

    print("release-check: OK")


if __name__ == "__main__":
    main()
