import sys
import unittest
from unittest.mock import MagicMock


sys.modules.setdefault("sublime", MagicMock())

from echo.sublime_adapter.presentation.md_render import MarkdownFormatter


class MarkdownFormatterTest(unittest.TestCase):
    def test_partial_line_is_held_until_the_stream_continues(self):
        formatter = MarkdownFormatter()

        self.assertEqual("", formatter.format("hello"))
        self.assertEqual("hello world\n", formatter.format(" world\n"))

    def test_table_is_aligned_when_flushed(self):
        formatter = MarkdownFormatter()

        rendered = formatter.format(
            "| name | value |\n| --- | --- |\n| A | 1 |", flush=True
        )

        self.assertIn("name", rendered)
        self.assertIn("value", rendered)
        self.assertNotIn("| --- |", rendered)

    def test_fenced_pipe_lines_are_not_treated_as_tables(self):
        formatter = MarkdownFormatter()
        source = "```\n| raw | text |\n```"

        self.assertEqual(source, formatter.format(source, flush=True))


if __name__ == "__main__":
    unittest.main()
