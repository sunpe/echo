import unittest

from echo.workspace.context_composer import path_context


class ContextComposerTest(unittest.TestCase):
    def test_sidebar_paths_form_one_prompt_fragment(self):
        draft = path_context(["src/app.py", "docs"])

        self.assertEqual("@src/app.py @docs", draft.initial_message)
        self.assertEqual("@src/app.py @docs ", draft.insertion)


if __name__ == "__main__":
    unittest.main()
