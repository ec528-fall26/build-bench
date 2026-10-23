"""An unchanged leading error does not alone establish stalled progress."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
))

from src.contracts import BuildResult, Diagnostic, ModelTurn
from src.loop import run_loop
from src.types import CaseContext, Edit


def build(count, stage="test"):
    diagnostics = [
        Diagnostic(
            message=f"error {index}",
            stage=stage,
            log_path="example.c",
            line=175 + index,
            resolved=["input/example.c"],
            source_line=None,
            log_offset=index,
        )
        for index in range(count)
    ]
    return BuildResult(
        succeeded=False,
        stage_reached=stage,
        diagnostics=diagnostics,
        duration_seconds=0,
    )


class RepeatedDiagnosticProgressTests(unittest.TestCase):
    def run_case(self, first, second):
        with tempfile.TemporaryDirectory() as temporary:
            worktree = Path(temporary)
            (worktree / "input").mkdir()
            source = worktree / "input/example.c"
            source.write_text("alpha\n")

            context = CaseContext(
                case_id="progress-test",
                worktree=worktree,
                log_tail="",
                task_metadata={},
            )
            proposals = iter([
                ModelTurn(
                    action="edit", tool_requests=[],
                    hypothesis="First repair.", evidence=[],
                    edits=[Edit("input/example.c", "alpha", "beta")],
                    reason="",
                ),
                ModelTurn(
                    action="edit", tool_requests=[],
                    hypothesis="Second repair.", evidence=[],
                    edits=[Edit("input/example.c", "beta", "gamma")],
                    reason="",
                ),
                ModelTurn(
                    action="stop", tool_requests=[],
                    hypothesis="", evidence=[], edits=[],
                    reason="Script complete.",
                ),
            ])
            seen = []

            def next_turn(request):
                seen.append(request)
                return next(proposals), {
                    "input_tokens": 1, "output_tokens": 1
                }

            class Builder:
                def __init__(self):
                    self.results = iter([first, second])
                    self.calls = 0

                def request_build(self, candidate):
                    self.calls += 1
                    return next(self.results)

            builder = Builder()
            result = run_loop(
                context,
                next_turn=next_turn,
                build_feedback=builder,
            )
            self.assertEqual(builder.calls, 2)
            self.assertEqual(len(result.attempts), 2)
            self.assertEqual([a.applied for a in result.attempts], [1, 1])
            return result, len(seen), source.read_text()

    def test_same_first_error_with_fewer_diagnostics_continues(self):
        result, calls, source = self.run_case(build(3), build(2))
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(calls, 3)
        self.assertEqual(source, "gamma\n")

    def test_same_first_error_without_progress_stops(self):
        result, calls, source = self.run_case(build(3), build(3))
        self.assertEqual(result.termination_reason, "repeated_diagnostic")
        self.assertEqual(calls, 2)
        self.assertIn("without improvement", result.message)

    def test_later_build_stage_counts_as_progress(self):
        result, calls, source = self.run_case(
            build(3, "build"), build(3, "test")
        )
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(calls, 3)

    def test_worse_candidate_is_restored_and_stops(self):
        result, calls, source = self.run_case(build(2), build(3))
        self.assertEqual(result.termination_reason, "repeated_diagnostic")
        self.assertEqual(calls, 2)
        self.assertEqual(source, "beta\n")


if __name__ == "__main__":
    unittest.main()
