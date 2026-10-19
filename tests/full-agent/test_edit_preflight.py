"""Batch validation and delivery of correction feedback; no network."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import tempfile
import unittest

AGENT = (
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
)
sys.path.insert(0, str(AGENT))

from src.candidates import CandidateSnapshot
from src.contracts import AgentConfig, Budget
from src.edit_preflight import validate_batch
from src.loop import LoopRequest, run_loop
from src.model_adapter import ModelAdapter


def edit(old, new, path="input/example.rs"):
    return SimpleNamespace(path=path, old_text=old, new_text=new)


def action(kind, edits=None):
    return SimpleNamespace(
        action=kind, edits=edits or [], hypothesis="test",
        reason="finished", tool_requests=[],
    )


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "input").mkdir()
        self.path = self.root / "input/example.rs"
        self.path.write_text("alpha\nbeta\n", encoding="utf-8")

    def check(self, edits):
        snapshot = CandidateSnapshot.capture(
            self.root, [item.path for item in edits]
        )
        return validate_batch(snapshot, edits)

    def test_valid_batch_is_read_only(self):
        self.assertEqual(self.check([edit("alpha", "gamma")]), "")
        self.assertEqual(self.path.read_text(), "alpha\nbeta\n")

    def test_sequential_dependencies_are_valid(self):
        self.assertEqual(self.check([
            edit("alpha", "gamma"), edit("gamma", "delta")
        ]), "")

    def test_duplicate_missing_text_is_identified(self):
        result = self.check([
            edit("alpha", "gamma"), edit("alpha", "gamma")
        ])
        self.assertIn("INDEX 1", result)
        self.assertIn("repeats INDEX 0", result)
        self.assertIn("old_text_not_found", result)
        self.assertEqual(self.path.read_text(), "alpha\nbeta\n")

    def test_legitimate_repeated_edit_after_reversal_is_not_removed(self):
        self.assertEqual(self.check([
            edit("alpha", "gamma"),
            edit("gamma", "alpha"),
            edit("alpha", "gamma"),
        ]), "")

    def test_overlapping_matches_are_ambiguous(self):
        self.path.write_text("aaa\n")
        result = self.check([edit("aa", "bb")])
        self.assertIn("old_text_ambiguous", result)
        self.assertIn("Matching source locations", result)
        self.assertEqual(self.path.read_text(), "aaa\n")

    def test_missing_and_no_change_are_reported(self):
        result = self.check([
            edit("absent", "new"), edit("alpha", "alpha")
        ])
        self.assertIn("old_text_not_found", result)
        self.assertIn("no_change", result)

    def test_build_policy_is_checked_before_writing(self):
        rules = self.root / "input/debian/rules"
        rules.parent.mkdir()
        rules.write_text("override_dh_auto_test:\n\tdh_auto_test\n")
        result = self.check([
            edit("dh_auto_test\n", "true\n", "input/debian/rules")
        ])
        self.assertIn("test_invocation_removed", result)
        self.assertIn("\tdh_auto_test\n", rules.read_text())

    def test_invalid_batch_then_correction_uses_one_attempt(self):
        requests = []
        proposals = iter([
            action("edit", [edit("alpha", "gamma"), edit("absent", "new")]),
            action("edit", [edit("alpha", "gamma")]),
            action("stop"),
        ])

        def next_turn(request):
            requests.append(request)
            if len(requests) == 2:
                self.assertEqual(self.path.read_text(), "alpha\nbeta\n")
                self.assertIn("old_text_not_found", request.edit_validation_feedback)
                self.assertIsNone(request.latest_attempt)
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}

        builder = SimpleNamespace(request_build=lambda root: self.fail(
            "Builds are disabled for this test."
        ))
        result = run_loop(
            SimpleNamespace(worktree=self.root),
            next_turn=next_turn,
            config=AgentConfig(use_build_feedback=False),
            build_feedback=builder,
        )
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertEqual(self.path.read_text(), "gamma\nbeta\n")
        self.assertEqual(requests[-1].edit_validation_feedback, "")

    def test_invalid_retries_are_bounded_by_turn_limit(self):
        def next_turn(request):
            return action("edit", [edit("absent", "new")]), {
                "input_tokens": 1, "output_tokens": 1
            }

        with patch("src.loop.apply_edits") as apply:
            result = run_loop(
                SimpleNamespace(worktree=self.root),
                next_turn=next_turn,
                max_turns=2,
            )
        apply.assert_not_called()
        self.assertEqual(result.termination_reason, "turn_budget")
        self.assertEqual(result.attempts, [])
        self.assertEqual(result.usage["input_tokens"], 2)

    def test_adapter_sends_and_records_validation_feedback(self):
        sent = []

        def complete(messages):
            sent.extend(messages)
            return (
                '{"action":"stop","reason":"test complete"}',
                "stop",
                {"input_tokens": 1, "output_tokens": 1},
            )

        context = SimpleNamespace(worktree=self.root)
        adapter = ModelAdapter(
            complete, context, config=AgentConfig(), budget=Budget(),
            diagnostics=[], log_path=None,
        )
        self.addCleanup(adapter.close)
        request = LoopRequest(
            context=context,
            edit_validation_feedback="VALIDATION_SENTINEL",
        )
        with (
            patch("src.model_adapter.prompts.opening_message", return_value=""),
            patch("src.model_adapter.prompts.system_prompt", return_value=""),
            patch("src.model_adapter.prompts.budget_note", return_value=""),
        ):
            adapter(request)

        self.assertTrue(any(
            "VALIDATION_SENTINEL" in message["content"] for message in sent
        ))
        self.assertEqual(
            adapter.turns[0]["edit_validation_feedback"],
            "VALIDATION_SENTINEL",
        )


if __name__ == "__main__":
    unittest.main()
