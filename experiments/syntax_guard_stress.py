#!/usr/bin/env python3
"""Precision and recall of the 0.5.1 syntax guard on real public cases, with no model.

For N small development cases (unpacked with dpkg-source), up to four C/C++
files each get harmless edits (a comment; an #include wrapped in an always-true
#if), which must never be flagged, and broken edits (a semicolon dropped from
an assignment or return; the last closing brace removed), which should be.
Each check compiles for the case's target architecture with the failing
build's own -D flags, exactly as the agent does.

Usage (repository root; dpkg-source and gcc on PATH):
    python3 experiments/syntax_guard_stress.py CASES_ROOT N

Result on 7 November 2026 (x86_64 host, 30 cases): 0 of 212 harmless edits
flagged, 136 of 164 broken edits caught, about 0.5 s per check. Misses are
files whose dependencies (glib, Python, Qt headers) are not installed, so the
lines around the edit already fail to parse here.
"""
import csv, random, re, subprocess, sys, tempfile
from pathlib import Path
from time import monotonic

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
from src import syntax_guard as g  # noqa: E402

root, limit = Path(sys.argv[1]), int(sys.argv[2])
small = {l.strip()[-12:] for l in open(REPO / "harness/public_small_cases.txt")
         if l.strip() and not l.startswith("#")}
rows = [r for r in csv.DictReader(open(root / "manifest.csv"))]
cases = [root / Path(r["case_metadata"]).parent for r in rows]
cases = [c for c in cases if c.name[-12:] in small]
random.Random(1).shuffle(cases)
SRC = {".c", ".h", ".cc", ".cpp", ".hpp", ".cxx"}

def benign(text):
    lines = text.split("\n")
    inc = next((i for i, l in enumerate(lines) if l.startswith("#include")), None)
    out = {"comment": "/* bb-check: harmless */\n" + text}
    if inc is not None:
        l2 = lines[:inc] + ["#if defined(__aarch64__) || defined(__x86_64__) || defined(__riscv) || 1"] \
            + [lines[inc]] + ["#endif"] + lines[inc + 1:]
        out["guard-include"] = "\n".join(l2)
    return out

def broken(text, rng):
    lines = text.split("\n")
    cand = [i for i, l in enumerate(lines)
            if re.match(r"^\s+(?:return\s+[^;{}]+|[A-Za-z_][\w\]\[.>-]*\s*[-+*/|&]?=\s*[^=;{}][^;{}]*);\s*$", l)
            and not lines[i - 1].rstrip().endswith("\\")]
    out = {}
    if cand:
        i = rng.choice(cand)
        out["drop-semicolon"] = "\n".join(lines[:i] + [lines[i].rstrip()[:-1]] + lines[i + 1:])
    if "}" in text:
        k = text.rfind("}")
        out["drop-last-brace"] = text[:k] + text[k + 1:]
    return out

stats = {"benign": 0, "benign_flagged": 0, "broken": 0, "broken_caught": 0, "time": 0.0}
flags_list, done = [], 0
for case in cases:
    if done >= limit:
        break
    dsc = list((case / "source/input").glob("*.dsc"))
    if len(dsc) != 1:
        continue
    work = Path(tempfile.mkdtemp(prefix="bbs-"))
    r = subprocess.run(["dpkg-source", "-x", str(dsc[0]), "input"], cwd=work, capture_output=True, timeout=600)
    if r.returncode:
        continue
    files = [p for p in (work / "input").rglob("*") if p.suffix in SRC and p.is_file()
             and ".pc" not in p.parts and p.stat().st_size < 200_000]
    if not files:
        continue
    done += 1
    target = "arm64" if "-amd64-arm64-" in case.name else "amd64"
    log = case / "logs/target-failure.log"
    rng = random.Random(case.name)
    picks = rng.sample(files, min(4, len(files)))
    for f in picks:
        rel = f.relative_to(work).as_posix()
        try:
            before = f.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        for kind, variants in (("benign", benign(before)), ("broken", broken(before, rng))):
            for vname, after in variants.items():
                t = monotonic()
                found = g.file_problems(rel, before, after, worktree=work, deadline=monotonic() + 45,
                                        target=target, log_path=log)
                stats["time"] += monotonic() - t
                stats[kind] += 1
                if found:
                    stats[kind + ("_flagged" if kind == "benign" else "_caught")] += 1
                    if kind == "benign":
                        flags_list.append(f"FALSE ALARM {case.name[-40:]} {rel} {vname}: {found[0].message[:150]}")
                elif kind == "broken":
                    flags_list.append(f"missed {case.name[-30:]} {rel.split('/')[-1]} {vname}")
    print(f"{done:2} {case.name[-45:]} files={len(files)}", flush=True)
print(stats)
print("\n".join(flags_list))
