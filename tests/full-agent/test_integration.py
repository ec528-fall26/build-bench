"""Cross-package checks (campaign 9 November): recipes x gates.

A macro-expanded symbols batch must be accepted by the symbols grammar, the
edit-size budget and the syntax guard; the loop must pass the missing-symbol
count and the target architecture on. No model, network or compiler.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent -p test_integration.py
"""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import edit_preflight, edit_repair, patterns, syntax_guard  # noqa: E402
from src.candidates import CandidateSnapshot  # noqa: E402
from src.recipes import registry  # noqa: E402
from src.types import Edit  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "recipes"


class SymbolsMacroThroughTheGates(unittest.TestCase):
    def run_case(self, case):
        root = FIX / "trees" / case
        if not root.is_dir():
            self.skipTest("fixture tree absent")
        log = (FIX / "logs" / f"{case}.log").read_text(encoding="utf-8").splitlines()
        path = sorted(str(p.relative_to(root)) for p in (root / "input/debian").glob("*.symbols*"))[0]
        edits, notes = edit_repair.expand_macros([Edit(path, registry.macro_token("symbols-tag"), "")],
                                                 root, log, "amd64")
        self.assertTrue(edits, notes)
        snapshot = CandidateSnapshot.capture(root, [e.path for e in edits])
        missing = len(patterns.missing_symbols(log))
        self.assertGreater(missing, 0)
        self.assertEqual(edit_preflight.inspect_batch(snapshot, edits, missing_symbol_count=missing), [])
        self.assertIsNone(syntax_guard.check_batch(snapshot, edits, worktree=root, seconds=2,
                                                   target_arch="amd64"))
        after = edit_repair.simulate(snapshot, edits)[path]
        self.assertEqual(syntax_guard.symbols_lint(after), [])

    def test_libbpp_qt(self):
        self.run_case("libbpp-qt")

    def test_libbpp_core(self):
        self.run_case("libbpp-core")


class LoopWiring(unittest.TestCase):
    def test_the_loop_passes_the_budget_inputs(self):
        text = (ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent/src/loop.py").read_text()
        self.assertIn("missing_symbol_count=missing", text)
        self.assertIn("target_arch=str(", text)


if __name__ == "__main__":
    unittest.main()
