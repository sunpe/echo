import io
import logging
import re
import sys
import unittest
from unittest.mock import MagicMock


sys.modules.setdefault("sublime", MagicMock())

from echo.shared.logging import LOG, update_log_level
from echo.domain.changes.diff_document import build_diff_document


class LoggingConfigurationTest(unittest.TestCase):
    def test_all_handlers_receive_echo_prefix(self):
        original_handlers = list(LOG.handlers)
        original_level = LOG.level
        original_propagate = LOG.propagate
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        try:
            LOG.handlers.clear()
            LOG.addHandler(handler)

            update_log_level({"log_level": "ERROR"})
            LOG.error("boom")

            self.assertRegex(
                stream.getvalue(),
                re.compile(
                    r"^\[echo\] \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} "
                    r"\[ERROR\] echo: boom\n$"
                ),
            )
        finally:
            LOG.handlers.clear()
            LOG.handlers.extend(original_handlers)
            LOG.setLevel(original_level)
            LOG.propagate = original_propagate


class DiffDocumentTest(unittest.TestCase):
    def test_equal_values_do_not_create_review(self):
        self.assertIsNone(build_diff_document("same\n", "same\n", "a.py"))

    def test_document_has_echo_header_and_hunk(self):
        document = build_diff_document("old\n", "new\n", "a.py")

        self.assertEqual("a.py", document.title)
        self.assertTrue(document.content.startswith("diff a/a.py b/a.py\n"))
        self.assertIn("-old\n", document.content)
        self.assertIn("+new\n", document.content)


if __name__ == "__main__":
    unittest.main()
