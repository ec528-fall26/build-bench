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
    / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"
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
            config=AgentConfig(use_no_build_control=False, use_build_feedback=False),
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

    def test_unfixable_single_item_discards_the_batch_and_continues(self):
        # A correction that repeats an already rejected old_text cannot succeed,
        # so the item is dropped at once; with nothing left, the batch is over
        # (it costs an attempt) and the model may propose another.
        result = self.run_script([
            turn([edit("absent", "new")]),
            turn([edit("still absent", "new")]),
            turn([edit("still absent", "new")]),
            turn([edit("alpha", "gamma")]),
            self.stop(),
        ])
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual([a.applied for a in result.attempts], [0, 1])
        self.assertEqual(self.path.read_text(), self.original.replace("alpha", "gamma"))
        self.assertIn("No edit of that batch could be applied", self.requests[3].feedback)
        self.assertEqual(self.requests[3].edit_validation_feedback, "")

    def test_correction_budget_applies_the_rest_of_the_batch(self):
        # rust-subversion, 2 November: 26 edits, one unfixable, none written.
        corrections = [turn([edit(f"absent {n}", "new")]) for n in range(12)]
        result = self.run_script(
            [turn([edit("alpha", "gamma"), edit("absent", "new"),
                   edit("second repeat", "second replacement")])]
            + corrections + [self.stop()]
        )
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].applied, 2)
        self.assertEqual(self.path.read_text(), "gamma\nfirst repeat\nsecond replacement\n")
        self.assertIn("the correction budget was spent on it", self.requests[-1].feedback)
        self.assertIn("Accepted 2 of 2", self.requests[-1].feedback)

    def test_an_edit_an_earlier_one_already_made_is_dropped(self):
        # rust-subversion, 2 Nov: replace_all "*const i8" made every
        # "as *const i8" edit unmatched, and 12 corrections could not fix it.
        self.path.write_text("a as *const i8;\nfn f(p: *const i8) {}\n")
        broad = SimpleNamespace(path="input/example.rs", old_text="*const i8",
                                new_text="*const u8", replace_all=True)
        narrow = SimpleNamespace(path="input/example.rs", old_text="as *const i8",
                                 new_text="as *const u8", replace_all=True)
        result = self.run_script([turn([broad, narrow]), self.stop()])
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertEqual(self.path.read_text(), "a as *const u8;\nfn f(p: *const u8) {}\n")
        self.assertEqual(self.requests[1].edit_validation_feedback, "")
        self.assertIn("an earlier edit in the batch had already made its change",
                      self.requests[1].feedback)

    def test_dropped_when_every_broad_match_is_also_a_narrow_one(self):
        # delta.rs: all 13 "*const i8" were "as *const i8".
        self.path.write_text("a as *const i8;\nb as *const i8;\n")
        broad = SimpleNamespace(path="input/example.rs", old_text="*const i8",
                                new_text="*const u8", replace_all=True)
        narrow = SimpleNamespace(path="input/example.rs", old_text="as *const i8",
                                 new_text="as *const u8", replace_all=True)
        result = self.run_script([turn([broad, narrow]), self.stop()])
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertEqual(self.path.read_text(), "a as *const u8;\nb as *const u8;\n")

    def test_an_edit_with_a_different_change_is_not_dropped(self):
        self.path.write_text("a as *const i8;\n")
        broad = SimpleNamespace(path="input/example.rs", old_text="*const i8",
                                new_text="*const u8", replace_all=True)
        other = SimpleNamespace(path="input/example.rs", old_text="as *const i8",
                                new_text="as *const c_char", replace_all=False)
        self.run_script([turn([broad, other]), self.stop()])
        feedback = self.requests[1].edit_validation_feedback
        self.assertIn("FOCUSED INDEX: 1", feedback)
        self.assertIn("IS in the file on disk", feedback)

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

    def test_correction_prompt_omits_full_source_and_allows_reading(self):
        sent = []

        def complete(messages):
            sent.append(messages)
            return (
                '{"action":"tool","requests":[{"tool":"list_dir","args":{}}]}',
                "stop", {"input_tokens": 1, "output_tokens": 1},
            )

        adapter = ModelAdapter(
            complete, self.context, config=AgentConfig(use_no_build_control=False), budget=Budget(),
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
        # Reading the file is how a correction finds the text to copy.
        self.assertEqual(result.action, "tool")
        self.assertIn("read_file", sent[0][0]["content"])
        self.assertEqual(len(sent), 1)
        self.assertFalse(adapter.turns[0]["follow_up"])
        self.assertEqual(adapter._investigating, 0)

    def test_correction_tools_are_refused_when_tools_are_off(self):
        def complete(messages):
            return (
                '{"action":"tool","requests":[{"tool":"list_dir","args":{}}]}',
                "stop", {"input_tokens": 1, "output_tokens": 1},
            )

        adapter = ModelAdapter(
            complete, self.context, config=AgentConfig(use_no_build_control=False, use_tools=False), budget=Budget(),
            diagnostics=[], log_path=None,
        )
        self.addCleanup(adapter.close)
        request = LoopRequest(
            context=self.context,
            edit_validation_feedback=FOCUSED_PREFIX + "\ncorrect INDEX 0",
        )
        result, _ = adapter(request)
        self.assertEqual(result.action, "malformed")


if __name__ == "__main__":
    unittest.main()
