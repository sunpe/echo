"""Pure policies for protecting Echo's transcript from editor mutations."""

DELETE_COMMANDS = frozenset({
    "left_delete",
    "right_delete",
    "delete_word",
    "delete_word_backward",
    "delete_to_mark",
    "run_macro_file",
    "cut",
})

MUTATION_COMMANDS = frozenset({
    "insert",
    "paste",
    "insert_characters",
    "insert_snippet",
    "append",
    "yank",
    "paste_and_indent",
    "clipboard_history_paste",
})


def clamp_carets(selections, boundary, region_at):
    """Move carets out of history while preserving non-empty selections."""
    changed = False
    result = []
    for selection in selections:
        if selection.empty() and selection.begin() < boundary:
            result.append(region_at(boundary))
            changed = True
        else:
            result.append(selection)
    return changed, result


def deletion_crosses_boundary(command, selections, boundary):
    if command not in DELETE_COMMANDS:
        return False
    for selection in selections:
        if selection.begin() < boundary:
            return True
        if (
            command in ("left_delete", "delete_word_backward")
            and selection.empty()
            and selection.begin() == boundary
        ):
            return True
    return False


def mutation_starts_in_history(command, selections, boundary):
    return (
        command in MUTATION_COMMANDS
        and any(selection.begin() < boundary for selection in selections)
    )
