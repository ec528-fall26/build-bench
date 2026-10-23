"""Missing-prefix correction preserves pending edits; no model calls."""

from pathlib import Path
from types import SimpleNamespace as NS
import sys
import tempfile
import unittest

AGENT = (
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
)
sys.path.insert(0, str(AGENT))

from src.contracts import AgentConfig
from src.loop import run_loop
from src.path_feedback import missing_prefix_feedback


def edit(path, old="alpha", new="gamma"):
    return NS(path=path, old_text=old, new_text=new)


def action(kind, edits=None):
    return NS(
        action=kind, edits=edits or [], hypothesis="test",
        reason="finished", tool_requests=[],
    )


class PathFeedbackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "input/src").mkdir(parents=True)
        self.file = self.root / "input/src/client.rs"
        self.file.write_text("alpha\nbeta\n", encoding="utf-8")

    def test_missing_prefix_suggests_existing_file_without_changes(self):
        item = edit("src/client.rs")
        index, feedback = missing_prefix_feedback(self.root, [item])
        self.assertEqual(index, 0)
        self.assertIn("Submitted path: 'src/client.rs'", feedback)
        self.assertIn(
            "Existing file passing path and policy checks: 'input/src/client.rs'",
            feedback,
        )
        self.assertEqual(item.path, "src/client.rs")
        self.assertEqual(self.file.read_text(), "alpha\nbeta\n")

    def test_no_suggestion_for_missing_forbidden_or_traversal_path(self):
        cases = [
            ("src/missing.rs", None),
            ("../secret", None),
            ("src/client.rs", {
                "allowed_paths": ["input/**"],
                "forbidden_paths": ["input/src/client.rs"],
            }),
        ]
        for path, policy in cases:
            with self.subTest(path=path, policy=policy):
                _, feedback = missing_prefix_feedback(
                    self.root, [edit(path)], patch_policy=policy
                )
                self.assertNotIn("Existing file passing", feedback)

    def test_symlink_counterpart_is_not_suggested(self):
        (self.root / "input/src/link.rs").symlink_to(self.file)
        _, feedback = missing_prefix_feedback(
            self.root, [edit("src/link.rs")]
        )
        self.assertNotIn("Existing file passing", feedback)

    def test_corrected_path_preserves_other_pending_edits(self):
        proposals = iter([
            action("edit", [
                edit("src/client.rs"),
                edit("input/src/client.rs", "beta", "delta"),
            ]),
            action("edit", [edit("input/src/client.rs")]),
            action("stop"),
        ])
        calls = []

        def next_turn(request):
            calls.append(request)
            if len(calls) == 2:
                self.assertIn("FOCUSED INDEX: 0", request.edit_validation_feedback)
                self.assertIn("input/src/client.rs", request.edit_validation_feedback)
                self.assertEqual(self.file.read_text(), "alpha\nbeta\n")
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}

        result = run_loop(
            NS(worktree=self.root),
            next_turn=next_turn,
            config=AgentConfig(use_build_feedback=False),
        )
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].applied, 2)
        self.assertEqual(self.file.read_text(), "gamma\ndelta\n")


if __name__ == "__main__":
    unittest.main()
