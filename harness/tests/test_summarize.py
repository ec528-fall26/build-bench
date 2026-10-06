"""Token totals in harness.summarize.

The token counts are the real ones from the three live rcran runs of 3 October
(docs/part5-findings.md section 9). Unknown usage must be reported as unknown,
never counted as zero: the proposal's token targets depend on it.

Run from the repository root:  python3 -m unittest discover -s harness/tests -t .
"""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from harness.summarize import main, run_tokens, summarize


RCRAN = "launchpad-mantic-amd64-arm64-r-cran-digest-7a42effc961f"


def run(run_id, tokens_in, tokens_out, repaired=True, case=RCRAN, fix_type="build_commands"):
    return {"run_id": run_id, "case_id": case, "repaired": repaired, "fix_type": fix_type,
            "agent_version": "1.0.0+16e31a424d0d",
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out}}


LIVE = [run("live-1", 11096, 1241), run("live-2", 11096, 1211), run("live-3", 11096, 1594)]
NO_REPLY = run("no-reply", None, None, repaired=False, fix_type="none")


class Totals(unittest.TestCase):
    def test_real_live_runs(self):
        s = summarize(LIVE)
        self.assertEqual((s["input_tokens"], s["output_tokens"], s["tokens"]), (33288, 4046, 37334))
        self.assertEqual(s["median_tokens"], 12337)
        self.assertEqual(s["unknown_tokens"], 0)

    def test_unknown_usage_is_not_counted_as_zero(self):
        s = summarize(LIVE + [NO_REPLY])
        self.assertEqual(s["tokens"], 37334)
        self.assertEqual(s["unknown_tokens"], 1)
        # Counting the unknown run as 0 would drag the median down to 12,322.
        self.assertEqual(s["median_tokens"], 12337)

    def test_partly_known_usage_is_unknown(self):
        self.assertIsNone(run_tokens(run("x", 11096, None)))
        self.assertIsNone(run_tokens({"run_id": "x"}))
        self.assertIsNone(run_tokens({"run_id": "x", "usage": None}))

    def test_true_and_false_are_not_token_counts(self):
        self.assertIsNone(run_tokens(run("x", True, 5)))

    def test_all_unknown(self):
        s = summarize([NO_REPLY])
        self.assertEqual((s["tokens"], s["median_tokens"], s["unknown_tokens"]), (0, None, 1))


class Breakdowns(unittest.TestCase):
    def test_per_case(self):
        rows = LIVE + [run("other-1", 20000, 3000, repaired=False, case="other-case")]
        s = summarize(rows)
        self.assertEqual(list(s["by_case"]), sorted([RCRAN, "other-case"]))
        self.assertEqual((s["by_case"][RCRAN]["runs"], s["by_case"][RCRAN]["repaired"],
                          s["by_case"][RCRAN]["tokens"]), (3, 3, 37334))
        self.assertEqual(s["by_case"]["other-case"]["tokens"], 23000)

    def test_per_fix_type(self):
        s = summarize(LIVE + [NO_REPLY])
        self.assertEqual(s["by_fix_type"]["build_commands"]["tokens"], 37334)
        self.assertEqual(s["by_fix_type"]["none"]["unknown_tokens"], 1)


class Cli(unittest.TestCase):
    def test_printed_report_and_version_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs.jsonl"
            older = {**run("old", 99999, 99999), "agent_version": "0.1.0+b7956fa2035c"}
            runs.write_text("".join(json.dumps(r) + "\n" for r in LIVE + [NO_REPLY, older]))
            with redirect_stdout(io.StringIO()) as out:
                main([str(runs), "--agent-version", "1.0.0+16e31a424d0d"])
        text = out.getvalue()
        self.assertIn("runs: 4   repaired: 3", text)
        self.assertIn("tokens: 33,288 in + 4,046 out = 37,334 over 3 runs (median 12,337 per run); "
                      "unknown for 1", text)
        self.assertIn(RCRAN, text)
        self.assertNotIn("99,999", text)  # the filtered-out older run

    def test_unknown_groups_print_a_dash_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs.jsonl"
            runs.write_text(json.dumps(NO_REPLY) + "\n")
            with redirect_stdout(io.StringIO()) as out:
                main([str(runs)])
        text = out.getvalue()
        self.assertIn("tokens: unknown for the 1 run", text)
        none_row = next(l for l in text.splitlines() if l.startswith("none"))
        self.assertEqual(none_row.split()[3], "-")


if __name__ == "__main__":
    unittest.main()
