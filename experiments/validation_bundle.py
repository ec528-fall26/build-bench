#!/usr/bin/env python3
"""Ground truth: package recorded local repairs for Development Validation.

Development Validation is the organizers' official-environment check for
repaired public cases. It is a web upload, one task per team at a time, so it
cannot carry the evaluation; we use it to spot-check that the local validator's
verdicts agree with the official one (docs/full-agent.md, Part E).

This script builds the upload, a Worktree Bundle ZIP in the layout the
organizers document (Development Validation page, inspected 13 October 2026):

    manifest.json                        schema_version, suite_id, cases
    cases/<case_id>/worktree/input/      one run's rebuilt source package

For each selected run it takes <run>/case/input: the rebuilt .dsc and the
archives it names, the very files the local validator built, checked here
against the .dsc's own checksums. Case manifests, config, dependencies, logs and
repair.diff are never included. A bundle holds one run per case.

Beside the ZIP it writes a check sheet listing each run's local verdict with
the official verdict left empty. Fill those in from the website's Case Results,
then `--compare` counts the agreements.

Usage (repository root):
    python3 experiments/validation_bundle.py RUNS_DIR [RUNS_DIR ...] --sample 4 \\
        --output development-validation.zip
    python3 experiments/validation_bundle.py RUNS_DIR --run-id ID --output check.zip
    python3 experiments/validation_bundle.py --compare development-validation.checks.json

Standard library only; Python 3.10 or newer. Never part of the agent ZIP.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import zipfile

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.summarize import load_rows  # noqa: E402


SCHEMA_VERSION = "0.1"
SUITE_ID = "development-recommended-v1"  # "unless the website shows a different ID"
SEED = 528


class BundleError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_package(run_dir: Path) -> list[Path]:
    """The rebuilt .dsc and every archive it names, verified against its checksums."""
    folder = run_dir / "case" / "input"
    if not folder.is_dir():
        raise BundleError(f"{run_dir.name}: no rebuilt source package (the run never reached "
                          "the build, so there is nothing to validate)")
    dscs = sorted(folder.glob("*.dsc"))
    if len(dscs) != 1:
        raise BundleError(f"{run_dir.name}: expected one .dsc in {folder}, found {len(dscs)}")
    listed, inside = [], False
    for line in dscs[0].read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Checksums-Sha256:"):
            inside = True
        elif inside and line.startswith(" ") and len(line.split()) == 3:
            listed.append(line.split())
        elif inside:
            break
    if not listed:
        raise BundleError(f"{run_dir.name}: {dscs[0].name} lists no Checksums-Sha256 entries")
    files = [dscs[0]]
    for sha256, size, name in listed:
        path = folder / name
        if Path(name).name != name or path.is_symlink() or not path.is_file():
            raise BundleError(f"{run_dir.name}: {name}, named by the .dsc, is not a file in {folder}")
        if path.stat().st_size != int(size) or _sha256(path) != sha256:
            raise BundleError(f"{run_dir.name}: {name} does not match the checksum in the .dsc")
        files.append(path)
    return files


def find_runs(runs_dirs: list[Path]) -> dict[str, tuple[dict, Path]]:
    """Every recorded run, by run id, with the folder it was recorded in."""
    runs = {}
    for runs_dir in runs_dirs:
        record = runs_dir / "runs.jsonl"
        if not record.is_file():
            raise BundleError(f"{runs_dir} has no runs.jsonl")
        for row in load_rows(record):
            if row["run_id"] in runs:
                raise BundleError(f"run id {row['run_id']} is recorded more than once")
            runs[row["run_id"]] = (row, runs_dir / row["run_id"])
    return runs


def sample(runs: dict, count: int, seed: int = SEED) -> list[str]:
    """Up to ``count`` runs, one per case, alternating repaired and not repaired.

    Only runs that produced a source package can be validated. The order is
    fixed by ``seed`` so the selection can be reproduced and is not hand-picked.
    """
    usable = sorted(rid for rid, (_, run_dir) in runs.items() if (run_dir / "case/input").is_dir())
    random.Random(seed).shuffle(usable)
    pools = {True: [r for r in usable if runs[r][0].get("repaired")],
             False: [r for r in usable if not runs[r][0].get("repaired")]}
    chosen, cases, want = [], set(), True
    while len(chosen) < count and (pools[True] or pools[False]):
        pool = pools[want] or pools[not want]
        rid = pool.pop(0)
        if runs[rid][0].get("case_id") not in cases:
            cases.add(runs[rid][0].get("case_id"))
            chosen.append(rid)
            want = not want
    return chosen


def build_bundle(runs: dict, run_ids: list[str], output: Path, *, suite_id: str = SUITE_ID,
                 client: str | None = None) -> dict:
    """Write the ZIP and return the check sheet."""
    if not run_ids:
        raise BundleError("no run selected")
    packages, cases = [], {}
    for rid in run_ids:
        if rid not in runs:
            raise BundleError(f"run id {rid} is not recorded in the given folders")
        row, run_dir = runs[rid]
        case_id = row.get("case_id")
        if not case_id:
            raise BundleError(f"{rid}: the run record has no case id")
        if case_id in cases:
            raise BundleError(f"{rid} and {cases[case_id]} are both for {case_id}; "
                              "a bundle holds one worktree per case")
        cases[case_id] = rid
        packages.append((row, run_dir, source_package(run_dir)))

    manifest = {"schema_version": SCHEMA_VERSION, "suite_id": suite_id}
    if client:
        manifest["client"] = client
    manifest["cases"] = [{"case_id": row["case_id"],
                          "worktree": f"cases/{row['case_id']}/worktree"}
                         for row, _, _ in packages]
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        for row, _, files in packages:
            for path in files:
                bundle.write(path, f"cases/{row['case_id']}/worktree/input/{path.name}")

    return {
        "bundle": output.name, "bundle_sha256": _sha256(output), "suite_id": suite_id,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": [{
            "case_id": row["case_id"], "run_id": row["run_id"], "run_dir": str(run_dir),
            "agent_version": row.get("agent_version"), "model": row.get("model"),
            "fix_type": row.get("fix_type"),
            "local_repaired": bool(row.get("repaired")),
            "local_status": (row.get("result") or {}).get("status"),
            # Fill these in from the website's Run Detail & Case Results.
            "official_repaired": None, "official_status": None, "validated_on": None, "notes": "",
        } for row, run_dir, _ in packages],
    }


def compare(sheet: dict) -> str:
    """Count how many local verdicts the official environment confirmed."""
    checks = sheet["checks"]
    done = [c for c in checks if isinstance(c.get("official_repaired"), bool)]
    agree = [c for c in done if c["official_repaired"] == c["local_repaired"]]
    lines = [f"{len(agree)} of {len(done)} checked local verdicts agree with Development "
             f"Validation; {len(checks) - len(done)} not yet checked."]
    for check in done:
        if check not in agree:
            lines.append(f"DISAGREES {check['run_id']}: local "
                         f"{'repaired' if check['local_repaired'] else 'not repaired'}, official "
                         f"{'repaired' if check['official_repaired'] else 'not repaired'} "
                         f"({check.get('official_status')})")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs_dirs", nargs="*", type=Path, metavar="RUNS_DIR",
                        help="folder holding runs.jsonl and one folder per run")
    chosen = parser.add_mutually_exclusive_group()
    chosen.add_argument("--run-id", action="append", help="a run to include; repeatable")
    chosen.add_argument("--sample", type=int, metavar="N", help="choose N runs, one per case, "
                        "alternating repaired and not repaired")
    parser.add_argument("--seed", type=int, default=SEED, help=f"for --sample (default {SEED})")
    parser.add_argument("--output", type=Path, default=Path("development-validation.zip"))
    parser.add_argument("--suite-id", default=SUITE_ID)
    parser.add_argument("--client", help="optional manifest field")
    parser.add_argument("--compare", type=Path, metavar="SHEET",
                        help="count agreements in a filled-in check sheet; builds nothing")
    args = parser.parse_args(argv)

    if args.compare:
        print(compare(json.loads(args.compare.read_text(encoding="utf-8"))), end="")
        return 0
    if not args.runs_dirs or not (args.run_id or args.sample):
        parser.error("give RUNS_DIR and either --run-id or --sample")
    try:
        runs = find_runs(args.runs_dirs)
        run_ids = args.run_id or sample(runs, args.sample, args.seed)
        sheet = build_bundle(runs, run_ids, args.output, suite_id=args.suite_id, client=args.client)
    except BundleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.sample:
        sheet["sample"] = {"requested": args.sample, "seed": args.seed}
    sheet_path = args.output.with_suffix(".checks.json")
    sheet_path.write_text(json.dumps(sheet, indent=2) + "\n", encoding="utf-8")
    print(f"{args.output}: {len(sheet['checks'])} case(s), suite {args.suite_id}")
    for check in sheet["checks"]:
        verdict = "repaired" if check["local_repaired"] else "not repaired"
        print(f"  {check['case_id']}  local: {verdict}  ({check['run_id']})")
    print(f"check sheet: {sheet_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
