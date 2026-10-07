"""Baseline contract for Part 1's run_repair.

The one-shot agent is the project's naive comparison: log tail only, one model
call, no history. These tests pin that definition. If someone later makes the
baseline smarter — a second call, a longer or filtered log — a test fails
instead of the baseline quietly getting stronger between demos.

Run from the repository root:  python3 -m unittest discover -s tests/one-shot/part1-agent-shell
"""

import logging
from pathlib import Path
import sys
import tempfile
import unittest


AGENT = Path(__file__).resolve().parents[3] / "buildbench-starter-kit-0.1.0-rc.2/agents/one-shot"
sys.path.insert(0, str(AGENT))

from src.log_tail import extract_tail  # noqa: E402
from src.main import run_repair  # noqa: E402
from src.types import CaseContext, Edit, EditPlan  # noqa: E402

# Expected rejections are asserted on, not printed.
logging.getLogger("src.edit_applier").addHandler(logging.NullHandler())


TAIL_LINES = 500


class RecordingModel:
    def __init__(self, edits=()):
        self.edits = list(edits)
        self.calls = []

    def propose(self, context):
        self.calls.append(context.log_tail)
        return EditPlan(self.edits, "test", {"input_tokens": None, "output_tokens": None})


class BaselineContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.worktree = root / "work"
        (self.worktree / "input").mkdir(parents=True)
        (self.worktree / "input" / "a.c").write_text("int x;\n")
        self.log = root / "build.log"
        self.log.write_text("".join(f"line {i}\n" for i in range(1, 1001)))

    def run_baseline(self, model, tail=extract_tail, policy=None):
        context = CaseContext("case", self.worktree, "", {})
        return run_repair(context, self.log, tail, model, patch_policy=policy)

    def test_model_is_called_exactly_once(self):
        model = RecordingModel()
        self.run_baseline(model)
        self.assertEqual(len(model.calls), 1)

    def test_model_sees_exactly_the_last_500_lines(self):
        model = RecordingModel()
        self.run_baseline(model)
        seen = model.calls[0].splitlines()
        self.assertEqual(len(seen), TAIL_LINES)
        self.assertEqual((seen[0], seen[-1]), ("line 501", "line 1000"))

    def test_log_is_passed_through_unfiltered(self):
        model = RecordingModel()
        self.run_baseline(model)
        self.assertEqual(model.calls[0], extract_tail(self.log, TAIL_LINES))

    def test_tail_is_requested_once_with_500_lines(self):
        requests = []

        def spy(path, n_lines):
            requests.append((path, n_lines))
            return extract_tail(path, n_lines)

        self.run_baseline(RecordingModel(), tail=spy)
        self.assertEqual(requests, [(self.log, TAIL_LINES)])

    def test_edits_are_applied_and_counted(self):
        model = RecordingModel([Edit("input/a.c", "int x;", "long x;")])
        plan, applied = self.run_baseline(model)
        self.assertEqual(applied, 1)
        self.assertIs(plan.edits, model.edits)
        self.assertEqual((self.worktree / "input" / "a.c").read_text(), "long x;\n")

    def test_case_patch_policy_is_enforced(self):
        policy = {"allowed_paths": ["input/src/**"], "forbidden_paths": []}
        model = RecordingModel([Edit("input/a.c", "int x;", "long x;")])
        _, applied = self.run_baseline(model, policy=policy)
        self.assertEqual(applied, 0)
        self.assertEqual((self.worktree / "input" / "a.c").read_text(), "int x;\n")


if __name__ == "__main__":
    unittest.main()
