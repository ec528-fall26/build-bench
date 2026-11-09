# Full Build-Bench Agent

## Purpose
The project's full agent: it reads the failure, investigates the package with
read-only tools, proposes a repair with a stated hypothesis, tests it when build
feedback is available, and revises. Plan: `docs/full-agent.md`.

Experimental Rust FFI coverage: set `BB_FFI_ANALYSIS=1` and
`BB_AGENT_USE_BUILD_FEEDBACK=0` to trace C-char parameters and simple pointer
returns, resolve explicit aliases/imports, recognize `CStr::from_ptr`, review
simulated repairs, and record coverage of actual edits (`ffi-contracts-v2`). This automatically
enables edit review and replaces broad Rust signedness pattern advice. It is a
bounded source analysis, not a compiler or proof of build success. Default is off.
Implementation scope and evaluation instructions: `docs/ffi-contract-analysis.md`
in the project repository. The environment flag must reach the agent process;
local exports do not change a hosted submission's defaults.
For an offline JSON coverage report without edits, models or builds, run
`python3 -m src.ffi_analysis --worktree /path/to/work/repo` from this agent directory.
The worktree must contain the unpacked source under `input/`. Macros, generics,
method dispatch and missing generated declarations remain unresolved.

## Status: wired; repaired rcran in 4 of 4 live runs (14 October)
All parts are connected (14 October): `main.py` diagnoses the failure log (Part A),
runs Part C's loop with Part E's prompts and parser, and writes
`agent-result.json` with every attempt, model turn and token count. Submitted,
it has no build feedback, so edits are investigated and revised but submitted
untested. Locally, `harness/run_case.py --agent full-agent` runs the same loop
with real builds (Part D). Results: `docs/part5-findings.md` §9c.

Version 0.6.0-rc.6 (9 November), rc.5 with two more bypass refusals:
- `src/rules_guard.py` refuses a make condition replaced by a constant in
  debian/rules (golang-avo: `ifeq (1,1)` excluded 15 test packages) and a
  function body reduced to a bare return (opentelemetry-cpp: `Shutdown()`).
  Found by reading rc.5's repairs of the 111 small public cases.
- The `optimize-guard-test-memory` recipe is removed: it turned off the
  package's memory-test wrappers in optimised builds. libevtx keeps
  `o2-on-test-segfault`.

Version 0.6.0-rc.5 (9 November): recipes, edit gates, readable dependencies, loop control
(`docs/campaign-2026-11-09/implementation.md`):
- `recipes/`: nine verified repairs as one registry (symbols-tag,
  strip-bsymbolic-functions, o2-on-test-segfault, optimize-guard-test-memory,
  serial-helper-build, cmake-redirect-imported-dirs, replace-removed-command,
  arch-flags, static-archive-shim). The model asks for one with an edit whose
  `old_text` is `@@recipe:<id>@@`; `expand_macros` re-recognises the log
  signature, checks the tree, and writes the exact edit, or refuses with the
  reason. `patterns.recognise` now ranks log blocks by distance from the first
  fatal error and each block leads with its one-line invocation;
- edit gates: `syntax_guard.symbols_lint` checks a whole `.symbols` file
  against dpkg's grammar (glued tag groups, no tag after the version, no
  `#MISSING` markers) and its findings are not waived by an unchanged resend;
  `edit_preflight` refuses a symbols file edit larger than twice the reported
  missing symbols and any single edit over 200 changed lines; `verify.py`
  answers "unknown" rather than "fixed" when it cannot see the line, and checks
  over-tagging; `rules_guard` refuses compiler-flag overrides on `dh_auto_*`,
  `DEB_BUILD_OPTIONS` tokens in `DEB_BUILD_MAINT_OPTIONS`, and `|| true` after
  build or test steps;
- readable dependencies: `dep_audit.py` opens the case's dependency debs and
  reports dangling `.so` links, empty runtime packages and CMake/pkg-config
  paths that do not exist, next to the architecture block (30 s cap, silent
  when irrelevant). `deb_archive` streams gz/xz/bz2/zst with a size cap; zstd
  uses the one pinned wheel `zstandard==0.25.0` imported lazily (every path
  works without it). `search` has time and byte budgets instead of a file
  count and says what it did not scan; every tool failure becomes a result
  line; an agent exception after a changed worktree is reported as completed;
- loop control: an all-fixed verdict allows two more read-only turns, then a
  controller stop; an identical tool request is answered with a stub that does
  not count; investigation notes at 8 and 12 tool turns; the reply format asks
  for the JSON object only, reasoning text is stripped, and a malformed reply
  is answered inside the same conversation; attempt feedback opens with a
  VERDICT line; turn records carry `finish_reason` and completion tokens.

Version 0.6.0-rc.4 (9 November), rc.3 with the guard rules of 0.5.2:
- `src/rules_guard.py`: refuses tagging a whole symbols file (`(optional)` or
  `(arch=...)`: libbpp-core's degenerate pass in rc.3) and breaking `dh $@`
  (html-xml-utils in a 0.5.1 run). The compiler check of `src/syntax_guard.py`
  can be switched off with `BB_SYNTAX_GUARD_COMPILER=0`, as in a hosted run
  that has no compiler.
- `src/loop.py`: a refused symbols edit becomes the symbols macro, once per
  file, so only the symbols the log reports missing are tagged. A refused edit
  resent unchanged is called out as such.

Version 0.6.0-rc.3 (8 November): an architecture model instead of a bare loop
(`docs/architecture-model.md`):
- `arch_model.py`: what differs between the source and target architecture,
  as data (macros, char signedness and `c_char`, triplets, intrinsics
  headers, outline atomics, long double, page size, endianness), rendered for
  the model as "what differs and what it breaks";
- `arch_slice.py`: a scan of the whole package for every construct whose
  build depends on the architecture (preprocessor guards, x86 intrinsics
  includes and calls, inline asm, Rust `cfg` and `i8`/`u8` C-string pointers,
  make conditionals and exported flags, `Architecture:` fields, per-arch
  symbols files, hard-coded triplets), each evaluated for the target and
  shown next to the error it is nearest to, with where its guard macro is
  defined. The model sees the places that matter in files it never opened;
- `predict.py`: exact evaluators for C preprocessor conditionals (with the
  target's macros and the project's own `#define`s) and GNU make conditionals
  (with `DEB_HOST_ARCH` and friends set for the target); tri-state, never
  guessing;
- `verify.py`: after each unbuilt batch, the initial errors are re-examined in
  the edited files under the target's facts (is the include still reached, is
  the flag exported on the path make takes, do `i8` pointers remain, are the
  missing symbols tagged, was a quilt patch edited) and the verdicts are shown
  with the attempt as the feedback the hosted run otherwise lacks;
- a correction gets two tool turns before it must decide (rust-subversion
  spent 20 of its 60 turns searching mid-correction). The other 3 November
  error, a hand-edited quilt patch (pybigwig), is refused by 0.5.x's
  `rules_guard` already;
- `patterns.cmake_imported_target` and `verify._cmake` (ectrans, 8 November
  live run): a dependency's exported CMake target names an include directory
  that does not exist on the target. Every version so far cleared the
  property of the one target CMake names, and the next configure failed on
  the two sibling targets from the same package. The recogniser finds the
  siblings in the package's own link lists (`parkind_${prec}` expanded
  through the file's `foreach`), the `find_package` line to insert after, and
  renders a loop that replaces each missing directory with the one
  dh-fortran-mod installed; the verdict after the edit says which targets are
  still uncovered, whether the property was cleared instead of redirected,
  and whether the repair runs before the import (where `if(TARGET)` is false).
  Live: ectrans repaired in 4 turns (the first repair of that case).
- the slice's closing line now says what the constructs it does not show
  evaluate to on the target (rcran spent six tool turns after a verdict of
  fixed looking for the other seven, all guarded);
- two more recognisers from the first real amd64-target runs (round 10,
  `docs/no-build-live-findings.md`): `sphinx_build_command` (python-intbitset:
  `setup.py build_sphinx` was removed by Sphinx 7; the block quotes the
  `.install` line that depends on the step's output and gives the one-line
  `python3 -m sphinx` replacement, and `verify._sphinx` calls a disabled step
  "NOT fixed: dh_install will fail with missing files") and
  `nonpic_static_archive` (pybigwig: the amd64 runtime package of the library
  is 3 KB and ships no shared object, so ld used the non-PIC archive; nothing
  in the package can fix that, and the block says to stop without an edit);
- a macro edit for symbols files (libbpp-core: 13 missing symbols, the model
  retyped 4,131 lines in a syntax dpkg rejects). The model sends one edit with
  old_text `@@tag-missing-symbols@@`; `edit_repair.expand_macros` turns it
  into one correctly tagged line per symbol the log reports missing, before
  preflight. Live: libbpp-qt repaired (first x86-target repair), libbpp-core
  to re-run with the macro;
- `deb_contents` (`src/deb_archive.py`): a read-only tool over the case's
  frozen dependency packages, which the kit's runner copies into the worktree
  next to input/. It lists what a dependency installs (sizes, symlink
  targets, a filter) and reads one text file from it. Standard-library `ar`
  and tar; zstd data members need Python 3.14, the `zstandard` wheel or a
  `zstd` binary, otherwise the member sizes are still reported (a runtime
  library package of 2 KB ships no library). On the real cases it answers in
  10 ms what decided ectrans and pybigwig: the fiat module directory and the
  complete imported-target list in `fiat-targets.cmake`, and the empty
  `libbigwig0t64`. The local harness now mirrors the kit's layout.

Version 0.6.0-rc.2 (8 November), output-verification reliability:
- `src/loop.py`, `src/model_adapter.py`: a malformed verifier response receives
  one format-correction retry on the same pending candidate, within existing
  turn, token and time budgets. A second malformed response stops with
  `output_verification_format_failed`; format failures are separate from
  semantic rejections. Explicit review approval is still required.
- `src/output_verification.py`: bounded producer evidence includes edited files
  and build configuration such as `setup.py`, even without optional packaging
  manifests. Unavailable and truncated evidence is labelled; it is not proof
  that the patch works or that an output is unnecessary.
- `src/request_trace.py`: optional `BB_MODEL_TRACE` captures exact requests and
  paired raw responses with matching call numbers. Responses use the suffix
  `.responses.jsonl`. Use a new destination for each run; files have mode 0600.
- Validation: all 670 tests passed on EC2 with fresh review disabled and enabled.
  Three fresh end-to-end python-intbitset trials independently repaired the case:
  one applied edit each, 9/11/13 model turns, and zero loop builds. Final build
  validation passed in every trial. This is evidence for this case/configuration,
  not a broad success-rate estimate or attribution to one individual change.
- The preceding eleven-case campaign matched the baseline's four repairs, with
  six unrepaired cases and one already-building case. Its intbitset failure did
  not reproduce in these three trials after the follow-up changes. Other cases
  have not yet been rerun with these changes. Feature defaults remain unchanged;
  output verification stays experimental and off by default.
- Results and limitations:
  [output-verification experiment](../../../docs/full-agent-output-verification-experiment.md).

Version 0.6.0-rc.1 (7 November), output-verification experiment:
- Prerelease based on 0.5.1. Earlier python-intbitset repairs replaced the
  documentation build with a no-op or a helper that did not create the HTML
  files required by the package. The new optional gate checks whether a
  proposed repair preserves those required outputs before ordinary review.
- `src/output_verification.py`: a separate model call assesses each complete
  batch editing `input/debian/rules`, using the original failure, source
  evidence, command references and exact pending edits. It identifies required
  outputs, their producers and unsupported assumptions; it runs no builds.
  Enable with `BB_EXPERIMENT_OUTPUT_VERIFICATION=1`; it also requires
  `BB_REVIEW_EDITS=1` and `BB_EXPERIMENT_PACKAGING_CONTEXT=1`. Default is off.
- `src/loop.py`, `src/proposal_review.py`, `src/model_adapter.py`: verification
  precedes ordinary review. A supported assessment still requires explicit
  approval of the displayed batch. Revisions replace the complete pending
  batch and are checked again; rejected assessments remain in subsequent
  repair prompts. Repetition and verification failures are bounded, and model
  calls consume the existing budgets. No iterative build feedback is added.
- `src/source_context.py`, `src/evidence_support.py`, `src/command_evidence.py`:
  context collection is separated from diagnostic parsing and supplies bounded
  packaging or CMake source evidence without requiring a tool request.
  Switches: `BB_EXPERIMENT_PACKAGING_CONTEXT` and `BB_EXPERIMENT_CMAKE_CONTEXT`;
  both default off. Command references distinguish documentation producers
  from packaging helpers.
- `src/prompts.py`, `src/model_adapter.py`: optional fresh review presents the
  original failure, relevant evidence and pending patch without the failed
  attempt narrative (`BB_EXPERIMENT_FRESH_REVIEW=1`, default off). Optional
  full initial-log context and repair guidance remain separate experiments:
  `BB_EXPERIMENT_LOG_MODE=full` and `BB_EXPERIMENT_REPAIR_GUIDANCE=1`.
- `src/rules_guard.py`, `src/loop.py`: a narrow guard rejects replacing a
  build-producing command with a no-op. Rejection feedback now distinguishes
  controller refusals from text-matching failures; edit validation is split
  into smaller helpers. The guard alone does not establish output preservation.
- Validation: 664 tests passed on EC2 with fresh review off and on, including
  tests exercising the enabled gate. On Mac, 662 passed and two unchanged
  syntax-guard compiler tests failed under Apple Clang because diagnostic
  handling expects GCC messages; this portability limitation remains open.
- A fresh end-to-end run repaired python-intbitset with one applied edit,
  five model turns and zero loop builds; the harness's final build passed.
  The full-case rerun is still in progress, so broader improvement is not yet
  established. Its configuration enables packaging context, fresh review and
  output verification, with build feedback off; full-log context, CMake
  context and general repair guidance are disabled.
- The running campaign uses a saved source snapshot still labelled 0.5.1;
  this prerelease label is prepared separately. Settings, experiment history
  and result provenance:
  [output-verification experiment](../../../docs/full-agent-output-verification-experiment.md).

Version 0.5.1 (7 November), syntax guard (`src/syntax_guard.py`):
- In the 0.3.3 Development Validation run (111 small cases, 36 passed), about
  29 of the 69 failed cases ended on an error other than the original, several
  introduced by the agent's own untested edit (xmbmon "void value not
  ignored", sphde "expected primary-expression", pam-tmpdir, libmbim), and
  seven failed at an earlier stage than the original log.
- Before a batch is written, each edited file is checked as it was and as the
  batch leaves it; only problems the batch adds count: Python compile, sh -n,
  JSON, brace/parenthesis and #if/#endif balance for C, C++, Rust and Go,
  ifeq/endif balance and space-indented recipe lines in Makefiles and
  debian/rules, block balance in CMake, and, when a compiler is on PATH,
  `-fsyntax-only` parser errors. The compiler check builds for the case's
  target (macros switched so an x86 host takes the arm64 or riscv64 `#if`
  branches), with the -D flags of the failing build's own compile command,
  and for an edited header it also compiles up to two files that include it
  (xmbmon: a macro made void on ARM fails only in its user, io_static.c).
  Missing headers are stubbed; lines that already failed before the edit do
  not count. Compiler time is capped at 45 s per batch and 120 s per run.
- Measured without a model (`experiments/syntax_guard_stress.py`, 30 real
  cases): 0 of 212 harmless edits flagged, 136 of 164 broken edits caught.
- A finding sends that item back as a focused correction (`Reason:
  syntax_check`); an unchanged resend is applied, so a false alarm costs one
  correction turn and never blocks a repair. Switch: `BB_AGENT_USE_SYNTAX_GUARD`.
- Measure it on a recorded run without a model or a build:
  `experiments/syntax_guard_audit.py <run_public out> --cases-root <cases>`.
- Debian symbols files: a line dpkg-gensymbols cannot parse (libbpp-seq on
  6 November: "... is not a valid version") or an `(arch=...)` tag with a GNU
  name such as aarch64, which never matches, is sent back the same way.

Version 0.5.1, architecture names (`src/archnames.py`):
- The case header now gives the target's Debian name, GNU triplet, compiler
  macro and the facts that usually decide a failure (char signedness, long
  double, atomics). The symbols advice in `patterns.py` wrote
  `(arch=!aarch64)` for an aarch64 target; it now writes `(arch=!arm64)`.
- The playbook covers riscv64, which the competition lists among the
  evaluated architectures (task.html) although no development case has it:
  unsigned char, 128-bit long double, no __float128, -latomic for small
  atomics, the __riscv macro.

Version 0.4.0 (6 November), test-suite failures (`src/test_failures.py`):
- 59 of the 200 public cases fail in the package's own tests, the largest
  stage, and their diagnostics were wrapper lines (`dh_auto_test: error: ...
  returned exit code 13`). When Part A classifies the failure as `test`, a block
  after the errors now names each failing test and its message (Python
  unittest and pytest, automake and Autotest, CTest and GoogleTest, go test,
  cargo, TAP, Perl, meson, LLVM lit, Haskell), keeps crash, assertion and hang
  lines, the end of the test output, and shows the test's source from the
  worktree. Measured on the 200 public logs: 58 of 59 test-stage cases get a
  block (33 with named failing tests), none of the other 141.
- The block repeats the rules: no disabling, skipping or deleting a test, or
  ignoring its failure (rules.html section 6); it lists the usual architecture
  assumptions a test exposes.

Version 0.3.3 (4 November), from the Demo 2 feedback; no change in behaviour:
- `loop.py`: `run_loop` (634 lines, 68 `if`s) is now a thin wrapper around
  `_Loop`, one method per stage of a turn (budgets, request, model call, tool
  turn, review, and the six steps of an edit batch: merge a correction,
  validate and repair, review, apply, build, feedback). The longest method is
  70 lines; the stage order is documented in the class docstring;
- `tools.py`: one function per tool and a dispatch table (`_WORKTREE_TOOLS`)
  replace the 338-line `if` chain;
- `model_client.py`: `BB_MODEL_BASE_URL` (another Bedrock region) and
  `BB_MODEL_ID` (another model) are read for development runs; the hosted
  settings are unchanged. The one-shot baseline keeps its own copy of this
  file on purpose: `agents/one-shot/` is frozen (tag `one-shot-v1`) and a
  submission ZIP must be self-contained, so the two copies have diverged and
  cannot share a module.

Version 0.3.0 (2 November), for submitted runs with no build feedback:
- the model can read and search the whole failure log (`read_log`, `search_log`);
  the log excerpt it is shown carries line numbers;
- an edit correction shows the whole file (up to 24,000 characters) and may use
  tools before correcting;
- the system prompt lists common cross-architecture causes, and with no build
  adapter it says edits are not test-built and that `stop` submits them;
- `Budget.max_tokens` is 3,000,000 (was 100,000, which ended runs after about
  six turns; the website sets no token limit), `max_completion_tokens` is
  16,384, and the loop allows 60 turns and 120 tool calls (30 turns ran out
  after 3 to 6 minutes of the 17 in the first live no-build runs);
- an edit that an earlier edit in its batch already made (a broad replace_all
  followed by a narrower one) is dropped instead of sent back for correction;
- 8 edit attempts (was 5), and the playbook says where a Debian package's
  flags take effect (debian/rules, DEB_CFLAGS_MAINT_APPEND) and to change Rust
  C-string types to c_char in every file at once.

Version 0.3.1 (3 November), from the no-build live findings
(`docs/no-build-live-findings.md`), all in `src/edit_repair.py` and the loop:
- a batch is never lost to one item: when an item's correction budget is
  spent, or a correction repeats an already rejected old_text, that item alone
  is dropped and the rest of the batch is applied (rust-subversion: 26 edits,
  none written);
- an old_text that matches one place in the file apart from indentation is
  corrected to the file's whitespace when that is unambiguous (two tabs for
  three, consistently: libunwind sent that ten times); otherwise the
  correction shows the exact lines with tabs and spaces made visible;
- no-op edits and exact duplicates are dropped before any correction is asked;
- a `DEB_*_MAINT_*` line added at make level in debian/rules gets `export`,
  which it needs to reach any compile; a line inserted into a backslash-
  continued Makefile variable is rejected with the fix; a GNU make conditional
  in Makefile.am gets a warning;
- the prompts say the build output predates every edit and never changes, so
  the model stops once its edit addresses the cause instead of re-checking.

Version 0.3.2 (3 November, evening), from the live comparison of 0.3.1 with
0.3.0 (`docs/no-build-live-findings.md`, round 5):
- `src/patterns.py`: a recognised cross-architecture failure (Rust C-string
  signedness, an x86 intrinsics header on ARM, ARM outline-atomics helpers, a
  dpkg-gensymbols mismatch) is named right after the errors, with the files
  and lines gathered from the worktree and the usual repair; advice only;
- an edit whose old_text lives in exactly one other file of the same type is
  moved there instead of costing correction turns (16 wrong paths on
  rust-subversion);
- an edit that only removes `export` from a `DEB_*_MAINT_*` line is dropped
  with the reason instead of being re-exported into a no-op (37 turns on
  libunwind).
- an edit whose old_text is gone from the file and whose new_text is already
  there is dropped as already made, instead of being sent back for correction
  (rust-subversion: 58 turns after the repair was complete).

Live, without build feedback (round 6 in the findings): 0.3.2 repaired
rust-subversion, libunwind and rcran, one run each; rust-subversion for the
first time.

| File | Part | Status |
| --- | --- | --- |
| `contracts.py` | shared | Agreed 7 October. Change only by team agreement |
| `evidence.py`, `case.py` | A | Streaming diagnostics, source-path resolution, and read-only case metadata loading. See `docs/part-a-evidence.md` |
| `tools.py`, `path_checks.py`, `diff_tracker.py`, `tool_timeout.py` | B | Eight read-only tools, including `read_log` and `search_log` over the original failure log; called by the loop, which caps tool calls per case (`max_tool_calls`) |
| `build_feedback.py` | D | `NoBuildFeedback`: returns `None` until the organizers publish the protocol. The local build callback lives in the harness, never here |
| `loop.py`, `candidates.py` | C (Juliette Jacques) | The repair loop and candidate retention. See `docs/part-c-loop.md` |
| `model_adapter.py`, `main.py` | C (wiring) | The loop's `next_turn`: rebuilds each request from the loop's snapshot with Part E's text, one model call per turn. `main.py` is the entry point; `run_agent()` is shared with the harness |
| `prompts.py`, `reply.py`, `config.py` | E (Owen Zhang) | Instructions and per-turn messages, reply text → `ModelTurn`, and the ablation switches. Used every turn through `model_adapter.py`. See `docs/part-e-prompts-evaluation.md` |
| `model_client.py`, `log_tail.py`, `edit_applier.py`, `types.py` | baseline Parts 2–4 | Copied, not shared, so the baseline stays frozen |

Tests: `tests/full-agent/` at the repository root.

Part B arguments, limits, and Part C integration:
[Read-only tools guide](../../../docs/part-b-tools.md).

Part E prompts, reply parsing, switches and the Demo 3 comparison:
[Prompts and evaluation guide](../../../docs/part-e-prompts-evaluation.md).

## Model access
Configured through environment variables, never through files in this folder.

**Hosted** (competition platform; Starter Kit rc.5 model-access guide). The
platform injects all three; the agent uses them unchanged:

- `BB_MODEL_BASE_URL` — the organizers' endpoint, ending in `/v1`.
- `BB_MODEL_NAME` — the model alias, sent as `model`.
- `BB_MODEL_TOKEN_FILE` — a file holding the task credential.

Hosted requests send only `model`, `messages`, `max_completion_tokens`,
`store: false` and `n: 1`, with no retries and no proxy settings from the
environment. The run stops within `HOSTED_SECONDS` in `main.py` (17 minutes;
Hosted Smoke allows 20). Invalid settings, or no successful model call, end
with `status: "no_fix"`, the failing stage and exit code 1.

**Development** (local runs; used when `BB_MODEL_NAME` and
`BB_MODEL_TOKEN_FILE` are unset):

- `BB_MODEL_API_KEY` — Amazon Bedrock API key (required for a repair).
- `BB_MODEL_BASE_URL` — endpoint base URL (default: Amazon Bedrock's
  OpenAI-compatible endpoint in `us-east-1`).

The development model is fixed in code (`MODEL_ID` in `model_client.py`).
Without a key the agent still completes, makes no request and proposes no edits.

Check and package from the Starter Kit root (`buildbench-starter-kit-0.1.0-rc.5/`):

```bash
./bb check --agent ./agents/full-agent
./bb package --agent ./agents/full-agent
```

## Ablation switches
`src/config.py` reads one variable per `AgentConfig` field. Unset means on, which
is how the competition runs the agent; experiments set them to `0` to switch one
capability off. They take effect once Part C's loop calls `config_from_env()`.

- `BB_AGENT_USE_EVIDENCE_MANAGER`, `BB_AGENT_USE_TOOLS`,
  `BB_AGENT_USE_BUILD_FEEDBACK`, `BB_AGENT_USE_HISTORY`

## Runtime
Python 3.11. The current implementation uses only the Python standard library.

## Workspace
The workspace defaults to `/workspace`. For local testing, its location
can be overridden using the `BB_WORKSPACE` environment variable.

Required inputs:
- `input/task.json`
- `input/initial-build.log`
- `work/repo/`

Output:
- `output/agent-result.json`

A status of `completed` indicates that the agent finished running.
Repair success requires independent build and artifact validation.

## Entry point
Run `python -m src.main` from the agent directory.
