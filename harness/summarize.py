"""Summarize runs.jsonl: repairs and token use, overall, per case and per fix type.

Rows written before the fix-type scan existed are classified from their
repair.diff when it is still on disk. Failed and crashed runs stay in the
counts: they are part of the denominator. A run's tokens count only when both
its input and output counts are known; otherwise it is reported as unknown,
never added as zero.

Usage: python3 -m harness.summarize <runs.jsonl> [--model NAME] [--agent-version V]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
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


def _count(value) -> int | None:
    # JSON true/false parse as bool, which is an int subclass.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def run_tokens(row: dict) -> tuple[int, int] | None:
    """(input, output) tokens for a run, or None when either is unknown."""
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    counts = (_count(usage.get("input_tokens")), _count(usage.get("output_tokens")))
    return None if None in counts else counts


def _group(rows: list[dict]) -> dict:
    known = [sum(c) for c in map(run_tokens, rows) if c is not None]
    return {
        "runs": len(rows),
        "repaired": sum(bool(r.get("repaired")) for r in rows),
        "tokens": sum(known),
        "median_tokens": statistics.median(known) if known else None,
        "unknown_tokens": len(rows) - len(known),
    }


def summarize(rows: list[dict]) -> dict:
    by_type, by_case = defaultdict(list), defaultdict(list)
    for row in rows:
        by_type[row.get("fix_type", "unknown")].append(row)
        by_case[row.get("case_id") or "unknown"].append(row)
    known = [c for c in map(run_tokens, rows) if c is not None]
    return {
        **_group(rows),
        "input_tokens": sum(c[0] for c in known),
        "output_tokens": sum(c[1] for c in known),
        "needs_review": [r["run_id"] for r in rows if r.get("needs_review")],
        "by_fix_type": {t: _group(by_type[t]) for t in FIX_TYPES + ("unknown",) if by_type[t]},
        "by_case": {case: _group(rs) for case, rs in sorted(by_case.items())},
    }


def _n(value) -> str:
    return "-" if value is None else f"{value:,.0f}"


def _table(title: str, groups: dict) -> None:
    width = max([len(title)] + [len(k) for k in groups]) + 2
    print(f"{title:<{width}}{'runs':>6}{'repaired':>10}{'tokens':>12}{'median/run':>12}{'unknown':>9}")
    for key, g in groups.items():
        tokens = None if g["unknown_tokens"] == g["runs"] else g["tokens"]  # never print 0 for unknown
        print(f"{key:<{width}}{g['runs']:>6}{g['repaired']:>10}{_n(tokens):>12}"
              f"{_n(g['median_tokens']):>12}{g['unknown_tokens']:>9}")


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
    known = s["runs"] - s["unknown_tokens"]
    print(f"runs: {s['runs']}   repaired: {s['repaired']}")
    if not known:
        print(f"tokens: unknown for {'the' if s['runs'] == 1 else 'all'} "
              f"{s['runs']} run{'s' * (s['runs'] != 1)}")
    else:
        print(f"tokens: {_n(s['input_tokens'])} in + {_n(s['output_tokens'])} out = "
              f"{_n(s['tokens'])} over {known} run{'s' * (known != 1)}"
              f" (median {_n(s['median_tokens'])} per run); unknown for {s['unknown_tokens']}")
    print()
    _table("case", s["by_case"])
    print()
    _table("fix type", s["by_fix_type"])
    if s["needs_review"]:
        print("\nneeds review (build-time commands): " + ", ".join(s["needs_review"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
