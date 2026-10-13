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
  Clang source/caret pairs are supported. ANSI colors are removed from parsed
  text, while byte offsets still refer to the original bytes.
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
  paths and symlink files/directories are not resolved. Absolute build-host
  prefixes are not guessed away.
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
