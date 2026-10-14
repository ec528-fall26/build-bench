"""Part D: local build feedback for the full agent's loop.

    worktree/input -> copy -> dpkg-source --auto-commit -b -> assemble case
        -> run.sh -> BuildResult (Part A applied to the new log)

Implements the agent's BuildFeedback contract (agents/full-agent/src/contracts.py)
on the local validator bundle. It lives in the harness, never in the agent ZIP:
in the competition the organizers run the builds.

The agent's worktree is never touched. Repacking happens on a copy, so the
agent's own tree and its diff stay clean (--auto-commit adds patch files).

A candidate that fails is a result, not an error: a repack that dpkg-source
rejects, a build that never reaches compilation (for example a broken
debian/control), a timeout or a compile error all return succeeded=False with
diagnostics, so the loop can rank the attempt and revise. Only a validator
that produces no result at all raises, because then nothing is known about
the candidate.

Excerpt mode passes the agent only the log lines around the first errors and
the end of the log, not the whole log. The organizers' in-run builds return
"diagnostic excerpts" in a format not yet published; this is our stand-in, so
the agent is not tuned to evidence it will not have.

Usage (on the ARM64 host, from the repository root):
    python3 -m harness.build_feedback --bundle ~/buildbench-local-rcran-v1 --prepare ~/wt
    # edit ~/wt/input/..., then:
    python3 -m harness.build_feedback --bundle ~/buildbench-local-rcran-v1 --worktree ~/wt [--excerpt]
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

from harness.record import load_outcome, outcome_row
from harness.run_case import (
    BUILD_MARGIN, DPKG_TIMEOUT, REPO, CommandFailed, Runner, assemble_case, sh,
)


FULL_AGENT_DIR = REPO / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
STAGE_ORDER = ("unknown", "configure", "build", "test", "install", "packaging")

EXCERPT_ERRORS = 5    # errors whose surrounding lines are kept
EXCERPT_BEFORE = 5    # lines kept before each error ...
EXCERPT_AFTER = 10    # ... and after it
EXCERPT_TAIL = 20     # last lines of the log, always kept
RECORD_DIAGNOSTICS = 20


def load_full_agent(agent_dir: Path = FULL_AGENT_DIR) -> SimpleNamespace:
    """Import Part A and the contracts from the full agent's src package.

    Both agents are packages named `src`; importing one after the other in the
    same process silently returns the first. Refuse instead.
    """
    loaded = sys.modules.get("src")
    if loaded is not None and Path(loaded.__file__).resolve().parent != (agent_dir / "src").resolve():
        raise RuntimeError(f"another agent's src package is already imported: {loaded.__file__}")
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    from src.contracts import BuildResult, Diagnostic
    from src.evidence import diagnose

    return SimpleNamespace(diagnose=diagnose, BuildResult=BuildResult, Diagnostic=Diagnostic)


def write_excerpt(log: Path, diagnostics: list, dest: Path) -> None:
    """Write the lines around the first errors plus the log's end, streaming."""
    offsets = sorted({d.log_offset for d in diagnostics[:EXCERPT_ERRORS] if d.log_offset >= 0})
    error_lines, offset, number = [], 0, 0
    with log.open("rb") as stream:
        for number, raw in enumerate(stream):
            while offsets and offsets[0] < offset + len(raw):
                error_lines.append(number)
                offsets.pop(0)
            offset += len(raw)
    total = number + 1 if offset else 0
    keep = set(range(max(total - EXCERPT_TAIL, 0), total))
    for line in error_lines:
        keep.update(range(max(line - EXCERPT_BEFORE, 0), min(line + EXCERPT_AFTER + 1, total)))
    with log.open("rb") as stream, dest.open("wb") as out:
        previous = -1
        for number, raw in enumerate(stream):
            if number not in keep:
                continue
            if number != previous + 1:
                out.write(b"[... lines omitted ...]\n")
            out.write(raw if raw.endswith(b"\n") else raw + b"\n")
            previous = number


class LocalBuildFeedback:
    """BuildFeedback on the local validator bundle; one folder per build."""

    def __init__(self, bundle: Path, builds_dir: Path, agent: SimpleNamespace, *,
                 excerpt: bool = False, timeout_seconds: float | None = None,
                 runner: Runner = sh):
        self.bundle = Path(bundle).resolve()
        self.case_dir = self.bundle / "case"
        manifest = json.loads((self.case_dir / "manifest.json").read_text(encoding="utf-8"))
        self.builds_dir = Path(builds_dir).resolve()
        self.agent = agent
        self.excerpt = excerpt
        self.timeout = timeout_seconds or manifest["build"].get("timeout_seconds", 3600) + BUILD_MARGIN
        self.runner = runner
        self.records: list[dict] = []  # one per build, for the run record

    def request_build(self, worktree: Path):
        worktree = Path(worktree).resolve()
        if self.builds_dir.is_relative_to(worktree):
            raise ValueError("builds_dir must be outside the worktree")
        number = len(self.records) + 1
        build_dir = self.builds_dir / f"build-{number:02d}"
        build_dir.mkdir(parents=True)  # never overwrite an earlier build
        harness_log = build_dir / "harness.log"
        started = time.monotonic()

        work = build_dir / "work"
        work.mkdir()
        shutil.copytree(worktree / "input", work / "input", symlinks=True)
        try:
            self.runner(["dpkg-source", "--auto-commit", "-b", "."],
                        work / "input", harness_log, DPKG_TIMEOUT)
            assemble_case(self.case_dir, work, build_dir / "case")
        except CommandFailed as error:
            # dpkg-source rejected the candidate, e.g. a malformed patch.
            return self._finish(number, build_dir, worktree, started, kind="repack_failed",
                                log=harness_log, outcome=None, message=str(error))

        out = build_dir / "result"
        failure = None
        try:
            self.runner(["bash", self.bundle / "run.sh", "--input", build_dir / "case",
                         "--output", out], self.bundle, harness_log, self.timeout)
        except CommandFailed as error:
            failure = str(error)  # run.sh exits nonzero on every failed build
        outcome = load_outcome(out)
        if outcome.status in ("missing_result", "invalid_result"):
            message = "; ".join(filter(None, [failure, outcome.message]))
            self._record(number, build_dir, kind="validator_error", succeeded=False,
                         stage="unknown", started=started, diagnostics=[], outcome=outcome,
                         message=message)
            raise RuntimeError(f"validator produced no result: {message}; see {build_dir}")

        if outcome.repaired:
            kind = "succeeded"
        elif outcome.timed_out:
            kind = "timed_out"
        elif not _reached_real_build(out):
            kind = "no_real_build"  # e.g. dependencies unresolvable after a control edit
        else:
            kind = "build_failed"
        return self._finish(number, build_dir, worktree, started, kind=kind,
                            log=out / "build.log", outcome=outcome, message=outcome.message)

    def _finish(self, number, build_dir, worktree, started, *, kind, log, outcome, message):
        diagnostics = []
        if log.is_file():
            diagnostics = self.agent.diagnose(log, worktree)
            if self.excerpt:
                excerpt = log.with_name(log.stem + ".excerpt.log")
                write_excerpt(log, diagnostics, excerpt)
                diagnostics = self.agent.diagnose(excerpt, worktree)
        succeeded = kind == "succeeded"
        if not succeeded and not diagnostics:
            diagnostics = [self.agent.Diagnostic(message or kind, "unknown", None, None, [],
                                                 None, -1)]
        if succeeded:
            stage = "packaging"
        elif kind in ("repack_failed", "no_real_build"):
            stage = "unknown"  # nothing was compiled: rank below any real build
        else:
            stage = max((d.stage for d in diagnostics), key=STAGE_ORDER.index, default="unknown")
        record = self._record(number, build_dir, kind=kind, succeeded=succeeded, stage=stage,
                              started=started, diagnostics=diagnostics, outcome=outcome,
                              message=message)
        return self.agent.BuildResult(succeeded, stage, diagnostics, record["duration_seconds"])

    def _record(self, number, build_dir, *, kind, succeeded, stage, started, diagnostics,
                outcome, message) -> dict:
        record = {
            "build": number, "kind": kind, "succeeded": succeeded, "stage_reached": stage,
            "duration_seconds": round(time.monotonic() - started, 1), "excerpt": self.excerpt,
            "message": message, "diagnostic_count": len(diagnostics),
            "diagnostics": [asdict(d) for d in diagnostics[:RECORD_DIAGNOSTICS]],
            "result": outcome_row(outcome) if outcome else None, "build_dir": str(build_dir),
        }
        (build_dir / "feedback.json").write_text(json.dumps(record, indent=2) + "\n",
                                                 encoding="utf-8")
        self.records.append(record)
        return record


def _reached_real_build(out: Path) -> bool:
    try:
        details = json.loads((out / "build-diagnostics.json").read_text(encoding="utf-8"))
        return details["execution"]["reached_real_build"] is not False
    except (OSError, ValueError, KeyError, TypeError):
        return True  # unknown: classify by the build result alone


def prepare_worktree(bundle: Path, dest: Path, runner: Runner = sh) -> Path:
    """Unpack the case's source package into dest/input, as run_case does."""
    bundle, dest = Path(bundle).resolve(), Path(dest).resolve()
    manifest = json.loads((bundle / "case/manifest.json").read_text(encoding="utf-8"))
    dest.mkdir(parents=True)
    runner(["dpkg-source", "-x", bundle / "case" / manifest["build"]["recipe"], "input"],
           dest, dest / "prepare.log", DPKG_TIMEOUT)
    return dest


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", type=Path, required=True,
                        help="local validator bundle containing run.sh and case/")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", type=Path, metavar="DIR",
                        help="unpack the case's source into DIR/input to edit by hand")
    action.add_argument("--worktree", type=Path, metavar="DIR",
                        help="build DIR/input and print the BuildResult")
    parser.add_argument("--excerpt", action="store_true",
                        help="diagnose only the excerpt, as the competition will return")
    parser.add_argument("--builds-dir", type=Path,
                        help="default: <bundle>/harness-builds/<UTC time>")
    args = parser.parse_args(argv)

    if args.prepare:
        try:
            prepare_worktree(args.bundle, args.prepare)
        except CommandFailed as error:
            print(f"unpack failed: {error}", file=sys.stderr)
            return 1
        print(f"unpacked to {args.prepare / 'input'}")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    feedback = LocalBuildFeedback(
        args.bundle, args.builds_dir or args.bundle / "harness-builds" / stamp,
        load_full_agent(), excerpt=args.excerpt)
    try:
        result = feedback.request_build(args.worktree)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    record = feedback.records[-1]
    print(f"{'SUCCEEDED' if result.succeeded else 'failed'} ({record['kind']}), "
          f"stage {result.stage_reached}, {record['duration_seconds']}s, "
          f"{record['diagnostic_count']} diagnostics")
    for d in result.diagnostics[:5]:
        where = (d.resolved[0] if d.resolved else d.log_path) or ""
        if where and d.line is not None:
            where += f":{d.line}"
        print(f"  [{d.stage}] {where} {d.message}".rstrip())
    print(f"record: {record['build_dir']}/feedback.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
