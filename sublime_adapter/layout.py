"""Keep the Echo transcript in a full-height column on the far right."""


CHAT_COLUMN_WIDTH = 0.4
_DEFAULT_LAYOUT = {
    "cols": [0.0, 1.0],
    "rows": [0.0, 1.0],
    "cells": [[0, 0, 1, 1]],
}


def _layout_parts(layout):
    cols = list(layout.get("cols") or _DEFAULT_LAYOUT["cols"])
    rows = list(layout.get("rows") or _DEFAULT_LAYOUT["rows"])
    cells = [list(cell) for cell in layout.get("cells") or _DEFAULT_LAYOUT["cells"]]
    if len(cols) < 2 or len(rows) < 2:
        return _layout_parts(_DEFAULT_LAYOUT)
    return cols, rows, cells


def _is_right_column(cell, cols, rows):
    return (
        len(cell) == 4
        and cell[0] > 0
        and cell[1] == 0
        and cell[2] == len(cols) - 1
        and cell[3] == len(rows) - 1
    )


def _append_right_column(layout, width=CHAT_COLUMN_WIDTH):
    cols, rows, cells = _layout_parts(layout)
    editor_edge = 1.0 - width
    resized_cols = [round(value * editor_edge, 6) for value in cols]
    resized_cols[0] = 0.0
    resized_cols[-1] = editor_edge
    chat_group = len(cells)
    cells.append([len(cols) - 1, 0, len(cols), len(rows) - 1])
    return {
        "cols": resized_cols + [1.0],
        "rows": rows,
        "cells": cells,
    }, chat_group


def place_in_dedicated_pane(window, echo_view):
    """Move ``echo_view`` to an idempotent, full-height rightmost group."""
    layout = window.get_layout()
    cols, rows, cells = _layout_parts(layout)
    group, _ = window.get_view_index(echo_view)

    shares_group = group >= 0 and any(
        view.id() != echo_view.id() for view in window.views_in_group(group)
    )
    if (
        0 <= group < len(cells)
        and not shares_group
        and _is_right_column(cells[group], cols, rows)
    ):
        chat_group = group
    else:
        updated_layout, chat_group = _append_right_column(layout)
        window.set_layout(updated_layout)

    window.set_view_index(echo_view, chat_group, 0)
    window.focus_view(echo_view)
    return chat_group
