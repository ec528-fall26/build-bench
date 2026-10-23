"""Rust diagnostic detail and location-aware deduplication."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
    ),
)

from src.contracts import Attempt, BuildResult
from src.evidence import diagnose, RUST_DETAIL_CHARS
from src.prompts import (
    attempt_result_message,
    render_diagnostics,
    COMPILER_DETAIL_TOTAL_CHARS,
)


class RustDetails(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.log = self.root / "build.log"

    def parse(self, text):
        self.log.write_text(text)
        return diagnose(self.log, self.root)

    def block(self, line):
        return (
            "error[E0308]: arguments to this function are incorrect\n"
            f"  --> src/example.rs:{line}:5\n"
            "   |\n"
            f"{line} | foreign_call(first, second)\n"
            "   | ^^^^^^^^^^^^\n"
            "note: expected `*const u8`, found `*const i8`\n"
            f"  --> src/example.rs:{line + 1}:7\n"
            f"{line + 1} | first\n"
            "   | ^^^^^\n"
            "note: expected `*const u8`, found `*const i8`\n"
            f"  --> src/example.rs:{line + 2}:7\n"
            f"{line + 2} | second\n"
            "   | ^^^^^^\n"
        )

    def test_same_headline_at_different_locations_is_preserved(self):
        result = self.parse(self.block(10) + self.block(20))
        self.assertEqual([item.line for item in result], [10, 20])

    def test_exact_repeated_diagnostic_is_deduplicated(self):
        result = self.parse(self.block(10) * 3)
        self.assertEqual(len(result), 1)

    def test_secondary_locations_do_not_replace_primary(self):
        item = self.parse(self.block(10))[0]
        self.assertEqual(item.line, 10)
        self.assertEqual(item.source_line, "foreign_call(first, second)")
        self.assertIn("src/example.rs:11:7", item.compiler_details)
        self.assertIn("src/example.rs:12:7", item.compiler_details)

    def test_quotes_do_not_create_errors_or_change_stage(self):
        result = self.parse(
            "dh_auto_build\n"
            + self.block(10)
            + "13 | // error: fake; dh_auto_install\n"
            + self.block(20)
        )
        self.assertEqual(len(result), 2)
        self.assertEqual([item.stage for item in result], ["build", "build"])

    def test_detail_limit_does_not_hide_next_error(self):
        result = self.parse(
            self.block(10)
            + ("note: " + "x" * 500 + "\n") * 100
            + self.block(20)
        )
        self.assertEqual(len(result), 2)
        self.assertLessEqual(len(result[0].compiler_details), RUST_DETAIL_CHARS)
        self.assertIn("[Compiler details truncated.]", result[0].compiler_details)
        self.assertIn("expected `*const u8`", result[1].compiler_details)

    def test_warning_notes_are_not_attached_to_previous_error(self):
        result = self.parse(
            self.block(10)
            + "warning: unrelated warning\n"
            + "note: unrelated warning explanation\n"
        )
        self.assertNotIn("unrelated", result[0].compiler_details)

    def test_both_argument_details_reach_attempt_feedback(self):
        diagnostics = self.parse(self.block(10))
        build = BuildResult(False, "build", diagnostics, 1.0)
        attempt = Attempt(1, "test hypothesis", [], 1, build, {})
        text = attempt_result_message(attempt)
        self.assertIn("expected `*const u8`, found `*const i8`", text)
        self.assertIn("src/example.rs:11:7", text)
        self.assertIn("src/example.rs:12:7", text)

    def test_prompt_detail_total_is_bounded_and_marked(self):
        diagnostics = self.parse(
            "".join(self.block(line) for line in range(1, 13))
        )
        for item in diagnostics:
            item.compiler_details = "Z" * RUST_DETAIL_CHARS
        text = render_diagnostics(diagnostics)
        self.assertEqual(text.count("Z"), COMPILER_DETAIL_TOTAL_CHARS)
        self.assertIn("Additional compiler details omitted", text)


if __name__ == "__main__":
    unittest.main()
