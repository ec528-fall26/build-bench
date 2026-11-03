# Part A — Case and Evidence Manager

Part A turns build logs into structured evidence for the repair loop. It does
not call the model, edit packages, or run builds. The frozen one-shot agent is
unchanged. Part C still needs to connect these APIs to its loop.

## Files and integration

Implementation lives in
`buildbench-starter-kit-0.1.0-rc.2/agents/full-agent/src/`:

- `evidence.py`: `diagnose(log_path, worktree) -> list[Diagnostic]`.
- `case.py`: `load_case(task_path, *, manifest_path=None, worktree=None,
  log_path=None) -> CaseInfo`.
- `contracts.py`: existing, unchanged `Diagnostic` interface.

From the full-agent directory:

```python
from pathlib import Path
from src.case import load_case
from src.evidence import diagnose

case = load_case(
    Path('/workspace/input/task.json'),
    worktree=Path('/workspace/work/repo'),
    log_path=Path('/workspace/input/initial-build.log'),
)
diagnostics = diagnose(case.log_path, case.worktree)
```

Pass the repository containing `input/`, not `input/` itself. A local caller can
also pass `manifest_path` to supply package/build metadata. Task fields win over
manifest fields. Package names may be strings or `package.name`; architecture
may come from `target_arch`, `target_architecture`, `platform.target_arch`, or
`build.architecture`. A Debian `.dsc` recipe supplies a package-name fallback.
Missing optional values remain `None`; architecture spelling is preserved.
Relative task paths are relative to `task.json`. Explicit host path overrides
are useful when replaying container metadata. Both original metadata objects
remain available without alteration. Missing requested files or invalid JSON
raise an error for the caller to report.

Part D can pass a newly produced log or excerpt directly to `diagnose`. There
is no dependence on the beginning of the original log. Part C should use the
existing `use_evidence_manager` flag when it integrates the component; this
change does not replace `main.py` or implement Parts B–E.

## Diagnostic behavior

- GCC/Clang errors retain the printed path, source line number, error message,
  and a source quote when the compiler supplies one. Numbered GCC quotes and
  Clang source/caret pairs are supported. ANSI colors and the local
  validator's elapsed-time prefix (`[   93s] `) are removed from parsed text,
  while byte offsets still refer to the original bytes.
- Linker undefined references, make failures, CMake/configure errors, Debian
  errors, Google Test `file:line: Failure`, and explicit build-killed messages
  are recognized. Ordinary output is not turned into a diagnostic.
- Exact repeated messages collapse to the earliest occurrence, even if a
  compiler reports that message for different files. Its first path and quote
  are retained. Different linker messages that include different object paths
  remain separate. Generic Google Test `Failure` messages collapse together.
- `dh_auto_configure/build/test/install` markers determine the active stage.
  Selected packaging commands also identify `packaging`. Marker-free excerpts
  stay `unknown`; a compiler error alone does not prove the build stage.
- `log_offset` is a **zero-based byte offset in the supplied file**, not a line
  number or an offset into a larger original log when supplied an excerpt.
- Printed paths match complete suffixes of real files under `worktree/input/`.
  Every match is returned in sorted order; nonexistent paths return `[]`.
  Leading `./` and `../` from out-of-tree builds are stripped. Interior `..`
  paths and symlink files/directories are not resolved. Absolute paths are
  resolved only under known build roots (see "Diagnostic context" below);
  other absolute paths, such as `/usr/include/...`, are not.
- Input logs and source files are never modified. Source quotes come from the
  log rather than a potentially edited worktree file.

The parser reads one binary line at a time and indexes source names lazily.
Memory depends on unique diagnostics, source filenames, and the longest line,
not total log size. It is not a fixed-memory guarantee for adversarial logs with
millions of distinct errors or one enormous line. Invalid UTF-8 uses replacement
characters. Missing/unreadable logs return `[]`; an I/O failure midstream returns
evidence already collected. **An empty result does not establish build success.**

Stage detection and error extraction are heuristics. A reported diagnostic may
be a downstream symptom or an expected configure probe. Only a verified build
establishes whether a repair succeeded.

## Package format — 1 November 2026

`load_case()` now also returns `case.package_format`: `name` is `"debian"`,
`"rpm"` or `"unknown"`, `basis` says what decided (`"metadata"`, `"files"` or
`"none"`), and `evidence` lists every observation, including disagreeing ones.
`agent-result.json` records it as `package_format`. The function is
`detect_package_format(task, manifest, worktree)` in `case.py`.

**Order of evidence.**

1. Case metadata: `package_type`, the build recipe (`.dsc` or `.spec`), the
   build backend and the expected artifacts (`.deb`/`.dsc` or `.rpm`), from
   task.json and then the case manifest. If these disagree, the result is
   `unknown` with both sides listed.
2. Only when metadata says nothing: packaging files at the top of `input/`.
   A `.dsc` means Debian. Otherwise `debian/control` means Debian and a
   `.spec` means RPM. If both are present without a `.dsc`, the result is
   `unknown`.

**Why not "any .spec means RPM".** 18 of the 200 public Debian cases ship an
upstream `.spec` file (for example `bpp-core.spec`, `libevtx.spec`,
`rpm/dnscap.spec`), and upstream projects can also ship a `debian/` directory.
Only the top level of `input/` is examined, and a lone file of the other kind
is never treated as proof.

**Worktree manifest.** Without an explicit `manifest_path`, `load_case()` now
reads the case's own `manifest.json` from the worktree when present; the
platform places it there (`/workspace/work/repo/manifest.json`). As a result,
`package` and `target_arch` are also filled from it when task.json lacks them;
task.json values still take precedence. An unreadable worktree manifest is
ignored and noted in the evidence; an explicitly requested one still raises.

**Regression fixture.** `tests/full-agent/fixtures/hello-rpm/` is the starter
kit's RPM `hello` case, copied unchanged. The agent had treated it as Debian
(see the design document's local readiness notes). `test_package_format.py`
checks that it is reported as RPM, from its metadata and, with the manifest
removed, from its files, and that `main()` records the format.

**Not done here (handoff).** Prompts still say "You repair Debian source
packages" and point at `input/debian/rules` (Part E). Using
`case.package_format.name` there, instead of assuming Debian, is the next step.

## Failure stage and diagnostic context — 1 November 2026

The Demo 2 sprint tasks 2–4 for Part A. Case analysis behind them:
[`part-a-case-analysis.md`](part-a-case-analysis.md).

**Failure stage (`src/failure.py`).** `classify_failure(log_path)` returns a
`FailureStage` with these fields:

- `stage`: one of setup, dependencies, configure, compile, link, test,
  install, packaging, none (the build succeeded) or unknown.
- `source`: what decided the stage.
- `validator_stage`: the validator's own stage event, when the log has one.
- `build_step`: for example `dh_auto_test` or `%build`.
- `repairable_in_package`: `false` for setup failures.
- `evidence`: at most 8 log lines, each at most 300 characters.

The validator's structured event (`[build-case] [event] {...}`) is preferred.
Inside the package build, the earliest specific message decides the stage. When
there is none, the running build step decides, and the evidence says it was
inferred. Setup evidence is reported, not explained: segmentation faults are
counted, not attributed to QEMU. `agent-result.json` records the stage as
`failure_stage`. On the Demo 2 audit's excerpts the classifier agrees with the
manual review on all 11 cases. For the five setup cases this mostly reflects
the validator's own event, which the review also used; the log-only rules have
their own tests.

Other validator stages before the package build (`backend_preflight`,
`manifest_validation`, `workspace`) count as setup. `dependency_preflight` and
`dependency_resolution` count as dependencies. Without a validator event, setup
is inferred only when the package build never visibly started, so a segfault in
a test is not setup. rpmbuild's "RPM build errors:" does not mark packaging,
because it follows a failure in any section. `main()` never fails because of
classification: an exception is recorded as `unknown`.

Speed: one pass with a byte prefilter. Logs over 32 MB are read from their last
32 MB, and the evidence says so. The classifier therefore adds at most about
1.5 s at startup, before the repair loop's budget clock starts.

**Diagnostic context (`src/evidence.py`).**

- gfortran's two-line errors (location line, quote, then `Fatal Error:` or
  `Error:`) become diagnostics with file, line, quote and resolved path. Before
  this, they were dropped, and on ectrans the model saw only `make` wrappers.
- Compiler, Fortran and linker diagnostics carry, in `compiler_details`, the
  last compile or link step printed before them: a compiler or libtool command,
  or a CMake/automake progress line, clipped to 600 characters.
- A missing Fortran module or header is checked against the package.
  - If found, the detail names the file.
  - If not, it says how many files were searched and that the module or header
    is expected from a dependency, the compiler or the system, or is generated
    during the build.
  - If the search stopped at its limit (400 Fortran files, 256 KB each), it says
    only that the module was not found in the files searched.
  - Searches are cached within one `diagnose()` call. The pattern stays on one
    line, so it cannot become slow on long runs of blank lines.
- Absolute paths under known build roots resolve to `input/`. The roots are:
  - `/usr/src/packages/BUILD/` (OBS),
  - `/<<PKGBUILDDIR>>/` and `/<<BUILDDIR>>/<dir>/` (the dataset's sbuild logs),
  - `/build/<dir>/` and `/build/<dir>/<dir>/` (sbuild),
  - `/home/abuild/rpmbuild/BUILD/<dir>/` and `.../<dir>/<dir>/` (rpm 4.20+).

  A stripped path is used only if it resolves; other absolute paths still do not.
- Diagnostics stay in the order printed. The prompt titles them that way, and
  the loop compares the first one. Generic errors were not moved after the
  specific ones: in the cases examined, the specific error is printed first
  once it is recognized.
- Errors printed before the package build starts (`dpkg-buildpackage: info:
  source package`, or rpmbuild's `Executing(%prep)`) are dropped once that
  start is seen. A repeat of the same message inside the build is kept. The
  successful RPM `hello` log no longer reports "ERROR: not all operations were
  successful." An excerpt without the start marker keeps everything.
- Line numbers in the log are read as at most 9 digits, so an absurd number is
  ignored rather than crashing the agent.
- Linkers named with their target (`/usr/bin/x86_64-linux-gnu-ld.bfd:`) are
  recognized, and linker warnings that name an object first (`ld: x.o:
  warning: ...`) are skipped. On pybigwig after the QEMU fix, the relocation
  error ("recompile with -fPIC") had been missed and `collect2` came first.

**Checked on unseen logs (2 November).** The six full logs in
`docs/evidence/qemu-fix-20261102/` were committed after this code was written.
The classifier gives:

- python-intbitset before the emulator update: setup, not repairable.
- libbpp-qt and libbpp-core: packaging (`dpkg-gensymbols: error`).
- pybigwig: link.
- libevtx: test (`FAIL: test_tools.sh`).
- python-intbitset after the update: compile, inferred from `dh_auto_build`.
  Its failure is a documentation command (`invalid command 'build_sphinx'`)
  with no specific message.

These agree with `five-case-results.md`, which was written by hand from the
same logs. The tests use these logs.

The `Diagnostic` contract is unchanged. The new context is carried in the
existing `compiler_details` field, which the prompts already show. Speed: about
1.4 times the previous time on a compile-heavy synthetic log (3.3 s against
2.4 s for 72 MB). The extra time is command tracking.

**Known gaps.**

- gfortran output from before GCC 10 (no `NN |` quotes), two-locus errors and
  parallel jobs interleaved between the location line and `Error:` are still
  reported, but without a location.
- Ninja prints `FAILED: target` before the compiler error, so that line comes
  first.

**Handoff.**

- Part C: `failure_stage.repairable_in_package == false` means a setup failure,
  which edits cannot fix.
- Part E: show `package_format` and `failure_stage` in the opening message.
- Part B or D: a way to list an installed dependency's files. This is what
  ectrans still lacks.

## Checks

### Results — 11 October 2026

- **200/200** public development logs produced at least one diagnostic. Full
  per-case results: [`evidence-report.json`](../tests/full-agent/evidence-report.json).
- The real rcran log produced `crc32c/crc32c_prefetch.h:18`, resolved to
  `input/src/crc32c/crc32c_prefetch.h`, with `#include <xmmintrin.h>`. The stage
  is `install`: this package compiles during `dh_auto_install`. Its byte offset
  is 138338, corresponding to log line 1995.
- The **2,370,517,311-byte libyuv log** completed in **69.36 seconds** on this
  Windows/Python 3.12.14 run and collapsed to four diagnostics. Its first Google
  Test failure is at byte 543748, source line 1859. Timing is one measurement,
  not a performance guarantee.
- **26 full-agent tests passed** on Linux, including symlinks. **58 harness
  tests passed**, including the frozen-baseline fingerprint. Windows Python
  3.12.14 passed the full-agent suite with one symlink test skipped.
- The starter-kit submission checker reported the full-agent folder valid.
  No model call, repaired-package build, hosted smoke test, or complete
  competition submission was performed for this component change.

The materials archive's SHA-256 is
`61adae40957fb644e9ca4381718129b301d7a0a270b416fa290e2630924a6912`.
All 200 original logs were scanned. Only the actual rcran header was materialized
under a validation worktree for source resolution; the other 199 cases measure
log extraction, not resolution against fully unpacked source trees.

Manual inspection compared these outputs with the original log bytes:

| Sample | Observation |
| --- | --- |
| r-cran-digest | Header path, line 18, include quote, and install marker agree |
| libyuv | Repeated Google Test equality failures collapse; the first location agrees |
| libbpp-core | Missing-symbol diagnostic agrees and is labeled packaging |
| gir2pas and sphde | Explicit 150-minute inactivity termination is captured |
| cysignals | The make failure is captured, but is a downstream symptom; active-stage tracking alone does not identify the root cause |

The initial scan missed three inactivity terminations. Adding the explicit
`E: Build killed` format and rerunning all logs increased coverage from 197/200
to 200/200. This does not mean all causes are understood or repaired.

Run from the repository root:

```sh
python -m unittest discover -s tests/full-agent -v
python -m unittest discover -s harness/tests -t . -v
python tests/full-agent/evaluate_evidence.py /path/to/development/cases \
    --worktrees /path/to/worktrees --output report.json
```

The optional worktree directory contains `<case-id>/input/`. Without it, the
evaluation command looks for each case's `source/input/`; packed source archives
alone cannot establish matches to individual source files. The report includes
log SHA-256s, byte sizes, diagnostic counts, timing, and the first five results
per case. Coverage means at least one recognized diagnostic, not a repair rate
or a measurement of root-cause accuracy.

Tests cover the rcran regression, deduplication, stages, byte provenance,
excerpts, quoted source, ambiguous/missing paths, traversal, symlinks, invalid
UTF-8, missing logs, metadata precedence, and streaming without whole-file reads.

On Windows, Git newline conversion changes the frozen-baseline fingerprint and
literal-edit fixtures. Run the harness on a Linux checkout or export with
`git -c core.autocrlf=false archive` to preserve repository bytes. Do not update
the frozen fingerprint to compensate for a local checkout conversion.
