"""Tests for restoring the worktree before a repair attempt."""

from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
sys.path.insert(0, str(AGENT))

from src.candidates import CandidateSnapshot
from src.edit_applier import apply_edits
from src.types import Edit


class CandidateSnapshots(unittest.TestCase):
    def test_restore_preserves_exact_bytes_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            (worktree / "input").mkdir()
            source = worktree / "input/example.c"

            # Include CRLF and no final newline to test exact restoration.
            original = b"first\r\nlast"
            source.write_bytes(original)
            source.chmod(0o755)

            snapshot = CandidateSnapshot.capture(
                worktree, ["input/example.c"]
            )

            source.write_bytes(b"changed\n")
            source.chmod(0o644)
            snapshot.restore()

            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(
                stat.S_IMODE(source.stat().st_mode), 0o755
            )
            self.assertEqual(
                sorted(p.name for p in source.parent.iterdir()),
                ["example.c"],
            )

    def test_restore_undoes_partially_accepted_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            (worktree / "input").mkdir()

            first = worktree / "input/first.c"
            second = worktree / "input/second.c"
            first.write_bytes(b"alpha beta\n")
            second.write_bytes(b"unchanged\n")

            snapshot = CandidateSnapshot.capture(
                worktree,
                ["input/first.c", "input/second.c"],
            )

            applied = apply_edits(
                worktree,
                [
                    Edit("input/first.c", "alpha", "beta"),
                    Edit("input/second.c", "missing text", "replacement"),
                ],
            )

            self.assertEqual(applied, 1)
            self.assertEqual(first.read_bytes(), b"beta beta\n")

            snapshot.restore()

            self.assertEqual(first.read_bytes(), b"alpha beta\n")
            self.assertEqual(second.read_bytes(), b"unchanged\n")

    def test_restore_rejects_symlink_before_writing_any_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "repo"
            (worktree / "input").mkdir(parents=True)

            first = worktree / "input/first.c"
            second = worktree / "input/second.c"
            outside = root / "outside.c"

            first.write_bytes(b"original first\n")
            second.write_bytes(b"original second\n")
            outside.write_bytes(b"outside unchanged\n")

            snapshot = CandidateSnapshot.capture(
                worktree,
                ["input/first.c", "input/second.c"],
            )

            first.write_bytes(b"candidate first\n")
            second.unlink()
            second.symlink_to(outside)

            with self.assertRaises((OSError, ValueError)):
                snapshot.restore()

            # Preflight should reject the second target before restoring first.
            self.assertEqual(first.read_bytes(), b"candidate first\n")
            self.assertTrue(second.is_symlink())
            self.assertEqual(outside.read_bytes(), b"outside unchanged\n")


if __name__ == "__main__":
    unittest.main()
