"""Missing replacement text receives source hints without fuzzy application."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
))
from src.edit_preflight import focused_feedback


def edit(old, new):
    return SimpleNamespace(path="input/client.rs", old_text=old, new_text=new)


def snapshot(text):
    return SimpleNamespace(files=[
        SimpleNamespace(path="input/client.rs", content=text.encode("utf-8"))
    ])


class MissingContextTests(unittest.TestCase):
    def test_multiline_source_is_shown_but_still_rejected(self):
        text = (
            "let value = std::ffi::CStr::from_ptr(repos_uuid)\n"
            "    .to_str()\n"
            "    .unwrap();\n"
        )
        old = "std::ffi::CStr::from_ptr(repos_uuid).to_str().unwrap()"
        index, feedback = focused_feedback(
            snapshot(text), [edit(old, "replacement")], "repair"
        )
        self.assertEqual(index, 0)
        self.assertIn("old_text_not_found", feedback)
        self.assertIn("1: let value", feedback)
        self.assertIn("2:     .to_str()", feedback)
        self.assertIn("NOT confirmed matches", feedback)

    def test_actual_and_simulated_source_are_distinguished(self):
        original = "let repos_uuid = old_value;\n"
        index, feedback = focused_feedback(
            snapshot(original),
            [
                edit("old_value", "new_value"),
                edit("let repos_uuid = absent_value;", "replacement"),
            ],
            "repair",
        )
        self.assertEqual(index, 1)
        self.assertIn("let repos_uuid = new_value;", feedback)
        self.assertIn("let repos_uuid = old_value;", feedback)
        self.assertIn("Actual file on disk", feedback)
        self.assertIn("earlier simulated edits", feedback)

    def test_unrelated_source_does_not_claim_a_match(self):
        index, feedback = focused_feedback(
            snapshot("unrelated\n"),
            [edit("repos_uuid", "replacement")],
            "repair",
        )
        self.assertEqual(index, 0)
        self.assertIn("No related source lines found", feedback)

    def test_large_source_produces_bounded_feedback(self):
        text = ("repos_uuid " + "x" * 1000 + "\n") * 1000
        index, feedback = focused_feedback(
            snapshot(text),
            [edit("repos_uuid.absent()", "replacement")],
            "repair",
        )
        self.assertEqual(index, 0)
        self.assertLess(len(feedback), 16000)
        self.assertIn("TRUNCATED", feedback)


if __name__ == "__main__":
    unittest.main()
