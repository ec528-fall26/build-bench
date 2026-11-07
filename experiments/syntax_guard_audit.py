#!/usr/bin/env python3
"""How many recorded no-build repairs would the 0.5.1 syntax guard have sent back?

Reads a harness/run_public output folder (one <case_id>/repair.diff per case),
unpacks each case's original source with dpkg-source, applies the recorded
diff, and runs the agent's own syntax_guard.file_problems on every changed
file: the same check, before and after, that 0.5.1 runs before writing a batch.

It needs no model and no build, so it can measure the guard on a finished
Development Validation run in minutes, before spending a queue slot on it.
With --verdicts it joins the website's per-case results, so the table shows
whether the cases the guard flags are the ones that failed.

Usage (ARM64 EC2 host, repository root; dpkg-source and patch on PATH):
    python3 experiments/syntax_guard_audit.py ~/bb-public-runs/v033 \\
        --cases-root ~/bb-public/buildbench-development-cases-v0.1 \\
        [--verdicts verdicts.json] [--output audit.json]

--verdicts takes the JSON from the website's case list for a run, saved from
the browser (it has "cases": [{"case_id", "status"}, ...]), or a plain
{"<case_id>": "succeeded" | "failed" | ...} mapping.

Standard library only. Never part of the agent ZIP.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic

REPO = Path(__file__).resolve().parents[1]
AGENT = REPO / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"
sys.path.insert(0, str(AGENT))
from src import syntax_guard  # noqa: E402

DPKG_TIMEOUT = 600


def _case_dirs(cases_root: Path) -> dict[str, Path]:
    """case_id -> the case folder, found by its case.json."""
    found = {}
    for meta in cases_root.rglob("case.json"):
        found[meta.parent.name] = meta.parent
    return found


def _changed_files(diff: str) -> list[str]:
    files = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[len("+++ b/"):].strip())
    return files


def _verdicts(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "cases" in data:
        return {c["case_id"]: c.get("status", "") for c in data["cases"]}
    return dict(data)


def audit_case(case_dir: Path, diff_path: Path, *, seconds: float = 60) -> dict:
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    source = case_dir / meta["paths"]["source_input"]
    dscs = sorted(source.glob("*.dsc"))
    row = {"files": [], "problems": [], "error": None}
    if len(dscs) != 1:
        row["error"] = f"expected one .dsc, found {len(dscs)}"
        return row
    diff = diff_path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="bb-audit-") as tmp:
        root = Path(tmp)
        try:
            subprocess.run(["dpkg-source", "-x", str(dscs[0]), "input"], cwd=root, check=True,
                           capture_output=True, timeout=DPKG_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            row["error"] = f"dpkg-source -x failed: {exc}"
            return row
        changed = _changed_files(diff)
        before = {}
        for rel in changed:
            try:
                before[rel] = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                before[rel] = ""
        try:
            subprocess.run(["patch", "-p1", "--batch", "--silent", "-i", str(diff_path)],
                           cwd=root, check=True, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            row["error"] = f"patch failed: {exc}"
            return row
        deadline = monotonic() + seconds
        for rel in changed:
            try:
                after = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            row["files"].append(rel)
            for problem in syntax_guard.file_problems(rel, before.get(rel, ""), after,
                                                      worktree=root, deadline=deadline):
                row["problems"].append({"path": rel, "line": problem.line,
                                        "message": problem.message})
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs", type=Path, help="a harness/run_public --out folder")
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--verdicts", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    cases = _case_dirs(args.cases_root)
    verdicts = _verdicts(args.verdicts)
    rows = []
    for diff_path in sorted(args.runs.glob("*/repair.diff")):
        case_id = diff_path.parent.name
        if case_id not in cases:
            rows.append({"case_id": case_id, "error": "case not in --cases-root"})
            continue
        row = {"case_id": case_id, "verdict": verdicts.get(case_id), **audit_case(
            cases[case_id], diff_path)}
        rows.append(row)
        flag = "FLAGGED" if row.get("problems") else ("error" if row.get("error") else "ok")
        print(f"{flag:8} {row.get('verdict') or '-':12} {case_id}")
        for problem in row.get("problems", []):
            print(f"         {problem['path']}:{problem['line'] or '?'}: {problem['message']}")
        if row.get("error"):
            print(f"         {row['error']}")

    flagged = [r for r in rows if r.get("problems")]
    by_verdict = Counter(r.get("verdict") or "unknown" for r in flagged)
    print(f"\n{len(rows)} repairs checked, {len(flagged)} flagged by the syntax guard.")
    if verdicts:
        print("Flagged, by website verdict: " + ", ".join(
            f"{k} {v}" for k, v in sorted(by_verdict.items())))
        passed_flagged = [r["case_id"] for r in flagged if r.get("verdict") == "succeeded"]
        if passed_flagged:
            print("Flagged but PASSED on the website (false alarms to look at): "
                  + ", ".join(passed_flagged))
    if args.output:
        args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
