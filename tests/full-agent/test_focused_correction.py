"""Focused corrections retain order, do not write early, and are bounded."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import tempfile
import unittest

sys.path.insert(0, str(
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
))

from src.contracts import AgentConfig, Budget
from src.loop import run_loop, LoopRequest
from src.model_adapter import ModelAdapter
from src.edit_preflight import FOCUSED_PREFIX


def edit(old, new):
    return SimpleNamespace(path="input/example.rs", old_text=old, new_text=new)


def turn(edits):
    return SimpleNamespace(
        action="edit", edits=edits, hypothesis="repair",
        evidence=[], reason="", tool_requests=[],
    )


class FocusedCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input").mkdir()
        self.path = self.root / "input/example.rs"
        self.original = "alpha\nfirst repeat\nsecond repeat\n"
        self.path.write_text(self.original)
        self.context = SimpleNamespace(worktree=self.root)

    def run_script(self, proposals):
        self.requests = []
        proposals = iter(proposals)

        def next_turn(request):
            self.requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}

        return run_loop(
            self.context, next_turn=next_turn,
            config=AgentConfig(use_build_feedback=False),
        )

    def stop(self):
        return SimpleNamespace(
            action="stop", reason="done", edits=[], tool_requests=[]
        )

    def test_valid_edits_retained_when_one_item_is_corrected(self):
        result = self.run_script([
            turn([edit("alpha", "gamma"), edit("repeat", "replacement")]),
            turn([edit("first repeat", "first replacement")]),
            self.stop(),
        ])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].applied, 2)
        self.assertEqual(
            self.path.read_text(), "gamma\nfirst replacement\nsecond repeat\n"
        )
        self.assertIn("FOCUSED INDEX: 1", self.requests[1].edit_validation_feedback)
        self.assertEqual(self.requests[1].history, [])

    def test_explicit_removal_of_duplicate_keeps_first_edit(self):
        result = self.run_script([
            turn([edit("alpha", "gamma"), edit("alpha", "gamma")]),
            turn([]),
            self.stop(),
        ])
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertEqual(self.path.read_text(), self.original.replace("alpha", "gamma"))

    def test_later_edit_revalidated_after_correction(self):
        result = self.run_script([
            turn([
                edit("absent", "gamma"),
                edit("gamma", "delta"),
            ]),
            turn([edit("alpha", "gamma")]),
            self.stop(),
        ])
        self.assertEqual(result.attempts[0].applied, 2)
        self.assertTrue(self.path.read_text().startswith("delta\n"))

    def test_multiple_correction_edits_do_not_replace_pending_batch(self):
        result = self.run_script([
            turn([edit("alpha", "gamma"), edit("repeat", "replacement")]),
            turn([edit("alpha", "WRONG"), edit("first repeat", "WRONG")]),
            turn([edit("first repeat", "first replacement")]),
            self.stop(),
        ])
        self.assertEqual(result.attempts[0].applied, 2)
        self.assertNotIn("WRONG", self.path.read_text())

    def test_correction_limit_leaves_files_unchanged(self):
        result = self.run_script(
            [turn([edit("absent", "new")])]
            + [turn([edit("still absent", "new")]) for _ in range(12)]
        )
        self.assertEqual(result.termination_reason, "edit_correction_budget")
        self.assertEqual(result.attempts, [])
        self.assertEqual(self.path.read_text(), self.original)

    def test_no_write_before_correction_and_only_one_build_after(self):
        calls = 0
        builds = []

        def next_turn(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return turn([
                    edit("alpha", "gamma"), edit("repeat", "replacement")
                ]), {}
            self.assertEqual(self.path.read_text(), self.original)
            return turn([edit("first repeat", "first replacement")]), {}

        def build(worktree):
            builds.append(self.path.read_text())
            return SimpleNamespace(
                succeeded=True, stage_reached="test",
                diagnostics=[], duration_seconds=0,
            )

        result = run_loop(
            self.context, next_turn=next_turn,
            build_feedback=SimpleNamespace(request_build=build),
        )
        self.assertEqual(result.termination_reason, "build_succeeded")
        self.assertEqual(builds, ["gamma\nfirst replacement\nsecond repeat\n"])

    def test_correction_prompt_omits_full_source_and_disables_tools(self):
        sent = []

        def complete(messages):
            sent.append(messages)
            return (
                '{"action":"tool","requests":[{"tool":"list_dir","args":{}}]}',
                "stop", {"input_tokens": 1, "output_tokens": 1},
            )

        adapter = ModelAdapter(
            complete, self.context, config=AgentConfig(), budget=Budget(),
            diagnostics=[], log_path=None,
        )
        self.addCleanup(adapter.close)
        request = LoopRequest(
            context=self.context,
            edit_validation_feedback=FOCUSED_PREFIX + "\ncorrect INDEX 0",
        )
        with patch("src.model_adapter.prompts.opening_message") as opening:
            result, usage = adapter(request)
        opening.assert_not_called()
        self.assertEqual(result.action, "malformed")
        self.assertEqual(len(sent), 1)
        self.assertFalse(adapter.turns[0]["follow_up"])


if __name__ == "__main__":
    unittest.main()
