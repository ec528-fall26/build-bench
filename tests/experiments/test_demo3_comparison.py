"""Part E: the Demo 3 comparison script, runnable on macOS and in CI.

The harness, the frozen one-shot agent (Parts 1, 2 and 4) and the stand-in model
run for real. dpkg-source and run.sh are replaced by the harness tests' FakeHost;
the real ones are exercised on the ARM64 host. The numbers in the report tests
are synthetic rows written for the test, not results.

Run from the repository root:  python3 -m unittest discover -s tests/experiments
"""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments import demo3_comparison as demo3  # noqa: E402
from experiments.demo3_comparison import BASELINE, CONFIGURATIONS, Case  # noqa: E402
from harness.tests.test_run_case import DSC, FIXTURES, FakeHost  # noqa: E402


REPLAYS = ROOT / "harness" / "replays"
RCRAN = "launchpad-mantic-amd64-arm64-r-cran-digest-7a42effc961f"
MODEL = "openai-compatible:openai.gpt-oss-120b-1:0"
FULL = "0.1.0+aaaaaaaaaaaa"
ON, OFF = CONFIGURATIONS[1], CONFIGURATIONS[2]


def in_process(host, calls=None):
    """Stands in for the child process: the same run_one, with FakeHost's commands."""
    def launch(config, case, rid, log, out, *, replay=None):
        if calls is not None:
            calls.append(rid)
        demo3.run_one(config.agent, case.bundle, rid, out / config.name, log, replay, runner=host)
    return launch


def row(config, case, repeat, repaired, *, tokens=(11_000, 1_200), wall=110.0,
        fix_type="source", reason=None, **extra):
    return {
        "run_id": f"{config.name}--{case}--r{repeat}", "case_id": case, "repaired": repaired,
        "fix_type": fix_type if repaired else "none", "needs_review": repaired and
        fix_type == "build_commands", "model": MODEL, "wall_seconds": wall,
        "agent_version": demo3.FROZEN_BASELINE if config is BASELINE else FULL,
        "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]} if tokens else {},
        "termination_reason": reason or ("build_succeeded" if repaired else "build_failed"),
        **extra}


class Experiment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.out = self.root / "out"
        self.bundle = self.make_bundle("bundle")
        self.case = demo3.find_cases([self.bundle])[0]

    def make_bundle(self, name):
        bundle = self.root / name
        case = bundle / "case"
        (case / "input").mkdir(parents=True)
        (case / "config").mkdir()
        (bundle / "run.sh").write_text("")
        shutil.copy(FIXTURES / "rcran-manifest.json", case / "manifest.json")
        (case / "input" / DSC).write_text("original signed dsc")
        return bundle

    def record(self, rows):
        for item in rows:
            name = item["run_id"].split("--")[0]
            demo3.append_jsonl(self.out / name / "runs.jsonl", item)

    def report(self, **kwargs):
        return demo3.build_report(self.out, **kwargs)


class Running(Experiment):
    def test_every_run_is_recorded_and_the_baseline_is_built_once(self):
        host, said = FakeHost(), []
        demo3.run_experiment([BASELINE], [self.case], 3, self.out,
                             replay=REPLAYS / "rcran-known-fix.json",
                             launch=in_process(host), runner=host, say=said.append)
        rows = demo3.load_rows(self.out / BASELINE.name / "runs.jsonl")
        self.assertEqual([r["run_id"] for r in rows],
                         [f"one-shot-v1--{RCRAN}--r{n}" for n in (1, 2, 3)])
        self.assertTrue(all(r["repaired"] and r["fix_type"] == "source" for r in rows))
        self.assertEqual({r["agent_version"] for r in rows}, {demo3.FROZEN_BASELINE})
        unrepaired_builds = [c for c in host.calls if c[0] == "bash" and "--input" not in c]
        self.assertEqual(len(unrepaired_builds), 1)  # one shared failure log, not one per run
        self.assertEqual(len([c for c in host.calls if "--input" in c]), 3)
        self.assertEqual(len(said), 3)
        self.assertIn("[3/3] one-shot-v1 r3", said[-1])
        self.assertIn("REPAIRED (build_succeeded)", said[-1])

        item = self.report(configs=[BASELINE])["configurations"][0]
        self.assertEqual((item["runs"], item["repaired"], item["repaired_per_repeat"]),
                         (3, 3, {1: 1, 2: 1, 3: 1}))
        self.assertEqual(item["mean_attempts"], 1.0)
        self.assertIsNone(item["median_tokens"])  # the stand-in model reports no usage

    def test_a_second_invocation_runs_only_what_is_missing(self):
        host, calls = FakeHost(), []
        run = dict(replay=REPLAYS / "rcran-comment-only.json", launch=in_process(host, calls),
                   runner=host, say=lambda line: None)
        demo3.run_experiment([BASELINE], [self.case], 2, self.out, **run)
        self.assertEqual(len(calls), 2)
        demo3.run_experiment([BASELINE], [self.case], 3, self.out, **run)
        self.assertEqual(calls[2:], [f"one-shot-v1--{RCRAN}--r3"])
        item = self.report(configs=[BASELINE])["configurations"][0]
        self.assertEqual((item["runs"], item["repaired"]), (3, 0))  # a failed repair is a result
        self.assertEqual(item["not_a_result"], [])
        self.assertEqual(len([c for c in host.calls if c[0] == "bash" and "--input" not in c]), 1)

    def test_a_run_that_leaves_no_record_stays_in_the_denominator(self):
        outcomes = iter([None, f"{demo3.RUN_TIMEOUT}: no result after 7500s"])
        demo3.run_experiment([BASELINE], [self.case], 2, self.out, runner=FakeHost(),
                             launch=lambda *a, **k: next(outcomes), say=lambda line: None)
        rows = demo3.load_rows(self.out / BASELINE.name / "runs.jsonl")
        self.assertEqual([r["termination_reason"] for r in rows],
                         [demo3.RUN_CRASHED, demo3.RUN_TIMEOUT])
        item = self.report(configs=[BASELINE], repeats=2)["configurations"][0]
        self.assertEqual((item["runs"], item["repaired"], len(item["not_a_result"])), (2, 0, 2))
        self.assertEqual(item["mean_attempts"], 0)

    def test_the_real_child_process_records_its_run(self):
        # dpkg-source is missing or rejects the fake .dsc: either way one row, not a repair.
        said = []
        demo3.run_experiment([BASELINE], [self.case], 1, self.out, runner=FakeHost(),
                             replay=REPLAYS / "rcran-known-fix.json", say=said.append)
        rows = demo3.load_rows(self.out / BASELINE.name / "runs.jsonl")
        self.assertEqual([r["run_id"] for r in rows], [f"one-shot-v1--{RCRAN}--r1"])
        self.assertFalse(rows[0]["repaired"])
        item = self.report(configs=[BASELINE], repeats=1)["configurations"][0]
        self.assertEqual(len(item["not_a_result"]), 1)
        self.assertIn("--run-one --agent one-shot", (self.out / "experiment.log").read_text())

    def test_a_case_that_already_builds_is_excluded_and_reported(self):
        said = []
        demo3.run_experiment([BASELINE], [self.case], 3, self.out,
                             launch=lambda *a, **k: self.fail("must not run"),
                             runner=FakeHost(baseline_passes=True), say=said.append)
        self.assertIn("already builds", said[0])
        report = self.report(configs=[BASELINE])
        self.assertEqual(report["cases"], [])
        self.assertIn(f"excluded case {RCRAN}: the unrepaired case already builds.",
                      report["notes"])

    def test_an_interrupted_baseline_build_is_redone(self):
        (self.out / "baselines" / RCRAN).mkdir(parents=True)  # no result inside
        log = demo3.failure_log(self.case, self.out, runner=FakeHost())
        self.assertIn("xmmintrin.h", log.read_text())
        host = FakeHost()
        self.assertEqual(demo3.failure_log(self.case, self.out, runner=host), log)
        self.assertEqual(host.calls, [])  # a finished one is reused


class Cases(Experiment):
    def test_bundles_are_validated_and_sorted(self):
        self.assertEqual(self.case, Case(RCRAN, self.bundle, 3600))
        with self.assertRaises(ValueError):
            demo3.find_cases([self.root / "not-a-bundle"])
        with self.assertRaises(ValueError):  # the same case twice would double its weight
            demo3.find_cases([self.bundle, self.make_bundle("copy")])

    def test_default_is_every_local_bundle_in_the_home_directory(self):
        home = self.root / "home"
        shutil.copytree(self.bundle, home / "buildbench-local-rcran-v1")
        (home / "buildbench-local-broken").mkdir()
        (home / "buildbench-local-rcran-v1.tar").write_text("archive")
        with patch.object(Path, "home", return_value=home):
            self.assertEqual([c.case_id for c in demo3.find_cases(None)], [RCRAN])


class Blockers(Experiment):
    def agents(self, full_main="the loop\n", baseline_main="single attempt\n"):
        agents = self.root / "agents"
        for name, main in (("one-shot", baseline_main), ("full-agent", full_main)):
            (agents / name / "src").mkdir(parents=True, exist_ok=True)
            (agents / name / "src" / "main.py").write_text(main)
        return agents

    def test_the_real_baseline_is_the_frozen_one(self):
        self.assertIsNone(demo3.blocker(BASELINE))

    def test_a_changed_baseline_is_not_measured(self):
        reason = demo3.blocker(BASELINE, self.agents())
        self.assertIn("not the frozen one-shot-v1", reason)
        self.assertIn(demo3.FROZEN_BASELINE, reason)

    def test_the_full_agent_is_not_measured_while_it_is_still_the_baseline(self):
        scaffold = self.agents(full_main="single attempt\n")
        for config in (ON, OFF):
            self.assertIn("Part C", demo3.blocker(config, scaffold))
        self.assertIsNone(demo3.blocker(ON, self.agents(full_main="the loop\n")))
        shutil.rmtree(self.root / "agents" / "full-agent")
        self.assertIn("missing", demo3.blocker(ON, self.root / "agents"))


class Switches(unittest.TestCase):
    def test_the_three_configurations_of_the_plan(self):
        self.assertEqual([(c.name, c.agent) for c in CONFIGURATIONS], [
            ("one-shot-v1", "one-shot"), ("full-history-on", "full-agent"),
            ("full-history-off", "full-agent")])
        self.assertEqual(BASELINE.env, {})
        self.assertEqual({k for k in ON.flags if ON.flags[k] != OFF.flags[k]}, {"use_history"})

    def test_each_arm_sets_the_switches_the_full_agent_reads(self):
        code = ("import dataclasses, json; from src.config import config_from_env; "
                "print(json.dumps(dataclasses.asdict(config_from_env())))")
        for config in (ON, OFF):
            with self.subTest(configuration=config.name):
                done = subprocess.run([sys.executable, "-c", code], cwd=demo3.AGENTS / "full-agent",
                                      env={**os.environ, **config.env}, capture_output=True,
                                      text=True, check=True)
                self.assertEqual(json.loads(done.stdout), config.flags)


class Report(Experiment):
    CASES = [f"case-{n:02d}" for n in range(1, 11)]

    def ten_cases(self, config, repaired_by_repeat, **extra):
        """repaired_by_repeat: for each repeat, how many of the ten cases were repaired."""
        return [row(config, case, repeat, index < count, **extra)
                for repeat, count in enumerate(repaired_by_repeat, start=1)
                for index, case in enumerate(self.CASES)]

    def test_the_table_for_the_three_configurations(self):
        self.record(self.ten_cases(BASELINE, [3, 3, 3], fix_type="build_commands", wall=100.0)
                    + self.ten_cases(ON, [5, 5, 4], tokens=(30_000, 4_000), wall=400.0, attempts=2)
                    + self.ten_cases(OFF, [4, 3, 3], tokens=(28_000, 3_000), wall=380.0,
                                     attempts=[{}, {}, {}]))
        report = self.report()
        self.assertEqual(report["problems"], [])
        base, on, off = report["configurations"]
        self.assertEqual((base["runs"], base["repaired"], base["repaired_per_repeat"]),
                         (30, 9, {1: 3, 2: 3, 3: 3}))
        self.assertEqual((on["repaired"], on["median_tokens"], on["median_wall_seconds"]),
                         (14, 34_000, 400.0))
        self.assertEqual((base["mean_attempts"], on["mean_attempts"], off["mean_attempts"]),
                         (1.0, 2.0, 3.0))
        self.assertEqual(base["repairs_by_fix_type"],
                         {"source": 0, "packaging": 0, "build_commands": 9})
        self.assertEqual(len(base["needs_review"]), 9)
        self.assertEqual(on["by_case"]["case-04"], {"runs": 3, "repaired": 3})

        first, second = report["comparisons"]
        self.assertEqual((first["name"], first["target"]), ("full-history-on", "met"))
        self.assertEqual(first["paired_differences"], [2, 2, 1])
        self.assertAlmostEqual(first["difference_per_run"], 5 / 3)
        self.assertEqual([g["case_id"] for g in first["gains"]], ["case-04", "case-05"])
        self.assertEqual(first["regressions"], [])
        self.assertEqual((second["name"], second["target"]), ("full-history-off", "not met"))

        text = demo3.render(report)
        self.assertIn("Cases measured: 10 of the 10 planned. Runs per case and configuration: 3. "
                      f"Model: {MODEL}.", text)
        self.assertIn("| `one-shot-v1` | the naive baseline (frozen) | 30 | 9 | 3 / 3 / 3 | 3.00 "
                      "| 12,200 | 100 s | 1.0 | 0 |", text)
        self.assertIn("| `full-history-on` | the improvement claimed | 30 | 14 | 5 / 5 / 4 | 4.67 "
                      "| 34,000 | 400 s | 2.0 | 0 |", text)
        self.assertIn("| case-04 | 0/3 | 3/3 | 1/3 |", text)
        self.assertIn("| unknown | 9/30 | 14/30 | 10/30 |", text)  # these ids state no direction
        self.assertIn("| `one-shot-v1` | build_failed 21, build_succeeded 9 |", text)
        self.assertIn("| `one-shot-v1` | 0 | 0 | 9 |", text)
        self.assertIn("`full-history-on`: +1.67 verified repairs per run. Gains: case-04 (0 -> 3 of "
                      "3); case-05 (0 -> 2 of 3). Regressions: none.", text)
        self.assertIn("Repair history: 14 verified repairs with it, 10 without, over 30 and 30 runs.",
                      text)
        self.assertIn("not RISC-V, hidden-case or leaderboard results", text)

    def test_regressions_are_reported_and_a_small_gain_does_not_meet_the_target(self):
        better = self.ten_cases(ON, [4, 3, 3])
        better[0]["repaired"] = False            # case-01, repeat 1: the baseline repairs it
        self.record(self.ten_cases(BASELINE, [3, 3, 3]) + better)
        comparison = self.report(configs=[BASELINE, ON])["comparisons"][0]
        self.assertEqual(comparison["regressions"],
                         [{"case_id": "case-01", "baseline": 3, "configuration": 2}])
        self.assertEqual(comparison["target"], "not met")
        self.assertIn("Regressions: case-01 (3 -> 2 of 3)",
                      demo3.render(self.report(configs=[BASELINE, ON])))

    def test_the_target_needs_a_net_gain_in_two_of_three_paired_runs(self):
        self.record(self.ten_cases(BASELINE, [3, 3, 3]) + self.ten_cases(ON, [9, 3, 3]))
        comparison = self.report(configs=[BASELINE, ON])["comparisons"][0]
        self.assertAlmostEqual(comparison["difference_per_run"], 2.0)
        self.assertEqual(comparison["target"], "not met")
        self.assertIn("positive in 1 of 3 paired runs", comparison["target_detail"])

    def test_one_case_is_reported_but_the_target_is_not_evaluated(self):
        self.record([row(BASELINE, RCRAN, n, n == 1) for n in (1, 2, 3)]
                    + [row(ON, RCRAN, n, True) for n in (1, 2, 3)])
        report = self.report(configs=[BASELINE, ON])
        comparison = report["comparisons"][0]
        self.assertEqual(comparison["target"], "not evaluated")
        self.assertIn("1 measured", comparison["target_detail"])
        self.assertEqual(comparison["gains"],
                         [{"case_id": RCRAN, "baseline": 1, "configuration": 3}])
        text = demo3.render(report)
        self.assertIn(f"Cases measured: 1 of the 10 planned ({RCRAN}).", text)
        self.assertIn("| x86_64 to aarch64 | 1/3 | 3/3 |", text)

    def test_direction_is_read_from_the_case_id(self):
        self.assertEqual(demo3.direction(RCRAN), "x86_64 to aarch64")
        self.assertEqual(demo3.direction("launchpad-jammy-arm64-amd64-tmpreaper-8c42ffb8e477"),
                         "aarch64 to x86_64")
        self.assertEqual(demo3.direction("case-01"), "unknown")

    def test_a_configuration_that_was_not_run_is_shown_as_not_run(self):
        self.record([row(BASELINE, RCRAN, n, False) for n in (1, 2, 3)])
        scaffold = self.root / "agents"
        for name in ("one-shot", "full-agent"):
            (scaffold / name / "src").mkdir(parents=True)
            (scaffold / name / "src" / "main.py").write_text("single attempt\n")
        report = self.report(agents=scaffold)
        self.assertEqual(report["comparisons"], [])
        self.assertTrue(any(note.startswith("full-history-on has not been run: ") and
                            "Part C" in note for note in report["notes"]))
        text = demo3.render(report)
        self.assertIn("| `full-history-on` | the improvement claimed | 0 | not run | - |", text)
        self.assertNotIn("Repair history:", text)

    def test_unknown_values_are_dashes_never_zeros(self):
        self.record([row(ON, RCRAN, n, False, tokens=None, wall=None) for n in (1, 2, 3)])
        text = demo3.render(self.report(configs=[ON]))
        self.assertIn("| `full-history-on` | the improvement claimed | 3 | 0 | 0 / 0 / 0 | 0.00 "
                      "| - | - | - | 0 |", text)
        self.assertIn("token use is unknown for 3 run(s)", text)

    def test_runs_that_are_not_comparable_are_called_out(self):
        rows = self.ten_cases(BASELINE, [3, 3, 3]) + self.ten_cases(ON, [5, 5, 5]) \
            + self.ten_cases(OFF, [4, 4, 4], agent_version="0.1.0+bbbbbbbbbbbb")
        rows[0]["agent_version"] = "1.0.1+cccccccccccc"   # an edited baseline
        rows[31]["model"] = "openai-compatible:another-model"
        del rows[59]                                       # one full-history-on run missing
        self.record(rows)
        report = self.report()
        problems = " ".join(report["problems"])
        for expected in ("one-shot-v1 mixes agent versions", "not all from the frozen baseline",
                         "did not use one model", "full-history-on does not have 3 runs for: case-10",
                         "history on and off ran different agent code"):
            self.assertIn(expected, problems)
        self.assertTrue(all(c["target"] == "not evaluated" for c in report["comparisons"]))
        self.assertIn("### Problems", demo3.render(report))

    def test_a_run_record_that_contradicts_its_arm_is_a_problem(self):
        agreed = {**OFF.flags}
        rows = [row(OFF, RCRAN, n, False, agent_config=agreed) for n in (1, 2, 3)]
        rows[1]["agent_config"] = {**agreed, "use_history": True}
        self.record(rows)
        report = self.report(configs=[OFF])
        self.assertIn(f"full-history-off: the run record states different switches for "
                      f"full-history-off--{RCRAN}--r2.", report["problems"])
        self.assertFalse(any("do not state the switches" in note for note in report["notes"]))

    def test_run_records_without_switches_are_noted(self):
        self.record([row(OFF, RCRAN, n, False) for n in (1, 2, 3)])
        notes = " ".join(self.report(configs=[OFF])["notes"])
        self.assertIn("3 run records do not state the switches used", notes)

    def test_stand_in_model_rows_are_never_presented_as_a_measurement(self):
        self.record([row(BASELINE, RCRAN, n, True, model="replay:rcran-known-fix.json")
                     for n in (1, 2, 3)])
        self.assertIn("a pipeline check, not a measurement of any agent.",
                      " ".join(self.report(configs=[BASELINE])["problems"]))

    def test_infrastructure_failures_are_counted_and_listed(self):
        self.record([row(BASELINE, RCRAN, 1, False, reason="repack_failed"),
                     row(BASELINE, RCRAN, 2, False, reason="no_edit_applied"),
                     {**row(BASELINE, RCRAN, 3, False), "termination_reason": None}])
        item = self.report(configs=[BASELINE])["configurations"][0]
        self.assertEqual(item["not_a_result"],
                         [f"one-shot-v1--{RCRAN}--r1", f"one-shot-v1--{RCRAN}--r3"])
        self.assertEqual((item["runs"], item["unknown_attempts"]), (3, 1))
        self.assertEqual(item["termination_reasons"],
                         {"no_edit_applied": 1, "no reason recorded": 1, "repack_failed": 1})


class CommandLine(Experiment):
    def main(self, *argv):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            try:
                code = demo3.main([*map(str, argv), "--out", str(self.out)])
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    def test_report_only_prints_the_table_and_writes_the_files(self):
        self.record([row(BASELINE, RCRAN, n, n == 1) for n in (1, 2, 3)])
        table, numbers = self.root / "table.md", self.root / "numbers.json"
        code, out, _ = self.main("--report-only", "--output", table, "--json", numbers)
        self.assertEqual(code, 0)
        self.assertIn("## Demo 3 comparison", out)
        self.assertIn("| `one-shot-v1` | the naive baseline (frozen) | 3 | 1 | 1 / 0 / 0 | 0.33 |", out)
        self.assertEqual(table.read_text(), out)
        self.assertEqual(json.loads(numbers.read_text())["configurations"][0]["repaired"], 1)

    def test_report_only_with_nothing_recorded(self):
        code, out, _ = self.main("--report-only")
        self.assertEqual(code, 0)
        self.assertIn("Cases measured: 0 of the 10 planned.", out)
        self.assertEqual(out.count("| not run |"), 3)

    def test_a_live_run_needs_the_key_before_anything_is_built(self):
        with patch.dict(os.environ):
            os.environ.pop("BB_MODEL_API_KEY", None)
            code, _, err = self.main("--bundle", self.bundle)
        self.assertEqual(code, 2)
        self.assertIn("BB_MODEL_API_KEY", err)
        self.assertFalse(self.out.exists())

    def test_bad_arguments(self):
        self.assertEqual(self.main("--bundle", self.root / "nowhere")[0], 2)
        self.assertEqual(self.main("--bundle", self.bundle, "--repeats", 0)[0], 2)
        self.assertEqual(self.main("--configurations", "one-shot-v2")[0], 2)
        with patch.object(Path, "home", return_value=self.root / "empty-home"):
            self.assertIn("no validator bundle found", self.main()[2])

    def test_pipeline_check_from_the_command_line(self):
        # The fake bundle's empty run.sh produces no result, so the case is excluded:
        # real bash, real exclusion, and still a table.
        code, out, _ = self.main("--bundle", self.bundle, "--configurations", "one-shot-v1",
                                 "--replay", REPLAYS / "rcran-known-fix.json", "--repeats", 1)
        self.assertEqual(code, 0)
        self.assertIn("configurations: one-shot-v1   runs each: 1   total: 1 runs", out)
        self.assertIn(f"excluded {RCRAN}: the unrepaired build gave no usable result", out)
        self.assertIn("| `one-shot-v1` | the naive baseline (frozen) | 0 | not run |", out)


if __name__ == "__main__":
    unittest.main()
