import tempfile
import unittest
from pathlib import Path

from echo.domain.changes.change_set import (
    build_change_preview,
    UnifiedPatch,
    WorkspaceBoundary,
)


class FileChangeHelpersTest(unittest.TestCase):
    def test_apply_unified_patch(self):
        patch = """--- a/example.txt
+++ b/example.txt
@@ -1,2 +1,2 @@
 first
-second
+updated
"""
        self.assertEqual(
            "first\nupdated\n",
            UnifiedPatch(patch).apply("first\nsecond\n"),
        )

    def test_resolve_workspace_path_rejects_escape(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(
                WorkspaceBoundary(root).resolve("../outside.txt")
            )

    def test_generate_multi_file_change_summary(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "one.txt").write_text("old one\n", encoding="utf-8")
            Path(root, "two.txt").write_text("old two\n", encoding="utf-8")
            result = build_change_preview({
                "changes": [
                    {
                        "path": "one.txt",
                        "kind": {"type": "update"},
                        "content": "new one\n",
                    },
                    {
                        "path": "two.txt",
                        "kind": {"type": "delete"},
                    },
                ]
            }, root)

        self.assertEqual("2 files", result["display_name"])
        self.assertEqual(["one.txt", "two.txt"], result["files"])
        self.assertIn("new one", result["new_text"])
        self.assertNotIn("old two\n\n", result["new_text"])


if __name__ == "__main__":
    unittest.main()
