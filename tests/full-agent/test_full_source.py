"""Complete source context: size limits, freshness, and path safety."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
    ),
)

from src.contracts import Diagnostic
from src.prompts import render_source_excerpts


class FullSource(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "input").mkdir()

    def source(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def diagnostic(self, name):
        return Diagnostic("error: example", "build", name, 1, [name], None, 0)

    def test_full_file_includes_distant_source(self):
        self.source("input/example.rs", "first\n" + "middle\n" * 100 + "last\n")
        text = render_source_excerpts(
            [self.diagnostic("input/example.rs")], self.root
        )
        self.assertIn("Complete current source file: input/example.rs", text)
        self.assertIn("last\n", text)

    def test_reads_current_contents_each_time(self):
        path = self.source("input/example.rs", "before\n")
        diagnostics = [self.diagnostic("input/example.rs")]
        self.assertIn("before\n", render_source_excerpts(diagnostics, self.root))
        path.write_text("after\n")
        text = render_source_excerpts(diagnostics, self.root)
        self.assertIn("after\n", text)
        self.assertNotIn("before\n", text)

    def test_duplicate_diagnostics_do_not_duplicate_file(self):
        self.source("input/example.rs", "content\n")
        diagnostic = self.diagnostic("input/example.rs")
        text = render_source_excerpts([diagnostic, diagnostic], self.root)
        self.assertEqual(text.count("=== Complete current source file:"), 1)

    def test_large_file_uses_existing_excerpt_renderer(self):
        self.source("input/example.rs", "x" * 64_001)
        diagnostics = [self.diagnostic("input/example.rs")]
        with patch("src.prompts._render_source_windows", return_value="EXCERPT") as fallback:
            text = render_source_excerpts(diagnostics, self.root)
        self.assertEqual(text, "EXCERPT")
        fallback.assert_called_once_with(diagnostics, self.root)

    def test_combined_limit_falls_back_for_second_file(self):
        self.source("input/one.rs", "a" * 60_000)
        self.source("input/two.rs", "b" * 50_000)
        first = self.diagnostic("input/one.rs")
        second = self.diagnostic("input/two.rs")
        with patch("src.prompts._render_source_windows", return_value="EXCERPT") as fallback:
            text = render_source_excerpts([first, second], self.root)
        self.assertEqual(text.count("=== Complete current source file:"), 1)
        fallback.assert_called_once_with([second], self.root)

    def test_symlink_is_not_read_as_complete_source(self):
        outside = self.source("outside.rs", "PRIVATE_CONTENT")
        link = self.root / "input/link.rs"
        link.symlink_to(outside)
        with patch("src.prompts._render_source_windows", return_value=""):
            text = render_source_excerpts(
                [self.diagnostic("input/link.rs")], self.root
            )
        self.assertNotIn("PRIVATE_CONTENT", text)

    def test_parent_escape_is_not_read_as_complete_source(self):
        self.source("outside.rs", "PRIVATE_CONTENT")
        with patch("src.prompts._render_source_windows", return_value=""):
            text = render_source_excerpts(
                [self.diagnostic("input/../outside.rs")], self.root
            )
        self.assertNotIn("PRIVATE_CONTENT", text)


if __name__ == "__main__":
    unittest.main()
