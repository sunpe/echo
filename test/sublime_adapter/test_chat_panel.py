import sys
import unittest
from unittest.mock import MagicMock, patch


sys.modules.setdefault("sublime", MagicMock())

from echo.sublime_adapter.presentation.chat_panel import LoadingAnimation, RewindConfirmPanel


class LoadingAnimationTest(unittest.TestCase):
    def test_stale_generation_does_not_repaint(self):
        view = MagicMock()
        animation = LoadingAnimation(view)
        animation.phantom_set = MagicMock()
        animation.is_loading = True
        animation._generation = 2

        animation._draw(1)

        animation.phantom_set.update.assert_not_called()


class RewindConfirmPanelTest(unittest.TestCase):
    def test_restore_consumes_callback_once(self):
        view = MagicMock()
        called = []
        panel = RewindConfirmPanel(view)
        panel._callback = lambda: called.append(True)

        panel._navigate("restore")
        panel._navigate("restore")

        self.assertEqual([True], called)
        self.assertFalse(panel.visible)


if __name__ == "__main__":
    unittest.main()
