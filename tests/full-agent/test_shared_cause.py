"""Shared-cause selection uses current evidence across files."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

AGENT = (
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
)
sys.path.insert(0, str(AGENT))
from src import repair_strategy as strategy


class SharedCauseTests(unittest.TestCase):
    def setUp(self):
        self.initial = [
            NS(resolved=["input/a.rs"]) for _ in range(4)
        ] + [NS(resolved=["input/b.rs"])]
        self.context = NS(log_tail="initial log")
        self.request = NS(
            context=self.context, current_build=None, latest_attempt=None
        )

    def test_configuration_is_opt_in(self):
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "shared-cause"}):
            self.assertEqual(strategy.configured_strategy(), "shared-cause")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(strategy.configured_strategy(), "default")

    def test_shared_cause_keeps_diagnostics_across_files(self):
        context, diagnostics, log, total = strategy.opening_inputs(
            self.request, self.initial, "initial.log", strategy="shared-cause"
        )
        self.assertEqual(diagnostics, self.initial)
        self.assertEqual(total, 5)
        self.assertEqual(log, "initial.log")
        self.assertIs(context, self.context)

    def test_small_group_selection_is_unchanged(self):
        _, diagnostics, _, total = strategy.opening_inputs(
            self.request, self.initial, "initial.log"
        )
        self.assertEqual(diagnostics, self.initial[:3])
        self.assertEqual(total, 5)

    def test_retained_build_replaces_initial_evidence(self):
        current = [NS(resolved=["input/c.rs"]), NS(resolved=["input/d.rs"])]
        self.request.current_build = NS(diagnostics=current)
        context, diagnostics, log, total = strategy.opening_inputs(
            self.request, self.initial, "initial.log", strategy="shared-cause"
        )
        self.assertEqual(diagnostics, current)
        self.assertEqual(total, 2)
        self.assertIsNone(log)
        self.assertEqual(context.log_tail, "")
        self.assertEqual(self.context.log_tail, "initial log")

    def test_historical_log_is_not_reused_after_unverified_edits(self):
        self.request.latest_attempt = NS()
        context, diagnostics, log, _ = strategy.opening_inputs(
            self.request, self.initial, "initial.log", strategy="shared-cause"
        )
        self.assertEqual(diagnostics, self.initial)
        self.assertIsNone(log)
        self.assertEqual(context.log_tail, "")


if __name__ == "__main__":
    unittest.main()
