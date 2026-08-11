"""Fast, bounded filename completion helpers for chat prompts."""

import os

import sublime


MAX_DIRECTORY_COMPLETIONS = 200


def _is_below(path, directory):
    try:
        return os.path.commonpath((path, directory)) == os.path.realpath(directory)
    except (OSError, ValueError):
        return False


def build_file_completions(window, chat_view_flag):
    """Build open-file and root-directory completions with bounded I/O."""
    folders = window.folders()
    current_dir = os.path.realpath(folders[0]) if folders else None
    completions = []
    seen_files = set()

    for view in window.views():
        file_path = view.file_name()
        if not file_path or view.settings().get(chat_view_flag, False):
            continue
        file_name = os.path.basename(file_path)
        if file_name in seen_files:
            continue
        seen_files.add(file_name)
        relative = (
            os.path.relpath(file_path, current_dir)
            if current_dir and _is_below(file_path, current_dir)
            else file_name
        )
        completions.append(sublime.CompletionItem(
            file_name,
            annotation="📂 " + relative,
            completion=file_name,
            kind=sublime.KIND_VARIABLE,
        ))

    if not current_dir:
        return completions

    try:
        with os.scandir(current_dir) as entries:
            visible = []
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                visible.append(entry)
                if len(visible) >= MAX_DIRECTORY_COMPLETIONS:
                    break
    except OSError:
        return completions

    for entry in sorted(visible, key=lambda value: value.name.casefold()):
        try:
            is_directory = entry.is_dir()
            is_file = entry.is_file()
        except OSError:
            continue
        if is_file and entry.name not in seen_files:
            seen_files.add(entry.name)
            completions.append(sublime.CompletionItem(
                entry.name,
                annotation="📄 current dir",
                completion=entry.name,
                kind=sublime.KIND_AMBIGUOUS,
            ))
        elif is_directory:
            name = entry.name + "/"
            completions.append(sublime.CompletionItem(
                name,
                annotation="📁 subdirectory",
                completion=name,
                kind=sublime.KIND_NAMESPACE,
            ))
    return completions
