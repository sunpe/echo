import unittest

from echo.sublime_adapter.editor_policy import (
    clamp_carets,
    deletion_crosses_boundary,
    mutation_starts_in_history,
)


class Selection:
    def __init__(self, begin, end=None):
        self._begin = begin
        self._end = begin if end is None else end

    def begin(self):
        return self._begin

    def empty(self):
        return self._begin == self._end


class EditorPolicyTest(unittest.TestCase):
    def test_only_carets_are_clamped_out_of_history(self):
        selected_history = Selection(1, 5)
        changed, values = clamp_carets(
            [Selection(2), selected_history], 10, lambda point: (point, point)
        )

        self.assertTrue(changed)
        self.assertEqual((10, 10), values[0])
        self.assertIs(selected_history, values[1])

    def test_backspace_at_boundary_is_blocked(self):
        self.assertTrue(deletion_crosses_boundary(
            "left_delete", [Selection(10)], 10
        ))
        self.assertFalse(deletion_crosses_boundary(
            "right_delete", [Selection(10)], 10
        ))

    def test_mutation_before_boundary_is_redirected(self):
        self.assertTrue(mutation_starts_in_history(
            "paste", [Selection(3)], 10
        ))
        self.assertFalse(mutation_starts_in_history(
            "paste", [Selection(12)], 10
        ))


if __name__ == "__main__":
    unittest.main()
