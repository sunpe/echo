import unittest

from echo.sublime_adapter.prompt_history import PromptHistory


class PromptHistoryTest(unittest.TestCase):
    def test_navigation_restores_the_unsubmitted_draft(self):
        history = PromptHistory()
        history.record("first")
        history.record("second")

        self.assertEqual("second", history.older("draft"))
        self.assertEqual("first", history.older("ignored"))
        self.assertIsNone(history.older("ignored"))
        self.assertEqual("second", history.newer())
        self.assertEqual("draft", history.newer())
        self.assertIsNone(history.newer())


if __name__ == "__main__":
    unittest.main()
