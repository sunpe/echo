import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


class ReleaseWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_release_is_tag_driven_and_can_write_release_assets(self):
        self.assertIn('"v*.*.*"', self.source)
        self.assertIn("contents: write", self.source)
        self.assertIn("gh release create", self.source)

    def test_release_checks_version_and_runs_project_gates(self):
        self.assertIn("from shared.version import VERSION", self.source)
        self.assertIn("python scripts/release_check.py", self.source)
        self.assertIn("python -m unittest discover", self.source)

    def test_release_publishes_runtime_checked_package_and_checksum(self):
        self.assertIn("python scripts/build_package.py", self.source)
        self.assertIn("ECHO_PACKAGE=echo.sublime-package", self.source)
        self.assertNotIn("ECHO_PACKAGE=echo-v${version}", self.source)
        self.assertIn('package.read(".python-version")', self.source)
        self.assertIn("sha256sum", self.source)
        self.assertIn("actions/upload-artifact@v4", self.source)


if __name__ == "__main__":
    unittest.main()
