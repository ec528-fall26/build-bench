import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT = (
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"
)
sys.path.insert(0, str(AGENT))

from src.prompts import experimental_full_log


class FullLogContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log = Path(self.directory.name) / "build.log"
        self.log.write_text("EARLY DETAIL\nerror: example\nFINAL DETAIL\n")

    def test_default_does_not_include_log(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(experimental_full_log(self.log), "")

    def test_full_mode_preserves_small_log(self):
        with patch.dict(os.environ, {
            "BB_EXPERIMENT_LOG_MODE": "full",
        }, clear=True):
            result = experimental_full_log(self.log)
            self.assertIn(self.log.read_text(), result)
            self.assertIn("complete supplied log", result)
            self.assertIn("pre-edit evidence", result)

    def test_large_log_preserves_ends_and_marks_omission(self):
        self.log.write_text("START\n" + "x" * 3000 + "\nEND")
        with patch.dict(os.environ, {
            "BB_EXPERIMENT_LOG_MODE": "full",
            "BB_EXPERIMENT_LOG_MAX_BYTES": "1000",
        }, clear=True):
            result = experimental_full_log(self.log)
            self.assertIn("START", result)
            self.assertIn("END", result)
            self.assertIn("TRUNCATED", result)
            self.assertIn("middle bytes omitted", result)
            self.assertLess(len(result), 2000)

    def test_missing_log_is_explicit(self):
        with patch.dict(os.environ, {
            "BB_EXPERIMENT_LOG_MODE": "full",
        }, clear=True):
            self.assertIn(
                "unavailable",
                experimental_full_log(self.log.parent / "missing"),
            )

    def test_invalid_mode_fails(self):
        with patch.dict(os.environ, {
            "BB_EXPERIMENT_LOG_MODE": "typo",
        }, clear=True):
            with self.assertRaises(ValueError):
                experimental_full_log(self.log)

    def test_invalid_limit_fails(self):
        with patch.dict(os.environ, {
            "BB_EXPERIMENT_LOG_MODE": "full",
            "BB_EXPERIMENT_LOG_MAX_BYTES": "0",
        }, clear=True):
            with self.assertRaises(ValueError):
                experimental_full_log(self.log)


if __name__ == "__main__":
    unittest.main()
