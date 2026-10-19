"""Individual edit feedback: offline tests, without model calls."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

AGENT = (
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
)
sys.path.insert(0, str(AGENT))

from src.model_adapter import _edit_outcomes


class EditOutcomeTests(unittest.TestCase):
    def attempt(self, applied):
        return SimpleNamespace(
            applied=applied,
            edits=[
                SimpleNamespace(
                    path="input/src/client.rs",
                    old_text=f"old {number}",
                    new_text=f"new {number}",
                )
                for number in range(4)
            ],
        )

    def rejections(self):
        return [
            "edit_rejected index=1 path='input/src/client.rs' "
            "reason=old_text_ambiguous",
            "edit_rejected index=2 path='input/src/client.rs' "
            "reason=old_text_ambiguous",
        ]

    def test_partial_batch_identifies_each_outcome(self):
        text = _edit_outcomes(
            self.attempt(2), self.rejections(), undone=False
        )
        self.assertIn("Edit 1: APPLIED\n", text)
        self.assertIn("Edit 2: REJECTED (old_text_ambiguous)", text)
        self.assertIn("Edit 3: REJECTED (old_text_ambiguous)", text)
        self.assertIn("Edit 4: APPLIED\n", text)
        self.assertIn('old_text: "old 0"', text)
        self.assertIn('new_text: "new 0"', text)

    def test_missing_rejection_records_do_not_imply_success(self):
        text = _edit_outcomes(self.attempt(2), [], undone=False)
        self.assertEqual(text.count(": UNKNOWN"), 4)
        self.assertNotIn(": APPLIED", text)

    def test_zero_applied_without_reasons_is_not_success(self):
        text = _edit_outcomes(self.attempt(0), [], undone=False)
        self.assertEqual(text.count(": NOT_APPLIED"), 4)

    def test_restored_batch_marks_successful_edits_undone(self):
        text = _edit_outcomes(
            self.attempt(2), self.rejections(), undone=True
        )
        self.assertIn("Edit 1: APPLIED_THEN_UNDONE", text)
        self.assertIn("Edit 4: APPLIED_THEN_UNDONE", text)
        self.assertIn("Edit 2: REJECTED", text)

    def test_wrong_path_prevents_inference(self):
        rejections = [
            message.replace("client.rs", "other.rs")
            for message in self.rejections()
        ]
        text = _edit_outcomes(
            self.attempt(2), rejections, undone=False
        )
        self.assertEqual(text.count(": UNKNOWN"), 4)


if __name__ == "__main__":
    unittest.main()