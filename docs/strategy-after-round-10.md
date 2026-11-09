# Why repairs fail, and where to turn (8 November, after rounds 8–10)

Written for the team after the first full day of real x86-target runs. Facts
from our own runs are in `docs/no-build-live-findings.md` (rounds 8–10); the
external facts carry their source.

## 1. Scoreboard

Eleven local cases, agent 0.6.0-rc.3 (main's rc.2 plus the architecture
model), no build feedback, one run each.

| Case | Direction | Result | Class of failure |
| --- | --- | --- | --- |
| rcran | amd64→arm64 | repaired | x86 intrinsics header reached on arm64 |
| libunwind | amd64→arm64 | repaired | outline atomics flag |
| rust-subversion | amd64→arm64 | repaired | `i8`/`u8` C-string pointers (29 edits) |
| html-xml-utils | amd64→arm64 | repaired (round 7) | |
| ectrans | amd64→arm64 | **repaired today** | dependency's CMake export names a missing directory |
| libbpp-qt | arm64→amd64 | **repaired today** | symbols file written for arm64 |
| python-intbitset | arm64→amd64 | **repaired on re-run** (2 turns) | `setup.py build_sphinx` removed; the agent disabled the step and broke the doc package's install contract |
| libevtx | arm64→amd64 | miss | a real amd64 segfault in a tool test, 4,100-line file, no diagnostic |
| pybigwig | arm64→amd64 | not repairable in-package; the agent now says so in 4 turns with no edit | the amd64 runtime library package is empty; ld used a non-PIC archive |
| libmceliece | amd64→arm64 | not repairable in-package | toolchain (BFD assertion) |
| libbpp-core | arm64→amd64 | build passes on re-run, by a degenerate edit (every symbol tagged); macro re-run queued | 13 missing symbols in a 4,131-line file; the model retyped the file in a syntax dpkg rejects. The agent now tags the symbols itself from the log (one macro edit). |

Eight builds pass after the round-11 re-runs (libbpp-core's by an edit a maintainer would reject). Two cases cannot be repaired by editing the package at all; for
those the best outcome is a one-turn, zero-edit stop with the reason, which
the new `nonpic_static_archive` block produces. Of the remaining misses one is
now covered (intbitset) and one is a genuine debugging problem (libevtx).

## 2. Why the misses happen (our evidence)

1. **The dependency side is invisible.** ectrans and pybigwig both failed
   because of what a *dependency* installed (a wrong module directory in
   `fiat-targets.cmake`; an empty `libbigwig0t64`). The prompt tells the
   model that files outside `input/` cannot be read, so it guesses. Two of
   four misses are this class.
2. **Consequence blindness.** intbitset: the model removed the failing step
   without asking what consumes its output. Without a build, nothing told it
   `dh_install` would fail next. Round 10 also showed the general shape: the
   log names the *first* failure (CMake stops at the first bad imported
   target), and a repair that covers only what the log names fails on the
   siblings.
3. **No stopping signal.** Before the static verdicts, rust-subversion ran to
   the 60-turn budget re-proposing edits already made (main's rc.2 still
   does: 60 turns, 1.49 M tokens). With verdicts it stops at 36.
4. **Needle in a haystack.** libevtx: a segfault in one of eight tool tests,
   no file or line in the log. Fifteen tool turns of reading, then an
   unrelated edit. No static evidence we have points at the bug.
5. **Malformed replies.** About one in four runs has a reply that does not
   parse; main's retry handles it, at a turn's cost.

## 3. What the literature says (fetched 8 November)

- **The Build-bench paper** (Zhao et al., arXiv 2511.00780v2; 268 OBS
  packages, 163 x86→arm, 105 arm→x86). With tools and **three build
  iterations**, GPT-5 reaches 63.19 % forward and 29.52 % reverse; without
  tools or feedback ("Bare-LLM") every model is between 1 % and 6 %. The
  authors' failure analysis: long interleaved logs, truncated output, wrong
  tool sequences. Their recommendation: "historical repair examples or
  ISA-specific documentation" as extra context. Reverse direction
  (arm→x86) is the hard one for everyone; compilation errors in that
  direction were GPT-5's worst cell (18 %).
- **GradleFixer** (arXiv 2510.08640v3): replacing a general shell with three
  narrow tools, one of which runs the build, lifted pass@1 from 49 % (no
  shell) and 54 % (shell) to 74 % under a 30-call budget. The gain came from
  the build tool and a narrow action space, not from model knowledge.
- **PhantomRun** (arXiv 2602.20284, MSR 2026): 7 B models repair up to 45 %
  of embedded CI compilation failures with the build in the loop and
  same-project fix examples in the prompt; hardware-dependency errors are
  the worst class (32 %).
- **ComBench** (arXiv 2603.27333): GPT-5 makes 73 % of C/C++ errors compile
  but only 41 % of the patches are semantically right; cascading cross-file
  errors are the common residue.
- **Patch ranking without execution** exists and works (entropy ranking,
  arXiv 2404.15236; verifier models, arXiv 2510.22775; best-of-k under
  throughput constraints, arXiv 2506.10056).
- **Tooling:** `buildlog-consultant` (Rust, GPL-2) classifies hundreds of
  build-log problems but none of our six cross-architecture signatures
  specifically, and it carries no fixes; `ognibuild`'s fixer only installs
  missing dependencies. The Debian FTBFS wiki is a short catalog of
  recurring causes with preferred fixes. No public corpus pairs FTBFS logs
  with the fixing diffs; Launchpad and buildd expose per-architecture logs,
  and the fix is in the next upload's debdiff.
- **The Arm MCP server** scans x86-specific C/C++ for the x86→Arm direction
  only, needs Docker and an embedding index, and covers less than
  `arch_slice.py`. Not usable in the submission.

The one lever every paper agrees on is **execution feedback**, and the
competition denies it to us. Our 6/11 against a 1–6 % no-feedback baseline
comes from substituting static evidence for that feedback. The plan below is
to push that substitution as far as the rules allow.

## 4. The plan, ranked by expected repairs per day of work

### A. Let the agent read the dependencies (highest value)

The kit's own runner copies the **entire case directory** into
`/workspace/work/repo` (`runner/run-agent-case.sh`: `cp -a
"$ORIGINAL_CASE/." "$WORKSPACE/work/repo/"`), and the patch is derived only
from `input/` (`generate_patch.py --allowed-prefix input/`). A case directory
holds `dependencies/<dist>-<arch>/*.deb` (296 packages, 182 MB for pybigwig)
and `config/dependency-lock.json`. Our tools refuse to read anything outside
`input/`: a rule we imposed on ourselves.

A `deb_contents` tool (list the files a dependency installs; read one text
file from it, such as `fiat-targets.cmake`) would have given the model, in
one call: the real fiat module directory (ectrans), the empty
`libbigwig0t64` (pybigwig), and the exact `INTERFACE_INCLUDE_DIRECTORIES`
line. A `.deb` is an `ar` archive with `data.tar.zst`; the `ar` header is
parseable with the standard library (and its member size alone exposes an
empty package), but zstd needs either Python 3.14 or the `zstandard` wheel.
The kit allows pinned dependencies (`requirements.lock`, `==`); the
standard-library-only rule is ours. Two things to settle first: (1) confirm
with a Hosted Smoke run that `/workspace/work/repo/dependencies` exists on
the organizers' platform (log a directory listing); (2) decide on
`zstandard==0.23.0` or a size-only fallback.

### A2. Make mechanical repairs mechanical

libbpp-core is the clearest case for the GradleFixer lesson (narrow tools
beat a general channel): the model knew the fix, "tag the missing symbols",
and failed at typing it. The agent now accepts a one-line macro edit and
computes the real edits from the log. The same shape applies to every repair
whose content is fully determined by the evidence: tagging symbols, adding
an exported flag under an architecture conditional, redirecting imported
targets. Each becomes a macro the block offers, with the model deciding
*whether*, not *how*.

### B. A consequence checker, not just an error checker

Generalise `verify.py` from "does the construct that produced the error
still produce it" to "what consumed the thing this edit changed":
`debian/*.install`, `*.docs`, `*.links`, `*.manpages` paths against the
build steps that produce them; removed or renamed make targets; files the
edit deletes that other files reference. The intbitset verdict is the first
instance. Each verdict is a line the model reads before it stops, which is
the only place feedback exists in a hosted run.

### C. Best-of-N on the first attempt, ranked by the static verdicts

Ask for two or three candidate repairs on the first edit turn (the hosted
endpoint uses default sampling; replies differ), apply each to a scratch
copy, run preflight, the syntax parse and the verdicts, and keep the one
with the fewest "NOT fixed" lines. The literature says verifier-ranked
best-of-k is the cheapest reliable gain when execution is unavailable. Cost:
two to three extra replies on one turn, well inside the budget that runs
like libbpp-qt (487 k tokens) already spend on wandering.

### D. Turn the recognisers into data

Six recognisers exist as code. The Build-bench authors' own recommendation
is "historical repair examples"; Debian's FTBFS wiki and the fixing uploads
on Launchpad (the next version's debdiff after an architecture-specific
FTBFS) are the corpus. A table of (log signature regex, facts to gather,
repair text, example) in a text file, retrieved by regex, is standard
library, reviewable by the whole team, and grows without touching the loop.
Start with the signatures we have met plus the FTBFS wiki's list.

### E. Check what the hosted runtime can execute

The agent image is `python:3.11.9-slim-bookworm` with `--network none`,
read-only, 1 CPU, 1 GB. It has **no C compiler**, so `syntax_guard`'s
compile check is inert in a hosted run (it skips when `gcc`/`cc` is absent).
Only the static evaluators run there. The next Hosted Smoke should log
`shutil.which("gcc")` and the `/workspace` listing so we stop guessing.

### F. Smaller items

Native function tools instead of our JSON action format would remove the
malformed-reply turns (the hosted docs say `tools` is supported). The
Build-bench paper found full-file output more successful but far costlier
than patches; our old/new edit format sits between and is fine.

### G. Not worth the days left

The Arm MCP server, embedding-based retrieval, a bigger model (not ours to
choose), or more loop iterations without new evidence.

## 5. Order of work to 13 November (final version confirm, UTC+8)

1. Today: queue intbitset and pybigwig re-runs with the new blocks; merge the
   rc.3 branch after review.
2. 9 Nov: Hosted Smoke of rc.3 with the runtime probe (E); `deb_contents`
   prototype with the `ar`-header fallback (A); the consequence checker for
   `.install` contracts (B).
3. 10 Nov: best-of-N on the first attempt behind a flag, measured on
   libunwind, rcran, intbitset, ectrans (C).
4. 11 Nov: signature table seeded from rounds 5–10 and the FTBFS wiki (D);
   Development Validation zip of the current agent.
5. 12 Nov: freeze; Hosted Smoke of the final ZIP; confirm on 13 Nov.

## 6. Sources

- Build-bench paper: https://arxiv.org/abs/2511.00780 (v2, May 2026); code https://github.com/zcyyc/Build-bench
- GradleFixer / AndroidBuildBench: https://arxiv.org/abs/2510.08640
- PhantomRun: https://arxiv.org/abs/2602.20284
- ComBench: https://arxiv.org/abs/2603.27333
- Patch ranking without execution: https://arxiv.org/abs/2404.15236, https://arxiv.org/abs/2510.22775, https://arxiv.org/abs/2506.10056
- buildlog-consultant: https://github.com/jelmer/buildlog-consultant ; ognibuild: https://github.com/jelmer/ognibuild
- Debian FTBFS causes and fixes: https://wiki.debian.org/qa.debian.org/FTBFS ; architecture facts: https://wiki.debian.org/ArchitectureSpecificsMemo
- Ubuntu FTBFS guide: https://ubuntu-packaging-guide.readthedocs.io/en/latest/ubuntu-packaging-guide/fixing-ftbfs.html
- Arm MCP server: https://learn.arm.com/learning-paths/servers-and-cloud-computing/arm-mcp-server/ ; https://github.com/arm/mcp
- Kit runner facts: `buildbench-starter-kit-0.1.0-rc.5/runner/run-agent-case.sh`, `runner/common.sh`, `runner/generate_patch.py`
