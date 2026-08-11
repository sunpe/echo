import unittest
from unittest.mock import MagicMock

from echo.sublime_adapter.layout import place_in_dedicated_pane


def view(view_id):
    result = MagicMock()
    result.id.return_value = view_id
    return result


class LayoutTest(unittest.TestCase):
    def test_single_editor_group_gets_a_right_chat_column(self):
        echo = view(1)
        window = MagicMock()
        window.get_layout.return_value = {
            "cols": [0.0, 1.0],
            "rows": [0.0, 1.0],
            "cells": [[0, 0, 1, 1]],
        }
        window.get_view_index.return_value = (0, 1)
        window.views_in_group.return_value = [echo]

        group = place_in_dedicated_pane(window, echo)

        self.assertEqual(1, group)
        self.assertEqual(
            {
                "cols": [0.0, 0.6, 1.0],
                "rows": [0.0, 1.0],
                "cells": [[0, 0, 1, 1], [1, 0, 2, 1]],
            },
            window.set_layout.call_args.args[0],
        )
        window.set_view_index.assert_called_once_with(echo, 1, 0)
        window.focus_view.assert_called_once_with(echo)

    def test_existing_editor_grid_is_preserved_left_of_chat(self):
        echo = view(1)
        window = MagicMock()
        window.get_layout.return_value = {
            "cols": [0.0, 0.5, 1.0],
            "rows": [0.0, 0.5, 1.0],
            "cells": [
                [0, 0, 1, 2],
                [1, 0, 2, 1],
                [1, 1, 2, 2],
            ],
        }
        window.get_view_index.return_value = (2, 0)
        window.views_in_group.return_value = [echo]

        group = place_in_dedicated_pane(window, echo)

        layout = window.set_layout.call_args.args[0]
        self.assertEqual(3, group)
        self.assertEqual([0.0, 0.3, 0.6, 1.0], layout["cols"])
        self.assertEqual([0.0, 0.5, 1.0], layout["rows"])
        self.assertEqual([2, 0, 3, 2], layout["cells"][-1])
        self.assertEqual(
            window.get_layout.return_value["cells"],
            layout["cells"][:-1],
        )
        window.set_view_index.assert_called_once_with(echo, 3, 0)

    def test_rightmost_full_height_chat_group_is_reused(self):
        echo = view(1)
        window = MagicMock()
        window.get_layout.return_value = {
            "cols": [0.0, 0.6, 1.0],
            "rows": [0.0, 1.0],
            "cells": [[0, 0, 1, 1], [1, 0, 2, 1]],
        }
        window.get_view_index.return_value = (1, 0)
        window.views_in_group.return_value = [echo]

        group = place_in_dedicated_pane(window, echo)

        self.assertEqual(1, group)
        window.set_layout.assert_not_called()
        window.set_view_index.assert_called_once_with(echo, 1, 0)
        window.focus_view.assert_called_once_with(echo)

    def test_right_editor_group_is_not_mistaken_for_a_chat_column(self):
        echo = view(1)
        source = view(2)
        window = MagicMock()
        window.get_layout.return_value = {
            "cols": [0.0, 0.5, 1.0],
            "rows": [0.0, 1.0],
            "cells": [[0, 0, 1, 1], [1, 0, 2, 1]],
        }
        window.get_view_index.return_value = (1, 1)
        window.views_in_group.return_value = [source, echo]

        group = place_in_dedicated_pane(window, echo)

        self.assertEqual(2, group)
        self.assertEqual(
            [0.0, 0.3, 0.6, 1.0],
            window.set_layout.call_args.args[0]["cols"],
        )
        window.set_view_index.assert_called_once_with(echo, 2, 0)


if __name__ == "__main__":
    unittest.main()
