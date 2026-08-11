import unittest

from echo.shared.settings import (
    ECHO_APPROVE_MODE,
    ECHO_CONNECTION_STATE,
    ECHO_DIFF_VIEW_PATH,
    ECHO_INPUT_START,
    ECHO_MODEL,
    ECHO_PLAN_MODE,
    ECHO_PLAN_REQUEST_ID,
    ECHO_SESSION_ID,
    ECHO_VIEW_FLAG,
    ECHO_WORKSPACE,
)


class SettingsKeyTest(unittest.TestCase):
    def test_all_persisted_keys_are_echo_owned_and_unique(self):
        keys = [
            ECHO_VIEW_FLAG,
            ECHO_WORKSPACE,
            ECHO_SESSION_ID,
            ECHO_INPUT_START,
            ECHO_MODEL,
            ECHO_PLAN_MODE,
            ECHO_APPROVE_MODE,
            ECHO_CONNECTION_STATE,
            ECHO_PLAN_REQUEST_ID,
            ECHO_DIFF_VIEW_PATH,
        ]

        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(key.startswith("echo_") for key in keys))


if __name__ == "__main__":
    unittest.main()
