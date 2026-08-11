import unittest
from unittest.mock import MagicMock

from echo.domain.conversation.checkpoints import PromptCheckpointLedger


class PromptCheckpointLedgerTest(unittest.TestCase):
    def test_attach_and_discard_are_atomic(self):
        ledger = PromptCheckpointLedger()
        first = MagicMock()
        second = MagicMock()
        ledger.add("region-1", first)
        ledger.add("region-2", second)

        self.assertEqual(1, ledger.attach_to_latest("message-2"))
        removed = ledger.discard_from(1)

        self.assertEqual(["region-1"], ledger.regions())
        self.assertEqual("message-2", removed[0].message_id)
        second.update.assert_called_once_with([])

if __name__ == "__main__":
    unittest.main()
