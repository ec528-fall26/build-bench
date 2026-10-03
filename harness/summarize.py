"""Summarize runs.jsonl: repair rate overall and split by fix type.

Rows written before the fix-type scan existed are classified from their
repair.diff when it is still on disk. Failed and crashed runs stay in the
counts: they are part of the denominator.

Usage: python3 -m harness.summarize <runs.jsonl> [--model NAME] [--agent-version V]
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from harness.diff_scan import scan_diff


FIX_TYPES = ("build_commands", "source", "packaging", "none")


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "fix_type" not in row:
            diff = row.get("diff_path")
            if diff and Path(diff).is_file():
                row.update(scan_diff(Path(diff).read_text(encoding="utf-8")))
            elif not diff:
                row.update(scan_diff(""))
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    by_type = {t: Counter() for t in FIX_TYPES + ("unknown",)}
    for row in rows:
        counts = by_type[row.get("fix_type", "unknown")]
        counts["runs"] += 1
        counts["repaired"] += bool(row.get("repaired"))
    return {
        "runs": len(rows),
        "repaired": sum(bool(r.get("repaired")) for r in rows),
        "needs_review": [r["run_id"] for r in rows if r.get("needs_review")],
        "by_fix_type": {t: dict(c) for t, c in by_type.items() if c["runs"]},
    }


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs", type=Path, help="runs.jsonl written by harness.run_case")
    parser.add_argument("--model", help="only rows whose model field equals this")
    parser.add_argument("--agent-version", help="only rows from this agent version")
    args = parser.parse_args(argv)

    rows = load_rows(args.runs)
    if args.model:
        rows = [r for r in rows if r.get("model") == args.model]
    if args.agent_version:
        rows = [r for r in rows if r.get("agent_version") == args.agent_version]
    s = summarize(rows)
    print(f"runs: {s['runs']}   repaired: {s['repaired']}")
    print(f"{'fix type':<16}{'runs':>6}{'repaired':>10}")
    for fix_type, c in s["by_fix_type"].items():
        print(f"{fix_type:<16}{c.get('runs', 0):>6}{c.get('repaired', 0):>10}")
    if s["needs_review"]:
        print("needs review (build-time commands): " + ", ".join(s["needs_review"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
