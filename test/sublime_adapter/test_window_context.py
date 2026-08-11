import unittest
from unittest.mock import MagicMock

from echo.runtime.session_store import echo_clients
from echo.sublime_adapter.window_context import EchoWindowContext


class EchoWindowContextTest(unittest.TestCase):
    def setUp(self):
        echo_clients.clear()

    def tearDown(self):
        echo_clients.clear()

    def test_bind_lookup_and_release_share_one_window_key(self):
        window = MagicMock()
        window.id.return_value = 42
        session = MagicMock()
        context = EchoWindowContext(window)

        context.bind(session)

        self.assertIs(session, context.session)
        self.assertIs(session, context.release(stop=True))
        session.stop.assert_called_once_with()
        self.assertIsNone(context.session)

    def test_echo_view_returns_only_marked_view(self):
        plain = MagicMock()
        plain.settings.return_value.get.return_value = False
        echo = MagicMock()
        echo.settings.return_value.get.return_value = True
        window = MagicMock()
        window.views.return_value = [plain, echo]

        self.assertIs(echo, EchoWindowContext(window).echo_view())


if __name__ == "__main__":
    unittest.main()
