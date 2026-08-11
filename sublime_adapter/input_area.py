from typing import NamedTuple


INPUT_VISIBLE_LINES = 3
DEFAULT_LEADING_NEWLINES = 2


class InputAreaLayout(NamedTuple):
    padding: str
    input_start_offset: int
    initial_text: str
    cursor_offset: int


def build_input_area_layout(leading_newlines, text=""):
    """Describe a three-line input area relative to the current buffer end."""
    if leading_newlines < 1:
        raise ValueError("leading_newlines must be positive")
    initial_text = text + " " if text else ""
    return InputAreaLayout(
        padding="\n" * (leading_newlines + INPUT_VISIBLE_LINES - 1),
        input_start_offset=leading_newlines - 1,
        initial_text=initial_text,
        cursor_offset=leading_newlines + len(initial_text),
    )
