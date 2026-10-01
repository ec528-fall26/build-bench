"""Run the agent against one local case and record the validator's verdict.

    baseline build (unless --log) -> dpkg-source -x -> agent edits ->
    dpkg-source --auto-commit -b -> assemble case -> run.sh -> record

Parts 1, 2 and 4 run for real: Part 1's run_repair drives Part 2's extract_tail
and Part 4's apply_edits. Only the model is replaced, by ReplayModel, which
returns edits from a JSON file. Swap in Part 3's client when it lands.

Every attempt appends one row to <runs-dir>/runs.jsonl, including runs that
stop early: failures stay in the evaluation denominator.

Usage (on the ARM64 host, from the repository root):
    python3 -m harness.run_case --bundle ~/buildbench-local-rcran-v1 \\
        --replay harness/replays/rcran-known-fix.json --run-id fix-auto-1
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Callable, Iterator

from harness.record import append_jsonl, load_outcome, outcome_row


REPO = Path(__file__).resolve().parents[1]
AGENT_DIR = REPO / "buildbench-starter-kit-0.1.0-rc.2/agents/one-shot"
DPKG_TIMEOUT = 600
BUILD_MARGIN = 600  # beyond the manifest's own timeout_seconds


class CommandFailed(RuntimeError):
    pass


class _Stop(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


Runner = Callable[[list, Path, Path, float], None]


def sh(cmd: list, cwd: Path, log: Path, timeout: float) -> None:
    """Run cmd, appending its output to log. Raises CommandFailed on failure."""
    with log.open("a", encoding="utf-8") as out:
        out.write(f"\n$ (cd {cwd} && {' '.join(map(str, cmd))})\n")
        out.flush()
        try:
            proc = subprocess.run(
                [str(c) for c in cmd], cwd=cwd, stdout=out,
                stderr=subprocess.STDOUT, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise CommandFailed(f"{cmd[0]} timed out after {timeout:.0f}s") from None
    if proc.returncode != 0:
        raise CommandFailed(f"{cmd[0]} exited {proc.returncode}; see {log.name}")


def load_agent(agent_dir: Path) -> SimpleNamespace:
    """Import the agent's src package (Parts 1, 2 and 4) from the starter kit."""
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    from src import edit_applier
    from src.log_tail import extract_tail
    from src.main import run_repair
    from src.types import CaseContext, Edit, EditPlan

    return SimpleNamespace(
        run_repair=run_repair, extract_tail=extract_tail, edit_applier=edit_applier,
        CaseContext=CaseContext, Edit=Edit, EditPlan=EditPlan,
    )


class ReplayModel:
    """Stand-in for Part 3: proposes the edits stored in a JSON file."""

    def __init__(self, path: Path, agent: SimpleNamespace):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.name = f"replay:{Path(path).name}"
        self.case_id = data["case_id"]
        self.rationale = data.get("rationale", "")
        self.edits = data["edits"]
        self.agent = agent

    def propose(self, context):
        no_usage = {"input_tokens": None, "output_tokens": None}
        if context.case_id != self.case_id:
            return self.agent.EditPlan([], f"replay file is for {self.case_id}", no_usage)
        edits = [self.agent.Edit(e["path"], e["old_text"], e["new_text"]) for e in self.edits]
        return self.agent.EditPlan(edits, self.rationale, no_usage)


@contextmanager
def capture_warnings(logger: logging.Logger) -> Iterator[list]:
    """Collect Part 4's rejection and compatibility warnings, which it only logs."""
    messages: list = []

    class Collect(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    handler = Collect(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)


def _link_or_copy(src, dst):
    # Hard links cost no disk; the validator mounts the case read-only.
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def assemble_case(case_dir: Path, work_dir: Path, dest: Path) -> None:
    """Copy the case, replacing only input/ with the rebuilt source package."""
    built = sorted(
        p for p in work_dir.iterdir()
        if p.is_file() and (p.suffix == ".dsc" or ".tar." in p.name)
    )
    if sum(p.suffix == ".dsc" for p in built) != 1:
        raise CommandFailed(f"expected one rebuilt .dsc in {work_dir}, found {[p.name for p in built]}")
    dest.mkdir()
    for item in case_dir.iterdir():
        if item.name == "input":
            continue
        if item.is_dir():
            shutil.copytree(item, dest / item.name, copy_function=_link_or_copy)
        else:
            shutil.copy2(item, dest / item.name)
    (dest / "input").mkdir()
    for p in built:
        shutil.copy2(p, dest / "input" / p.name)


def run_case(
    bundle: Path, run_id: str, runs_dir: Path, replay: Path, *,
    log: Path | None = None, agent_dir: Path = AGENT_DIR, runner: Runner = sh,
) -> dict:
    bundle, runs_dir = bundle.resolve(), runs_dir.resolve()
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)  # refuses to reuse a run id
    harness_log = run_dir / "harness.log"
    case_dir = bundle / "case"
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    build_timeout = manifest["build"].get("timeout_seconds", 3600) + BUILD_MARGIN

    agent = load_agent(agent_dir)
    model = ReplayModel(replay, agent)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    row = {
        "run_id": run_id, "case_id": manifest["case_id"], "mode": "one-shot",
        "model": model.name, "started_at": started.isoformat(timespec="seconds"),
        "edits_proposed": 0, "edits_applied": 0, "edit_warnings": [],
        "rationale": "", "usage": {}, "baseline": None, "result": None,
        "repaired": False, "termination_reason": None, "error": None,
    }

    def build(case: Path, out: Path):
        """Return a validator outcome; a nonzero run.sh exit is a normal failed build."""
        cmd = ["bash", bundle / "run.sh", "--output", out]
        if case != case_dir:
            cmd[2:2] = ["--input", case]
        failure = None
        try:
            runner(cmd, bundle, harness_log, build_timeout)
        except CommandFailed as error:
            failure = str(error)
        outcome = load_outcome(out)
        if outcome.status in ("missing_result", "invalid_result") and failure:
            outcome = replace(outcome, message=f"{failure}; {outcome.message}")
        return outcome

    try:
        if log is None:
            baseline = build(case_dir, run_dir / "baseline")
            row["baseline"] = outcome_row(baseline)
            if baseline.repaired:
                raise _Stop("case_not_failing", "unrepaired case already builds")
            if baseline.status in ("missing_result", "invalid_result"):
                raise _Stop("baseline_error", baseline.message)
            log = run_dir / "baseline" / "build.log"

        work = run_dir / "work"
        work.mkdir()
        try:
            runner(["dpkg-source", "-x", case_dir / manifest["build"]["recipe"], "input"],
                   work, harness_log, DPKG_TIMEOUT)
        except CommandFailed as error:
            raise _Stop("unpack_failed", str(error)) from None

        context = agent.CaseContext(
            case_id=manifest["case_id"], worktree=work, log_tail="",
            task_metadata={"case_id": manifest["case_id"], "worktree": str(work),
                           "initial_build_log": str(log),
                           "target_arch": manifest["build"]["architecture"]},
        )
        with capture_warnings(agent.edit_applier.LOGGER) as warnings:
            try:
                plan, applied = agent.run_repair(
                    context, Path(log), agent.extract_tail, model,
                    patch_policy=manifest["patch_policy"],
                )
            except Exception as error:  # the agent's failure is a result, not a crash
                raise _Stop("agent_error", f"{type(error).__name__}: {error}") from None
            finally:
                row["edit_warnings"] = list(warnings)
        row.update(edits_proposed=len(plan.edits), edits_applied=applied,
                   rationale=plan.rationale, usage=plan.usage)
        if applied == 0:
            raise _Stop("no_edit_applied", "nothing to build: the source is unchanged")

        try:
            runner(["dpkg-source", "--auto-commit", "-b", "."],
                   work / "input", harness_log, DPKG_TIMEOUT)
            assemble_case(case_dir, work, run_dir / "case")
        except CommandFailed as error:
            raise _Stop("repack_failed", str(error)) from None

        result = build(run_dir / "case", run_dir / "result")
        row["result"] = outcome_row(result)
        row["repaired"] = result.repaired
        row["termination_reason"] = "build_succeeded" if result.repaired else "build_failed"
        if result.status in ("missing_result", "invalid_result"):
            row["termination_reason"], row["error"] = "build_error", result.message
    except _Stop as stop:
        row["termination_reason"], row["error"] = stop.reason, stop.detail
    finally:
        row["wall_seconds"] = round(time.monotonic() - clock, 1)
        (run_dir / "record.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
        append_jsonl(runs_dir / "runs.jsonl", row)
    return row


INFRA_FAILURES = {"baseline_error", "unpack_failed", "repack_failed", "build_error"}


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", type=Path, required=True,
                        help="local validator bundle containing run.sh and case/")
    parser.add_argument("--replay", type=Path, required=True,
                        help="JSON file of edits for the stand-in model")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-dir", type=Path, help="default: <bundle>/harness-runs")
    parser.add_argument("--log", type=Path,
                        help="failure log for the agent; default: run a baseline build first")
    args = parser.parse_args(argv)

    row = run_case(args.bundle, args.run_id, args.runs_dir or args.bundle / "harness-runs",
                   args.replay, log=args.log)
    verdict = "REPAIRED" if row["repaired"] else "not repaired"
    print(f"{row['run_id']}: {verdict} ({row['termination_reason']}), "
          f"{row['edits_applied']}/{row['edits_proposed']} edits applied, {row['wall_seconds']}s")
    if row["error"]:
        print(f"  {row['error']}")
    return 1 if row["termination_reason"] in INFRA_FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
