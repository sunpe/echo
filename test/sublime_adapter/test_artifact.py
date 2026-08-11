import sys
import unittest
from unittest.mock import MagicMock


sys.modules.setdefault('sublime', MagicMock())

from echo.sublime_adapter.presentation.artifact import (
    _ChangeLedger,
    build_artifact_document,
    build_recorded_diff,
    is_agent_data_path,
)


class ChangeLedgerTest(unittest.TestCase):
    def test_take_preserves_first_seen_order_and_accumulates_stats(self):
        ledger = _ChangeLedger()
        ledger.add('/tmp/a.py', 'a.py', '+++ b/a.py\n+one\n-two\n')
        ledger.add('/tmp/a.py', 'a.py', '+three\n')
        ledger.add('/tmp/b.py', 'b.py', '')

        changes = ledger.take()

        self.assertEqual(['/tmp/a.py', '/tmp/b.py'], [path for path, _ in changes])
        self.assertEqual((2, 1), (changes[0][1].added, changes[0][1].removed))
        self.assertEqual([], ledger.pending)

    def test_agent_private_paths_are_not_project_paths(self):
        self.assertTrue(is_agent_data_path('/tmp/codex/config.json', {'CODEX_HOME': '/tmp/codex'}))
        self.assertFalse(is_agent_data_path('/tmp/project/main.py', {'CODEX_HOME': '/tmp/codex'}))

    def test_document_projection_keeps_link_offsets_with_statistics(self):
        ledger = _ChangeLedger()
        ledger.add('/work/a.py', 'src/a.py', '+new\n-old\n')

        document = build_artifact_document(
            ledger.take(), 10, lambda start, end: (start, end)
        )

        self.assertEqual((31, 39), document.links[0].region)
        self.assertIn('src/a.py  +1 -1', document.text)

    def test_recorded_diff_has_one_normalized_header(self):
        self.assertEqual(
            'diff a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n+one\n',
            build_recorded_diff('a.py', ['+one']),
        )


if __name__ == '__main__':
    unittest.main()
