import unittest
from unittest.mock import MagicMock

from echo.sublime_adapter.editor_events import history_move, is_plain_word_click


class EditorEventDecisionTest(unittest.TestCase):
    def test_modified_word_click_is_not_plain_navigation(self):
        self.assertFalse(is_plain_word_click("drag_select", {
            "by": "words", "extend": True
        }))

    def test_up_on_prompt_first_row_uses_history(self):
        view = MagicMock()
        caret = MagicMock()
        caret.empty.return_value = True
        caret.begin.return_value = 20
        view.sel.return_value = [caret]
        view.is_auto_complete_visible.return_value = False
        view.rowcol.side_effect = [(3, 0), (3, 0)]

        command = history_move(
            view,
            "move",
            {"by": "lines", "forward": False},
            18,
            lambda start, end: (start, end),
        )

        self.assertEqual("echo_chat_history_up", command)


if __name__ == "__main__":
    unittest.main()
