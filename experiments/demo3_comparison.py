#!/usr/bin/env python3
"""Demo 3 comparison: the frozen baseline against the full agent, history on and off.

Measures verified repairs on every development case that has a local validator
environment, for the three configurations of docs/full-agent.md ("Demo 3
comparison"), 3 runs each, and prints the comparison table with tokens, wall
time, attempts, fix types, gains and regressions.

    1. Find the cases: local validator bundles (run.sh + case/manifest.json).
    2. Build each unrepaired case once and keep its failure log, so every run of
       every configuration starts from the same evidence.
    3. Run each configuration on each case `--repeats` times, one process per
       run (both agents are Python packages named `src`), through
       harness.run_case, with the configuration's switches in the environment.
    4. Summarise the recorded rows with harness.summarize and print the table.

Runs already recorded are skipped, so the same command resumes an interrupted
experiment, and --report-only reprints the table without running anything.
Failed, crashed and timed-out runs stay in the counts. A configuration that
cannot be measured yet is reported as not run, never filled in.

Usage (ARM64 host, repository root):
    export BB_MODEL_API_KEY=...
    python3 experiments/demo3_comparison.py
    python3 experiments/demo3_comparison.py --report-only

Standard library only; Python 3.10 or newer. Never part of the agent ZIP.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.diff_scan import scan_diff  # noqa: E402
from harness.record import append_jsonl, load_outcome  # noqa: E402
from harness.run_case import (  # noqa: E402
    BUILD_MARGIN, DPKG_TIMEOUT, INFRA_FAILURES, CommandFailed, agent_version, load_agent,
    run_case, sh,
)
from harness.summarize import load_rows, summarize  # noqa: E402
from harness.tests.test_frozen_baseline import FROZEN as FROZEN_BASELINE  # noqa: E402


AGENTS = REPO / "buildbench-starter-kit-0.1.0-rc.2/agents"
DEFAULT_OUT = Path.home() / "buildbench-experiments" / "demo3-comparison"
PLANNED_CASES = 10            # the frozen development set (docs/full-agent.md)
REPEATS = 3
AGENT_SECONDS = 1_800         # Budget.max_seconds: the agent's own stop
RUN_CRASHED, RUN_TIMEOUT = "run_crashed", "run_timeout"
# Runs that say nothing about the agent. They stay in the denominator. None is a
# run the harness recorded while an unexpected error was passing through it.
NOT_A_RESULT = INFRA_FAILURES | {RUN_CRASHED, RUN_TIMEOUT, None}
BEFORE_THE_AGENT = {"baseline_error", "unpack_failed", "case_not_failing", RUN_CRASHED, RUN_TIMEOUT}
REVIEWED_TYPES = ("source", "packaging", "build_commands")
_REPEAT = re.compile(r"--r(\d+)$")
# Every public development case id reads <source>-<series>-<from>-<to>-<package>-<hash>.
_DIRECTION = re.compile(r"^[a-z]+-[a-z]+-(amd64|arm64)-(amd64|arm64)-")
_ARCH = {"amd64": "x86_64", "arm64": "aarch64"}
_ALL_ON = {"use_evidence_manager": True, "use_tools": True,
           "use_build_feedback": True, "use_history": True}


@dataclass(frozen=True)
class Configuration:
    name: str
    agent: str                 # folder under agents/
    shows: str
    flags: dict = field(default_factory=dict)  # the AgentConfig this arm must run with

    @property
    def env(self) -> dict:
        """The switches as agents/full-agent/src/config.py reads them."""
        return {f"BB_AGENT_{name.upper()}": "1" if on else "0" for name, on in self.flags.items()}


BASELINE = Configuration("one-shot-v1", "one-shot", "the naive baseline (frozen)")
CONFIGURATIONS = (
    BASELINE,
    Configuration("full-history-on", "full-agent", "the improvement claimed", _ALL_ON),
    Configuration("full-history-off", "full-agent", "how much of the gain the history causes",
                  {**_ALL_ON, "use_history": False}),
)


@dataclass(frozen=True)
class Case:
    case_id: str
    bundle: Path
    build_timeout: int


class CaseUnusable(Exception):
    """The unrepaired case gave no failure log to start from."""


def blocker(config: Configuration, agents: Path = AGENTS) -> str | None:
    """Why this configuration cannot be measured now, or None."""
    agent_dir = agents / config.agent
    if not (agent_dir / "src" / "main.py").is_file():
        return f"agents/{config.agent} is missing"
    if config.agent == BASELINE.agent:
        version = agent_version(agent_dir)
        if version != FROZEN_BASELINE:
            return (f"agents/{config.agent} is {version}, not the frozen one-shot-v1 "
                    f"({FROZEN_BASELINE}); restore it before measuring")
        return None
    baseline_main = agents / BASELINE.agent / "src" / "main.py"
    if baseline_main.is_file() and \
            (agent_dir / "src" / "main.py").read_bytes() == baseline_main.read_bytes():
        return (f"agents/{config.agent}/src/main.py is still the baseline's single attempt "
                "(Part C's loop is not implemented), so a run would measure the baseline again")
    return None


def find_cases(bundles: list[Path] | None) -> list[Case]:
    """Every bundle given, or every ~/buildbench-local-* bundle when none is."""
    explicit = bundles is not None
    if not explicit:
        bundles = sorted(p for p in Path.home().glob("buildbench-local-*") if p.is_dir())
    cases: dict[str, Case] = {}
    for bundle in bundles:
        manifest = bundle / "case" / "manifest.json"
        if not (bundle / "run.sh").is_file() or not manifest.is_file():
            if explicit:
                raise ValueError(f"{bundle} is not a validator bundle (run.sh and case/manifest.json)")
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        case = Case(data["case_id"], bundle.resolve(), data["build"].get("timeout_seconds", 3600))
        if case.case_id in cases:
            raise ValueError(f"{case.case_id} is in both {cases[case.case_id].bundle} and {bundle}")
        cases[case.case_id] = case
    return sorted(cases.values(), key=lambda case: case.case_id)


def failure_log(case: Case, out: Path, *, runner=sh) -> Path:
    """Build the unrepaired case once; every run of the case then reads this log."""
    target = out / "baselines" / case.case_id
    log = target / "build.log"
    if target.exists() and not (log.is_file() and (target / "build-result.json").is_file()):
        shutil.rmtree(target)  # an interrupted build; run.sh refuses an existing directory
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            runner(["bash", case.bundle / "run.sh", "--output", target], case.bundle,
                   out / "experiment.log", case.build_timeout + BUILD_MARGIN)
        except CommandFailed:
            pass  # run.sh exits nonzero when the build fails, which is what we expect
    outcome = load_outcome(target)
    if outcome.repaired:
        raise CaseUnusable("the unrepaired case already builds")
    if outcome.status in ("missing_result", "invalid_result") or not log.is_file():
        raise CaseUnusable(f"the unrepaired build gave no usable result ({outcome.message})")
    return log


def run_id(config: Configuration, case: Case, repeat: int) -> str:
    return f"{config.name}--{case.case_id}--r{repeat}"


def runs_file(out: Path, config: Configuration) -> Path:
    return out / config.name / "runs.jsonl"


def recorded(out: Path, config: Configuration) -> dict[str, dict]:
    path = runs_file(out, config)
    return {row["run_id"]: row for row in load_rows(path)} if path.is_file() else {}


def run_timeout(case: Case) -> int:
    """Longer than every limit inside one run, so it fires only when something hangs."""
    return AGENT_SECONDS + 2 * DPKG_TIMEOUT + case.build_timeout + BUILD_MARGIN + 300


def run_one(agent: str, bundle: Path, rid: str, runs_dir: Path, log: Path,
            replay: Path | None = None, *, agents: Path = AGENTS, runner=sh) -> dict:
    """One run through the harness, in this process. The child process calls this."""
    agent_dir = agents / agent
    model = None if replay else load_agent(agent_dir).ModelClient.from_env()
    return run_case(bundle, rid, runs_dir, replay=replay, model=model, log=log,
                    agent_dir=agent_dir, runner=runner)


def launch(config: Configuration, case: Case, rid: str, log: Path, out: Path, *,
           replay: Path | None = None) -> str | None:
    """Run one case in a child process. Returns why it ended early, or None."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "--run-one", "--agent", config.agent,
           "--bundle", str(case.bundle), "--run-id", rid,
           "--runs-dir", str(out / config.name), "--log", str(log)]
    if replay:
        cmd += ["--replay", str(replay)]
    with (out / "experiment.log").open("a", encoding="utf-8") as output:
        output.write(f"\n$ {' '.join(cmd)}\n")
        output.flush()
        try:
            subprocess.run(cmd, cwd=REPO, env={**os.environ, **config.env}, stdout=output,
                           stderr=subprocess.STDOUT, timeout=run_timeout(case))
        except subprocess.TimeoutExpired:
            return f"{RUN_TIMEOUT}: no result after {run_timeout(case)}s"
    return None


def _lost_run(config: Configuration, case: Case, rid: str, why: str | None, seconds: float,
              agents: Path) -> dict:
    """A row for a run that ended without writing one, so it stays in the denominator."""
    reason = RUN_TIMEOUT if why and why.startswith(RUN_TIMEOUT) else RUN_CRASHED
    return {
        "run_id": rid, "case_id": case.case_id, "mode": None, "model": None, "served_model": None,
        "agent_version": agent_version(agents / config.agent), "diff_path": None, **scan_diff(""),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": round(seconds, 1), "edits_proposed": 0, "edits_applied": 0,
        "edit_warnings": [], "rationale": "", "usage": {}, "baseline": None, "result": None,
        "repaired": False, "termination_reason": reason,
        "error": why or "the run ended without a record; see experiment.log",
    }


def run_experiment(configs, cases, repeats: int, out: Path, *, replay: Path | None = None,
                   launch=launch, runner=sh, agents: Path = AGENTS, say=print) -> None:
    """Run every missing (repeat, case, configuration), repeat by repeat."""
    out.mkdir(parents=True, exist_ok=True)
    logs, status = {}, []
    for case in cases:
        try:
            logs[case.case_id] = failure_log(case, out, runner=runner)
            status.append({"case_id": case.case_id, "bundle": str(case.bundle), "excluded": None})
        except CaseUnusable as reason:
            say(f"excluded {case.case_id}: {reason}")
            status.append({"case_id": case.case_id, "bundle": str(case.bundle),
                           "excluded": str(reason)})
    (out / "cases.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    # Repeat by repeat, so run k of every configuration happens close together.
    todo = [(repeat, case, config) for repeat in range(1, repeats + 1)
            for case in cases if case.case_id in logs for config in configs]
    for number, (repeat, case, config) in enumerate(todo, start=1):
        rid = run_id(config, case, repeat)
        label = f"[{number}/{len(todo)}] {config.name} r{repeat} {case.case_id}"
        if rid in recorded(out, config):
            say(f"{label}: already recorded")
            continue
        clock = time.monotonic()
        why = launch(config, case, rid, logs[case.case_id], out, replay=replay)
        row = recorded(out, config).get(rid)
        if row is None:
            row = _lost_run(config, case, rid, why, time.monotonic() - clock, agents)
            append_jsonl(runs_file(out, config), row)
        verdict = "REPAIRED" if row.get("repaired") else "not repaired"
        reason = row.get("termination_reason") or "ended without a result; see experiment.log"
        say(f"{label}: {verdict} ({reason}), {row.get('wall_seconds')}s")


# ---- Summary -----------------------------------------------------------------

def _repeat(row: dict) -> int | None:
    match = _REPEAT.search(str(row.get("run_id", "")))
    return int(match.group(1)) if match else None


def direction(case_id: str) -> str:
    """The migration direction a case id states, or "unknown"."""
    match = _DIRECTION.match(case_id)
    return f"{_ARCH[match.group(1)]} to {_ARCH[match.group(2)]}" if match else "unknown"


def _attempts(row: dict, one_shot: bool) -> int | None:
    """Repair attempts in one run; None when the run record does not say."""
    value = row.get("attempts")
    if isinstance(value, list):
        return len(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    reason = row.get("termination_reason")
    if one_shot and reason is not None:  # one candidate by construction, if the agent ran
        return 0 if reason in BEFORE_THE_AGENT else 1
    return None


def _median(values: list) -> float | None:
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return statistics.median(values) if values else None


def describe(config: Configuration, rows: list[dict]) -> dict:
    """One configuration's rows, reduced with harness.summarize plus the Demo 3 extras."""
    summary = summarize(rows)
    attempts = [_attempts(row, config.agent == BASELINE.agent) for row in rows]
    known_attempts = [a for a in attempts if a is not None]
    per_repeat: dict[int, int] = {}
    for row in rows:
        if _repeat(row) is not None:
            per_repeat[_repeat(row)] = per_repeat.get(_repeat(row), 0) + bool(row.get("repaired"))
    recorded_flags = [row["agent_config"] for row in rows if isinstance(row.get("agent_config"), dict)]
    reasons: dict[str, int] = {}
    by_direction: dict[str, dict] = {}
    for row in rows:
        reason = str(row.get("termination_reason") or "no reason recorded")
        reasons[reason] = reasons.get(reason, 0) + 1
        group = by_direction.setdefault(direction(str(row.get("case_id"))), {"runs": 0, "repaired": 0})
        group["runs"] += 1
        group["repaired"] += bool(row.get("repaired"))
    return {
        "name": config.name, "shows": config.shows, "agent": config.agent, "flags": config.flags,
        "runs": summary["runs"], "repaired": summary["repaired"],
        "repaired_per_repeat": dict(sorted(per_repeat.items())),
        "by_case": {case: {"runs": group["runs"], "repaired": group["repaired"]}
                    for case, group in summary["by_case"].items()},
        "median_tokens": summary["median_tokens"], "unknown_tokens": summary["unknown_tokens"],
        "tokens": summary["tokens"] if summary["unknown_tokens"] < summary["runs"] else None,
        "median_wall_seconds": _median([row.get("wall_seconds") for row in rows]),
        "mean_attempts": statistics.fmean(known_attempts) if known_attempts else None,
        "unknown_attempts": len(attempts) - len(known_attempts),
        "not_a_result": sorted(row["run_id"] for row in rows
                               if row.get("termination_reason") in NOT_A_RESULT),
        "repairs_by_fix_type": {
            fix: sum(bool(r.get("repaired")) and r.get("fix_type") == fix for r in rows)
            for fix in REVIEWED_TYPES},
        "needs_review": summary["needs_review"],
        "termination_reasons": dict(sorted(reasons.items())),
        "by_direction": dict(sorted(by_direction.items())),
        "models": sorted({str(row.get("model")) for row in rows}),
        "agent_versions": sorted({str(row.get("agent_version")) for row in rows}),
        "flags_recorded": len(recorded_flags),
        "flags_contradicted": sorted(
            row["run_id"] for row in rows if isinstance(row.get("agent_config"), dict)
            and any(row["agent_config"].get(k) != v for k, v in config.flags.items())),
    }


def _mean_per_repeat(item: dict, repeats: list[int]) -> float | None:
    counts = [item["repaired_per_repeat"].get(repeat) for repeat in repeats]
    return None if not counts or None in counts else statistics.fmean(counts)


def build_report(out: Path, configs=CONFIGURATIONS, *, repeats: int = REPEATS,
                 agents: Path = AGENTS) -> dict:
    """Everything the table needs, from the rows on disk. Runs nothing."""
    status = []
    if (out / "cases.json").is_file():
        status = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    excluded = {item["case_id"]: item["excluded"] for item in status if item.get("excluded")}
    rows = {config.name: list(recorded(out, config).values()) for config in configs}
    items = [describe(config, rows[config.name]) for config in configs]
    for config, item in zip(configs, items):
        item["blocker"] = blocker(config, agents)
    cases = sorted({case for item in items for case in item["by_case"]}
                   | {item["case_id"] for item in status if not item.get("excluded")})
    expected = list(range(1, repeats + 1))

    problems, notes = [], []
    for item in items:
        if not item["runs"]:
            notes.append(f"{item['name']} has not been run"
                         + (f": {item['blocker']}." if item["blocker"] else "."))
            continue
        short = [case for case in cases if item["by_case"].get(case, {}).get("runs", 0) != repeats]
        if short:
            problems.append(f"{item['name']} does not have {repeats} runs for: {', '.join(short)}.")
        if len(item["agent_versions"]) > 1:
            problems.append(f"{item['name']} mixes agent versions: "
                            f"{', '.join(item['agent_versions'])}.")
        if item["flags_contradicted"]:
            problems.append(f"{item['name']}: the run record states different switches for "
                            f"{', '.join(item['flags_contradicted'])}.")
        elif item["flags"] and item["flags_recorded"] < item["runs"]:
            notes.append(f"{item['name']}: {item['runs'] - item['flags_recorded']} run records "
                         "do not state the switches used; the arm is known only from where the "
                         "run was launched.")
        if item["not_a_result"]:
            notes.append(f"{item['name']}: {len(item['not_a_result'])} run(s) ended in an "
                         f"infrastructure failure and are counted as not repaired: "
                         f"{', '.join(item['not_a_result'])}.")
        if item["unknown_tokens"]:
            notes.append(f"{item['name']}: token use is unknown for {item['unknown_tokens']} "
                         "run(s); medians cover the known runs only.")
        if item["needs_review"]:
            notes.append(f"{item['name']}: read the diff before counting these repairs "
                         f"(build-time commands): {', '.join(item['needs_review'])}.")
    ran = [item for item in items if item["runs"]]
    base = next((item for item in ran if item["name"] == BASELINE.name), None)
    if base and base["agent_versions"] != [FROZEN_BASELINE]:
        problems.append(f"{BASELINE.name} rows are not all from the frozen baseline "
                        f"{FROZEN_BASELINE}: {', '.join(base['agent_versions'])}.")
    models = sorted({model for item in ran for model in item["models"]})
    if len(models) > 1:
        problems.append(f"the configurations did not use one model: {', '.join(models)}.")
    if any(model.startswith("replay:") for model in models):
        problems.append("these rows come from the harness's stand-in model: a pipeline check, "
                        "not a measurement of any agent.")
    full_versions = sorted({v for item in ran if item["agent"] != BASELINE.agent
                            for v in item["agent_versions"]})
    if len(full_versions) > 1:
        problems.append("history on and off ran different agent code: "
                        f"{', '.join(full_versions)}.")
    for case, reason in sorted(excluded.items()):
        notes.append(f"excluded case {case}: {reason}.")

    comparisons = []
    for item in ran:
        if base is None or item is base:
            continue
        mean, base_mean = _mean_per_repeat(item, expected), _mean_per_repeat(base, expected)
        paired = [item["repaired_per_repeat"][r] - base["repaired_per_repeat"][r] for r in expected
                  if r in item["repaired_per_repeat"] and r in base["repaired_per_repeat"]]
        gains, regressions = [], []
        for case in cases:
            ours = item["by_case"].get(case, {}).get("repaired", 0)
            theirs = base["by_case"].get(case, {}).get("repaired", 0)
            if ours != theirs:
                (gains if ours > theirs else regressions).append(
                    {"case_id": case, "baseline": theirs, "configuration": ours})
        difference = None if mean is None or base_mean is None else mean - base_mean
        target = "not evaluated"
        if problems:
            why = "the runs are not comparable (see Problems)"
        elif len(cases) < PLANNED_CASES:
            why = (f"the target is defined on the {PLANNED_CASES} frozen cases; "
                   f"{len(cases)} measured")
        elif difference is None:
            why = "runs are missing"
        else:
            per_ten = difference * 10 / len(cases)
            met = per_ten >= 1 and sum(d > 0 for d in paired) >= 2
            target = "met" if met else "not met"
            why = (f"{per_ten:+.2f} verified repairs per 10 cases on average; net gain positive "
                   f"in {sum(d > 0 for d in paired)} of {len(paired)} paired runs")
        comparisons.append({"name": item["name"], "difference_per_run": difference,
                            "paired_differences": paired, "gains": gains,
                            "regressions": regressions, "target": target, "target_detail": why})

    return {"out": str(out), "repeats": repeats, "cases": cases, "planned_cases": PLANNED_CASES,
            "models": models, "configurations": items, "comparisons": comparisons,
            "problems": problems, "notes": notes}


def _number(value, pattern: str = "{:,.0f}") -> str:
    return "-" if value is None else pattern.format(value)


def render(report: dict) -> str:
    """The comparison as Markdown, ready for the design document or a slide."""
    items, repeats = report["configurations"], report["repeats"]
    cases = report["cases"]
    lines = ["## Demo 3 comparison", ""]
    lines.append(f"Cases measured: {len(cases)} of the {report['planned_cases']} planned"
                 + (f" ({', '.join(cases)})" if 0 < len(cases) <= 3 else "") + ". "
                 f"Runs per case and configuration: {repeats}. "
                 f"Model: {', '.join(report['models']) or 'none recorded'}.")
    lines.append("")
    per_run = " / ".join(f"r{n}" for n in range(1, repeats + 1))
    lines.append(f"| Configuration | What it shows | Runs | Verified repairs | Per run ({per_run}) "
                 "| Mean per run | Tokens, median per run | Wall time, median | "
                 "Attempts per run, mean | Infrastructure failures |")
    lines.append("| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |")
    for item in items:
        if not item["runs"]:
            lines.append(f"| `{item['name']}` | {item['shows']} | 0 | not run | - | - | - | - | - | - |")
            continue
        counts = [item["repaired_per_repeat"].get(n) for n in range(1, repeats + 1)]
        mean = _mean_per_repeat(item, list(range(1, repeats + 1)))
        lines.append(
            f"| `{item['name']}` | {item['shows']} | {item['runs']} | {item['repaired']} "
            f"| {' / '.join(_number(c) for c in counts)} | {_number(mean, '{:.2f}')} "
            f"| {_number(item['median_tokens'])} | {_number(item['median_wall_seconds'], '{:.0f} s')} "
            f"| {_number(item['mean_attempts'], '{:.1f}')} | {len(item['not_a_result'])} |")

    ran = [item for item in items if item["runs"]]
    if ran and cases:
        lines += ["", "### Verified repairs per case (repaired runs / runs)", "",
                  "| Case | " + " | ".join(f"`{item['name']}`" for item in ran) + " |",
                  "| --- |" + " ---: |" * len(ran)]
        for case in cases:
            cells = [item["by_case"].get(case) for item in ran]
            lines.append(f"| {case} | " + " | ".join(
                "-" if cell is None else f"{cell['repaired']}/{cell['runs']}" for cell in cells) + " |")
        directions = sorted({d for item in ran for d in item["by_direction"]})
        lines += ["", "### Verified repairs by migration direction (repaired runs / runs)", "",
                  "| Direction | " + " | ".join(f"`{item['name']}`" for item in ran) + " |",
                  "| --- |" + " ---: |" * len(ran)]
        for name in directions:
            cells = [item["by_direction"].get(name) for item in ran]
            lines.append(f"| {name} | " + " | ".join(
                "-" if cell is None else f"{cell['repaired']}/{cell['runs']}" for cell in cells) + " |")
        lines += ["", "### Why runs ended", "", "| Configuration | Termination reasons (runs) |",
                  "| --- | --- |"]
        for item in ran:
            reasons = ", ".join(f"{reason} {count}" for reason, count in
                                item["termination_reasons"].items())
            lines.append(f"| `{item['name']}` | {reasons} |")
        lines += ["", "### Fix types of the verified repairs", "",
                  "| Configuration | source | packaging | build_commands (read the diff) |",
                  "| --- | ---: | ---: | ---: |"]
        for item in ran:
            fixes = item["repairs_by_fix_type"]
            lines.append(f"| `{item['name']}` | {fixes['source']} | {fixes['packaging']} "
                         f"| {fixes['build_commands']} |")

    if report["comparisons"]:
        lines += ["", f"### Against `{BASELINE.name}`", ""]
        for comparison in report["comparisons"]:
            def listed(entries):
                return "; ".join(f"{e['case_id']} ({e['baseline']} -> {e['configuration']} of "
                                 f"{repeats})" for e in entries) or "none"
            lines.append(f"- `{comparison['name']}`: "
                         f"{_number(comparison['difference_per_run'], '{:+.2f}')} verified repairs "
                         f"per run. Gains: {listed(comparison['gains'])}. "
                         f"Regressions: {listed(comparison['regressions'])}. "
                         f"Target (at least 1 more verified repair per 10 cases on average, and a "
                         f"net gain in at least 2 of 3 paired runs): {comparison['target']} "
                         f"- {comparison['target_detail']}.")
        by_name = {item["name"]: item for item in ran}
        on, off = by_name.get("full-history-on"), by_name.get("full-history-off")
        if on and off:
            lines.append(f"- Repair history: {on['repaired']} verified repairs with it, "
                         f"{off['repaired']} without, over {on['runs']} and {off['runs']} runs.")

    for title, entries in (("Problems", report["problems"]), ("Notes", report["notes"])):
        if entries:
            lines += ["", f"### {title}", ""] + [f"- {entry}" for entry in entries]
    lines += ["", f"These results describe the {len(cases)} public development case(s) run "
                  "locally, verified by the organizers' local validator. They are not RISC-V, "
                  "hidden-case or leaderboard results.",
              f"Run records: `{report['out']}/<configuration>/runs.jsonl`."]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", type=Path, action="append",
                        help="a local validator bundle; repeatable "
                             "(default: every ~/buildbench-local-* bundle)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"where runs are recorded (default: {DEFAULT_OUT})")
    parser.add_argument("--repeats", type=int, default=REPEATS, help="runs per case and "
                        f"configuration (default: {REPEATS})")
    parser.add_argument("--configurations", nargs="+", metavar="NAME",
                        choices=[c.name for c in CONFIGURATIONS],
                        help="measure only these (default: all three)")
    parser.add_argument("--replay", type=Path, help="pipeline check: use the harness's stand-in "
                        "model with this edit file instead of the real model")
    parser.add_argument("--report-only", action="store_true",
                        help="run nothing; print the table from what is recorded")
    parser.add_argument("--output", type=Path, help="also write the table to this file")
    parser.add_argument("--json", type=Path, help="also write the numbers behind it as JSON")
    for name in ("--run-one", "--agent", "--run-id", "--runs-dir", "--log"):
        parser.add_argument(name, help=argparse.SUPPRESS,
                            **({"action": "store_true"} if name == "--run-one" else {}))
    args = parser.parse_args(argv)

    if args.run_one:  # the child process: exactly one run
        row = run_one(args.agent, args.bundle[0], args.run_id, Path(args.runs_dir),
                      Path(args.log), args.replay)
        print(f"{row['run_id']}: {row['termination_reason']}")
        return 0

    out = args.out.expanduser().resolve()
    if args.replay:
        args.replay = args.replay.expanduser().resolve()  # the child runs from the repository root
        if not args.replay.is_file():
            parser.error(f"--replay {args.replay} is not a file")
    configs = [c for c in CONFIGURATIONS
               if not args.configurations or c.name in args.configurations]
    if not args.report_only:
        if args.repeats < 1:
            parser.error("--repeats must be at least 1")
        try:
            cases = find_cases(args.bundle)
        except ValueError as error:
            parser.error(str(error))
        if not cases:
            parser.error("no validator bundle found; pass --bundle (see harness/README.md)")
        runnable = []
        for config in configs:
            reason = blocker(config)
            if reason:
                print(f"not running {config.name}: {reason}")
            else:
                runnable.append(config)
        if runnable and not args.replay and not os.environ.get("BB_MODEL_API_KEY"):
            parser.error("set BB_MODEL_API_KEY (see harness/README.md, 'Live runs'), "
                         "or use --replay for a pipeline check")
        total = len(runnable) * len(cases) * args.repeats
        bound = sum(run_timeout(case) for case in cases) * len(runnable) * args.repeats \
            + sum(case.build_timeout + BUILD_MARGIN for case in cases)
        print("Measuring verified repairs on the available development cases: the frozen "
              "baseline against the full agent with repair history on and off.")
        print(f"cases: {len(cases)}   configurations: {', '.join(c.name for c in runnable) or 'none'}"
              f"   runs each: {args.repeats}   total: {total} runs")
        print(f"bounded by the harness's own limits: at most {bound / 3600:.1f} h if every "
              f"build and every agent runs to its timeout. Recording to {out}")
        run_experiment(runnable, cases, args.repeats, out, replay=args.replay)
        print()

    report = build_report(out, configs, repeats=args.repeats)
    text = render(report)
    print(text, end="")
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
