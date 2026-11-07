"""Run the full agent on public development cases as hosted, then bundle for Development Validation.

Hosted runs have no in-run builds, so the agent needs only what every public
case ships: the source package and the original failure log. This runs the
full agent on any of the 200 public cases (buildbench-development-cases-v0.1)
without building anything, then writes one Worktree Bundle that the website's
Development Validation builds in the official environment. That gives an
official verdict for every case, including the aarch64 -> x86_64 ones the ARM
instance cannot build.

    run:     dpkg-source -x -> agent (no build feedback, hosted time limit)
             -> dpkg-source --auto-commit -b -> <out>/<case_id>/case/input/
    bundle:  every recorded case -> one ZIP (experiments/validation_bundle.py)

A case where the agent changes nothing, or that stops early, is bundled with
its original source, so every case that was run is validated and stays in the
denominator; select the same cases on the website ("Recommended" selects the
111 small ones in harness/public_small_cases.txt).

Usage (ARM64 host, repository root; the model settings as for run_case --live):
    python3 -m harness.run_public run --cases-root ~/bb-public/buildbench-development-cases-v0.1 \\
        --out ~/bb-public-runs/v033 --tag v033 --small [--shard 1/4]
    python3 -m harness.run_public bundle --out ~/bb-public-runs/v033 --output ~/devval-v033.zip
    python3 -m harness.run_public status --out ~/bb-public-runs/v033

Shards are separate processes over disjoint cases; a case already recorded in
<out>/runs.jsonl is skipped, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import random
import shutil
import sys
import time

from harness.record import append_jsonl
from harness.run_case import (AGENTS, DPKG_TIMEOUT, CommandFailed, agent_version, load_agent,
                              sh, write_diff)
from harness.diff_scan import scan_diff

REPO = Path(__file__).resolve().parents[1]
SMALL_CASES = Path(__file__).with_name("public_small_cases.txt")
# The cases the agent's recognised patterns and playbook were written from; a
# result on them is not a held-out result.
TUNED_CASES = (
    "r-cran-digest-7a42effc961f", "libunwind-f18b55e03a91", "rust-subversion-b3a4b8c35bb2",
    "html-xml-utils-fe68b3f14b84", "ectrans-50e45e72102d", "libmceliece-5020df9683fc",
    "pybigwig-56ac842288ce", "libbpp-core-13069fd09280", "libbpp-qt-32eb71858f9f",
    "python-intbitset-0ede6db7f446", "libevtx-1ee53325da7e",
)
# The validator's policy for every local bundle (case/manifest.json); hosted
# runs get theirs from task.json.
PATCH_POLICY = {"allowed_paths": ["input/**"],
                "forbidden_paths": ["manifest.json", "config/**", "dependencies/**"]}
# Every file a .dsc can list: the archives and the upstream signatures
# (<orig>.tar.gz.asc, as in zvbi), which the validator checks like the rest.
ARCHIVE = (".dsc", ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.lzma", ".tar.zst", ".diff.gz", ".asc")


def _hash_names(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def _matches(case_id: str, names) -> bool:
    return any(case_id.endswith("-" + name) for name in names)


def public_cases(cases_root: Path) -> list[dict]:
    """Every case in the dataset manifest, with its folder."""
    with (cases_root / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["dir"] = cases_root / Path(row["case_metadata"]).parent
    return rows


def select(cases: list[dict], *, ids=None, small=False, held_out=False, sample=None,
           seed=0, stage=None, classify=None) -> list[dict]:
    chosen = cases
    if ids:
        missing = [i for i in ids if not any(c["case_id"] == i or c["case_id"].endswith("-" + i)
                                             for c in cases)]
        if missing:
            raise SystemExit(f"not in the dataset: {', '.join(missing)}")
        chosen = [c for c in chosen if c["case_id"] in ids or _matches(c["case_id"], ids)]
    if small:
        names = _hash_names(SMALL_CASES)
        chosen = [c for c in chosen if _matches(c["case_id"], names)]
    if held_out:
        chosen = [c for c in chosen if not _matches(c["case_id"], TUNED_CASES)]
    if stage:
        # The agent's own failure classifier, on each case's original log.
        chosen = [c for c in chosen if classify(c["dir"] / "logs/target-failure.log") == stage]
    chosen = sorted(chosen, key=lambda c: c["case_id"])
    if sample is not None and sample < len(chosen):
        chosen = sorted(random.Random(seed).sample(chosen, sample), key=lambda c: c["case_id"])
    return chosen


def _copy_package(files: list[Path], dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for path in files:
        shutil.copy2(path, dest / path.name)


def _package_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and any(p.name.endswith(s) for s in ARCHIVE))


def run_one(case: dict, out: Path, tag: str, agent, model, *, keep_work=False,
            runner=sh) -> dict:
    """One case: unpack, run the agent without builds, repack. Always returns a row."""
    from src.contracts import Budget  # the agent's own package, loaded by load_agent
    from src.main import HOSTED_SECONDS

    case_id = case["case_id"]
    meta = json.loads((case["dir"] / "case.json").read_text(encoding="utf-8"))
    run_dir = out / case_id
    if run_dir.exists():
        shutil.rmtree(run_dir)  # an unrecorded, interrupted attempt
    run_dir.mkdir(parents=True)
    log = case["dir"] / meta["paths"]["target_failure_log"]
    source = case["dir"] / meta["paths"]["source_input"]
    harness_log = run_dir / "harness.log"
    started, clock = datetime.now(timezone.utc), time.monotonic()
    row = {"run_id": f"{tag}-{case_id}", "case_id": case_id, "mode": "full-public",
           "direction": case["direction"], "package": case["package_name"],
           "agent_version": agent_version(AGENTS / "full-agent"), "model": model.name,
           "served_model": None, "started_at": started.isoformat(timespec="seconds"),
           "edits_proposed": 0, "edits_applied": 0, "repaired": None,
           "termination_reason": None, "error": None, "diff_path": None,
           "held_out": not _matches(case_id, TUNED_CASES), **scan_diff("")}
    original = _package_files(source)
    work = run_dir / "work"
    try:
        dscs = [p for p in original if p.suffix == ".dsc"]
        if len(dscs) != 1:
            raise CommandFailed(f"expected one .dsc in {source}, found {len(dscs)}")
        work.mkdir()
        runner(["dpkg-source", "-x", dscs[0], "input"], work, harness_log, DPKG_TIMEOUT)
        pristine = run_dir / "pristine"
        shutil.copytree(work / "input", pristine, symlinks=True)

        context = agent.CaseContext(
            case_id=case_id, worktree=work, log_tail="",
            task_metadata={"case_id": case_id, "worktree": str(work),
                           "initial_build_log": str(log), "target_arch": meta["platform"]["target_arch"],
                           "patch_policy": PATCH_POLICY})
        try:
            result, adapter = agent.run_agent(
                context, log, agent.client_completion(model), config=agent.config_from_env(),
                budget=Budget(max_seconds=HOSTED_SECONDS), build_feedback=None,
                patch_policy=PATCH_POLICY, package=meta["package"]["name"])
        except Exception as error:  # the agent's failure is a result, not a crash
            row["termination_reason"] = "agent_error"
            row["error"] = f"{type(error).__name__}: {error}"
            result = adapter = None
        finally:
            row["served_model"] = getattr(model, "served_model", None)
            if write_diff(pristine, work / "input", run_dir / "repair.diff"):
                row["diff_path"] = str(run_dir / "repair.diff")
                row.update(scan_diff((run_dir / "repair.diff").read_text(encoding="utf-8")))
            shutil.rmtree(pristine)
        if adapter is not None:
            with (run_dir / "replies.jsonl").open("w", encoding="utf-8") as handle:
                for entry in adapter.replies:
                    handle.write(json.dumps(entry) + "\n")
            record = agent.result_record(result, adapter, agent.config_from_env())
            row.update(agent_termination=record["termination_reason"],
                       agent_message=record["termination_message"], attempts=record["attempts"],
                       turns=record["turns"], malformed_count=record["malformed_count"],
                       usage=result.usage)
            # 0.5.1: each edit the syntax guard sent back, and why (empty before 0.5.1).
            row["syntax_guard"] = [
                {"path": e.get("path"), "problems": e.get("problems")}
                for e in record.get("review_events") or [] if e.get("event") == "syntax_guard"]
            row["edits_proposed"] = sum(len(a.edits) for a in result.attempts)
            row["edits_applied"] = sum(a.applied for a in result.attempts
                                       if a.number in result.kept_attempts)
        if row["diff_path"] is None:
            row["termination_reason"] = row["termination_reason"] or "no_edit_applied"
        else:
            # Edits left in the worktree are submitted, as on the platform, even
            # when the agent stopped with an error.
            try:
                runner(["dpkg-source", "--auto-commit", "-b", "."], work / "input",
                       harness_log, DPKG_TIMEOUT)
                _copy_package(_package_files(work), run_dir / "case" / "input")
                row["bundled"] = "rebuilt"
                row["termination_reason"] = row["termination_reason"] or "submitted"
            except CommandFailed as error:
                row["termination_reason"], row["error"] = "repack_failed", str(error)
    except CommandFailed as error:
        row["termination_reason"], row["error"] = "unpack_failed", str(error)
    finally:
        row["wall_seconds"] = round(time.monotonic() - clock, 1)
        if row.get("bundled") != "rebuilt":
            row["bundled"] = "original"
            _copy_package(original, run_dir / "case" / "input")
        if not keep_work and work.exists():
            shutil.rmtree(work)
        (run_dir / "record.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    return row


def recorded(out: Path) -> dict[str, dict]:
    path = out / "runs.jsonl"
    if not path.is_file():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {row["case_id"]: row for row in rows}


def cmd_run(args) -> int:
    classify = None
    if args.stage:
        load_agent(AGENTS / "full-agent")
        from src.failure import classify_failure
        classify = lambda log: classify_failure(log).stage  # noqa: E731
    cases = select(public_cases(args.cases_root.expanduser()), ids=args.case, small=args.small,
                   held_out=args.held_out, sample=args.sample, seed=args.seed,
                   stage=args.stage, classify=classify)
    index, count = (int(x) for x in args.shard.split("/"))
    if not 1 <= index <= count:
        raise SystemExit("--shard is K/N with 1 <= K <= N")
    mine = [c for n, c in enumerate(cases) if n % count == index - 1]
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    done = recorded(out)
    todo = [c for c in mine if c["case_id"] not in done]
    print(f"{len(cases)} cases selected; shard {args.shard}: {len(mine)}, "
          f"{len(mine) - len(todo)} already recorded", flush=True)
    agent = load_agent(AGENTS / "full-agent")
    for n, case in enumerate(todo, 1):
        model = agent.ModelClient.from_env()  # a fresh client: served_model is per case
        if not model.api_key:
            raise SystemExit("needs BB_MODEL_API_KEY (see harness/README.md)")
        print(f"[{n}/{len(todo)}] {case['case_id']}", flush=True)
        row = run_one(case, out, args.tag, agent, model, keep_work=args.keep_work)
        append_jsonl(out / "runs.jsonl", row)
        print(f"    {row['termination_reason']}: {row['edits_applied']} edits applied, "
              f"{row.get('agent_termination')}, {row['wall_seconds']}s", flush=True)
    return 0


def _bundler():
    spec = importlib.util.spec_from_file_location(
        "validation_bundle", REPO / "experiments" / "validation_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_bundle(args) -> int:
    out = args.out.expanduser().resolve()
    rows = recorded(out)
    if not rows:
        raise SystemExit(f"nothing recorded in {out}")
    runs = {row["run_id"]: (row, out / row["case_id"]) for row in rows.values()}
    if args.fill_from:
        # Cases this run left out, taken from another run (e.g. the baseline),
        # so the bundle covers the same cases and validates with one selection.
        base = args.fill_from.expanduser().resolve()
        filled = 0
        for case_id, row in recorded(base).items():
            if case_id not in rows:
                rows[case_id] = row
                runs[row["run_id"]] = (row, base / case_id)
                filled += 1
        print(f"{filled} cases filled in from {base}")
    vb = _bundler()
    try:
        # No "client" field: the website rejected a plain string there (6 November).
        sheet = vb.build_bundle(runs, sorted(runs), args.output.expanduser())
    except vb.BundleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for check in sheet["checks"]:
        row = rows[check["case_id"]]
        check.update(bundled=row["bundled"], held_out=row["held_out"],
                     termination_reason=row["termination_reason"])
    sheet_path = args.output.expanduser().with_suffix(".checks.json")
    sheet_path.write_text(json.dumps(sheet, indent=2) + "\n", encoding="utf-8")
    rebuilt = sum(r["bundled"] == "rebuilt" for r in rows.values())
    small = _hash_names(SMALL_CASES)
    every_small = len(rows) == len(small) and all(_matches(c, small) for c in rows)
    print(f"{args.output}: {len(rows)} cases ({rebuilt} with the agent's edits, "
          f"{len(rows) - rebuilt} original source)")
    print("select on the website: " + ("Recommended (exactly the 111 small cases)" if every_small
                                        else "these cases:"))
    if not every_small:
        for case_id in sorted(rows):
            print(f"  {case_id}")
    print(f"check sheet: {sheet_path}")
    return 0


def cmd_status(args) -> int:
    rows = recorded(args.out.expanduser().resolve())
    reasons: dict[str, int] = {}
    for row in rows.values():
        reasons[row["termination_reason"]] = reasons.get(row["termination_reason"], 0) + 1
    tokens = sum(((r.get("usage") or {}).get("input_tokens") or 0)
                 + ((r.get("usage") or {}).get("output_tokens") or 0) for r in rows.values())
    print(f"{len(rows)} cases recorded; {tokens:,} tokens")
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {reason}: {n}")
    guarded = {cid: r["syntax_guard"] for cid, r in rows.items() if r.get("syntax_guard")}
    if guarded:
        print(f"syntax guard sent edits back in {len(guarded)} cases:")
        for cid, events in sorted(guarded.items()):
            print(f"  {cid}: " + "; ".join(f"{e['path']}: {(e['problems'] or ['?'])[0][:90]}"
                                          for e in events))
    return 0


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the agent on public cases (no builds)")
    run.add_argument("--cases-root", type=Path, required=True,
                     help="the extracted buildbench-development-cases-v0.1 folder")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--tag", required=True, help="prefix of every run id, e.g. the agent version")
    run.add_argument("--case", action="append", help="a case id or <package>-<hash>; repeatable")
    run.add_argument("--small", action="store_true", help="only the website's 111 small cases")
    run.add_argument("--held-out", action="store_true",
                     help="leave out the eleven cases the agent was tuned on")
    run.add_argument("--stage", help="only cases whose original log fails at this stage "
                                     "(the agent's classifier), e.g. test")
    run.add_argument("--sample", type=int)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--shard", default="1/1", help="K/N: run every Nth case starting at K")
    run.add_argument("--keep-work", action="store_true", help="keep each unpacked worktree")
    bundle = sub.add_parser("bundle", help="one Worktree Bundle ZIP of every recorded case")
    bundle.add_argument("--out", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    bundle.add_argument("--fill-from", type=Path,
                        help="another run's --out: its cases complete the bundle")
    status = sub.add_parser("status", help="count the recorded cases by outcome")
    status.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return {"run": cmd_run, "bundle": cmd_bundle, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    from harness.run_public import main as _main
    raise SystemExit(_main(sys.argv[1:]))
