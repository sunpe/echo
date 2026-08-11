import unittest

from echo.sublime_adapter.input_area import (
    DEFAULT_LEADING_NEWLINES,
    build_input_area_layout,
)


class InputAreaLayoutTest(unittest.TestCase):
    def test_reserves_three_lines_and_keeps_cursor_on_first_line(self):
        layout = build_input_area_layout(5, "draft")

        self.assertEqual("\n" * 7, layout.padding)
        self.assertEqual(4, layout.input_start_offset)
        self.assertEqual("draft ", layout.initial_text)
        self.assertEqual(11, layout.cursor_offset)
        editable_padding = layout.padding[layout.input_start_offset + 1:]
        self.assertEqual("\n\n", editable_padding)

    def test_rejects_missing_leading_separator(self):
        with self.assertRaises(ValueError):
            build_input_area_layout(0)

    def test_default_composer_spacing_is_compact(self):
        layout = build_input_area_layout(DEFAULT_LEADING_NEWLINES)

        self.assertEqual("\n" * 4, layout.padding)
        self.assertEqual(1, layout.input_start_offset)
        self.assertEqual(2, layout.cursor_offset)


if __name__ == "__main__":
    unittest.main()
