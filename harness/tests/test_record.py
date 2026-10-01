"""Recorder checks against real rcran validator results; no Docker or network.

Run from the repository root:  python3 -m unittest discover -s harness/tests -t .
"""

import json
from pathlib import Path
import tempfile
import unittest

from harness.record import append_jsonl, load_outcome, outcome_row


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class RealResultTests(unittest.TestCase):
    """Fixtures are verbatim build-result.json files from the 1 October runs."""

    def test_verified_repair_counts_as_repaired(self):
        o = load_outcome(FIXTURES / "fix-1")
        self.assertEqual(o.status, "succeeded")
        self.assertTrue(o.repaired)
        self.assertEqual(o.build_exit_code, 0)
        self.assertEqual(o.duration_seconds, 87.0)
        self.assertIn("r-cran-digest_0.6.32-1_arm64.deb", o.binary_artifacts)

    def test_patch_applied_false_does_not_block_repair(self):
        raw = json.loads((FIXTURES / "fix-1" / "build-result.json").read_text())
        self.assertIs(raw["patch_applied"], False)
        self.assertTrue(load_outcome(FIXTURES / "fix-1").repaired)

    def test_unrepaired_case_fails(self):
        o = load_outcome(FIXTURES / "baseline-1")
        self.assertEqual(o.status, "failed")
        self.assertFalse(o.repaired)
        self.assertEqual(o.binary_artifacts, ())
        self.assertIn("was not matched", o.message)

    def test_round_trip_matches_baseline(self):
        base = load_outcome(FIXTURES / "baseline-1")
        trip = load_outcome(FIXTURES / "roundtrip-1")
        self.assertEqual((base.status, base.message), (trip.status, trip.message))
        self.assertFalse(trip.repaired)


class EdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def write(self, text):
        (self.dir / "build-result.json").write_text(text)

    def test_success_without_artifact_validation_is_not_repaired(self):
        self.write(json.dumps({"status": "succeeded", "artifact_validation_passed": False}))
        o = load_outcome(self.dir)
        self.assertEqual(o.status, "succeeded")
        self.assertFalse(o.repaired)

    def test_missing_result_is_an_outcome_not_an_exception(self):
        o = load_outcome(self.dir / "never-ran")
        self.assertEqual(o.status, "missing_result")
        self.assertFalse(o.repaired)

    def test_malformed_json_is_an_outcome(self):
        self.write("{not json")
        self.assertEqual(load_outcome(self.dir).status, "invalid_result")

    def test_non_object_json_is_an_outcome(self):
        self.write("[1, 2, 3]")
        self.assertEqual(load_outcome(self.dir).status, "invalid_result")

    def test_bool_exit_code_is_not_mistaken_for_an_integer(self):
        self.write(json.dumps({"status": "failed", "build_exit_code": True}))
        self.assertIsNone(load_outcome(self.dir).build_exit_code)

    def test_append_jsonl_writes_one_parseable_line_per_run(self):
        log = self.dir / "out" / "runs.jsonl"
        for name in ("baseline-1", "fix-1"):
            append_jsonl(log, outcome_row(load_outcome(FIXTURES / name)))
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([r["repaired"] for r in rows], [False, True])


if __name__ == "__main__":
    unittest.main()
