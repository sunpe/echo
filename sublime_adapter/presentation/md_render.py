"""Incremental Markdown normalization for streamed agent output."""

import re
import unicodedata

import sublime


_ALIGNMENT_CELL = re.compile(r"^:?-{3,}:?$")


def _display_width(value):
    return sum(
        2 if unicodedata.east_asian_width(character) in "WFA" else 1
        for character in value
    )


class _TableBlock:
    def __init__(self, source_lines):
        self.rows = [self._cells(line) for line in source_lines]

    @staticmethod
    def _cells(line):
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    def render(self, style):
        divider = next(
            (
                index for index, row in enumerate(self.rows[1:], 1)
                if row and all(_ALIGNMENT_CELL.fullmatch(cell) for cell in row)
            ),
            None,
        )
        if divider is None:
            return None
        column_count = max(map(len, self.rows))
        for row in self.rows:
            row += [""] * (column_count - len(row))
        widths = [
            max(
                3,
                *(
                    _display_width(row[column])
                    for index, row in enumerate(self.rows)
                    if index != divider
                ),
            )
            for column in range(column_count)
        ]
        return (
            self._bordered(divider, widths)
            if style == "bordered" else self._markdown(divider, widths)
        )

    def _bordered(self, divider, widths):
        rule = "-" * sum(width + 2 for width in widths)
        rendered = [rule]
        visible_rows = [
            row for index, row in enumerate(self.rows) if index != divider
        ]
        for index, row in enumerate(visible_rows):
            if index:
                rendered.append(rule)
            rendered.append("".join(
                " {}{} ".format(cell, " " * (widths[column] - _display_width(cell)))
                for column, cell in enumerate(row)
            ).rstrip())
        rendered.append(rule)
        return rendered

    def _markdown(self, divider, widths):
        rendered = []
        for index, row in enumerate(self.rows):
            values = []
            for column, cell in enumerate(row):
                value = "-" * widths[column] if index == divider else cell
                values.append(value + " " * (widths[column] - _display_width(value)))
            rendered.append("| {} |".format(" | ".join(values)))
        return rendered


class MarkdownFormatter:
    def __init__(self):
        self._fragment = ""
        self._table_lines = []
        self._inside_fence = False

    def format(self, text, flush=False):
        chunk = (self._fragment + (text or "")).expandtabs(4)
        complete, self._fragment = self._take_complete_lines(chunk, flush)
        output = []
        for line in complete:
            self._consume(line, output)
        if flush:
            output.extend(self._drain_table())
        if not output:
            return ""
        joined = "\n".join(output)
        return joined if flush else joined + "\n"

    def _take_complete_lines(self, chunk, flush):
        if flush:
            self._fragment = ""
            return chunk.splitlines(), ""
        if not chunk or chunk.endswith("\n"):
            return chunk.splitlines(), ""
        boundary = chunk.rfind("\n")
        if boundary < 0:
            return [], chunk
        return chunk[:boundary + 1].splitlines(), chunk[boundary + 1:]

    def _consume(self, line, output):
        stripped = line.strip()
        if stripped.startswith("```"):
            output.extend(self._drain_table())
            self._inside_fence = not self._inside_fence
            output.append(line)
        elif self._looks_like_table_row(stripped):
            self._table_lines.append(line)
        else:
            output.extend(self._drain_table())
            output.append(line)

    def _looks_like_table_row(self, stripped):
        return (
            not self._inside_fence
            and stripped.startswith("|")
            and "|" in stripped[1:]
        )

    def _drain_table(self):
        if not self._table_lines:
            return []
        source, self._table_lines = self._table_lines, []
        block = _TableBlock(source)
        rendered = block.render(self._table_style())
        return source if rendered is None else rendered

    @staticmethod
    def _table_style():
        try:
            return sublime.load_settings("echo.sublime-settings").get(
                "table_style", "bordered"
            )
        except Exception:
            return "bordered"
