"""Reproduce Part A coverage without running builds or contacting a model.

python tests/full-agent/evaluate_evidence.py CASES_ROOT --output report.json
Optional --worktrees ROOT expects ROOT/<case-id>/input/ with unpacked sources.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
                      "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"))
from src.evidence import diagnose


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--worktrees", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logs = sorted(args.cases.rglob("target-failure.log"))
    if not logs:
        parser.error("no target-failure.log files found")
    rows = []
    for log in logs:
        case_id = log.parent.parent.name
        worktree = args.worktrees / case_id if args.worktrees else log.parent.parent / "source"
        started = time.perf_counter()
        diagnostics = diagnose(log, worktree)
        elapsed = time.perf_counter() - started
        with log.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        rows.append({"case_id": case_id, "log": log.relative_to(args.cases).as_posix(),
                     "bytes": log.stat().st_size, "sha256": digest,
                     "seconds": round(elapsed, 4), "diagnostic_count": len(diagnostics),
                     "diagnostics_with_resolved_paths": sum(bool(d.resolved) for d in diagnostics),
                     "first_diagnostics": [asdict(d) for d in diagnostics[:5]]})
        print(f"{len(rows)}/{len(logs)} {case_id}: {len(diagnostics)}", flush=True)
    report = {"logs": len(rows), "logs_with_diagnostics": sum(r["diagnostic_count"] > 0 for r in rows),
              "note": "Parser coverage, not repair success. Path resolution requires unpacked worktrees.",
              "cases": rows}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
