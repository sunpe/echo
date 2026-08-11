"""Build a deterministic echo.sublime-package archive."""

import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INCLUDED_TOP_LEVEL_FILES = {
    "Default (OSX).sublime-keymap",
    "Default.sublime-keymap",
    "Echo.sublime-commands",
    "LICENSE",
    "Main.sublime-menu",
    "README.md",
    "Side Bar.sublime-menu",
    "THIRD_PARTY_NOTICES.md",
    "Tab Context.sublime-menu",
    "messages.json",
    "echo.py",
    "echo.sublime-settings",
    "install.txt",
    "chat_diff.sublime-syntax",
    "chat_md.sublime-syntax",
}
INCLUDED_DIRECTORIES = {
    "application", "domain", "providers", "transport", "workspace",
    "sublime_adapter", "runtime", "shared", "vendor",
}
EXCLUDED_PARTS = {"__pycache__", "tests"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".so", ".pyd", ".dll", ".dylib"}
EXCLUDED_NAMES = {".DS_Store"}


def include(path, root=ROOT, output=None):
    if not path.is_file() or path.is_symlink():
        return False
    if output is not None and path.resolve() == output.resolve():
        return False
    relative = path.relative_to(root)
    if len(relative.parts) == 1:
        return relative.name in INCLUDED_TOP_LEVEL_FILES
    if relative.parts[0] not in INCLUDED_DIRECTORIES:
        return False
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if any(part in EXCLUDED_NAMES for part in relative.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def package_files(root=ROOT, output=None):
    return sorted(
        path for path in root.rglob("*")
        if include(path, root=root, output=output)
    )


def main():
    output = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else ROOT / "dist" / "echo.sublime-package"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    files = package_files(output=output)
    with zipfile.ZipFile(
        str(output), "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for path in files:
            info = zipfile.ZipInfo(
                path.relative_to(ROOT).as_posix(),
                date_time=(2020, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    print("{} files -> {}".format(len(files), output))


if __name__ == "__main__":
    main()
