# Full agent without build feedback: live findings (2–3 November 2026)

Updated 3 November, evening, with rounds 5–7: 0.3.1 against 0.3.0, the 0.3.2 changes, and 0.3.2 on all eleven local cases.

What the full agent does when it runs as it will be scored: no builds during the
run, edits submitted untested. Branch `agent-improvements`, full-agent 0.3.0
and its follow-ups (`03f030d`, `bb4a541`, `a08c822`).

## Setup

- ARM64 EC2 host, `harness/run_case.py --agent full-agent --live`, model
  `openai.gpt-oss-120b-1:0` through Bedrock. **Not the competition model**,
  which is the organizers' and undisclosed.
- `BB_AGENT_USE_BUILD_FEEDBACK=0`: no builds in the loop. The harness still
  validates the final worktree with one clean build, as the platform does.
- `--log` is the organizers' `target-failure.log` from the development
  dataset (`materials/downloaded-materials.tar.zst`). The EC2 validator
  bundles do not contain it; it was copied to `~/organizer-logs/<case-id>.log`.
- Cases: rcran (r-cran-digest), rust-subversion, libunwind, libmceliece
  (round 2 only). All amd64 → arm64, so they build natively.

## Hosted Smoke

full-agent **0.2.0** (ZIP sha256 `cd9a754e…f084`, commit `d19cf19`) passed
Hosted Smoke on 3 November: model access, agent execution and independent
repair validation of `hello`. It is not confirmed as final. 0.3.0 has not been
through Smoke.

## Results

| Round | Run id | Agent change before it | rcran | rust-subversion | libunwind |
| --- | --- | --- | --- | --- | --- |
| 1 | `v030-nobuild-1` | 0.3.0 | — expired Bedrock key (HTTP 401), no turns | — | — |
| 2 | `v030-nobuild-2` | (same) | **repaired**, 1 edit, 5 turns | 0 edits: 12 corrections on one item | 4 attempts, 30-turn limit |
| 3 | `v030-nobuild-3` | drop already-covered edits; 60 turns | **repaired**, 1 edit, 6 turns | 14 edits, build failed (54 type errors) | stopped itself, build failed |
| 4 | `v030-nobuild-4` | playbook: Debian flags, consistent `c_char`; 8 attempts | **repaired**, 4 attempts, 27 turns | 0 edits: correction budget | correction budget, build failed |
| 5 | `v031-*-1` | **0.3.1**: batch repair (`edit_repair.py`), notes to the model, no-build prompt rules | **repaired**, 1 edit, 5 turns, 44k tokens | 28/28 edits applied, build failed (111 type errors), 60-turn limit, 841k tokens | **repaired**, 5 edits, 54 turns, attempt limit |
| 5 | `v030-*-1` | 0.3.0 reference, same day, same key | **repaired**, 4 edits, 25 turns, 248k tokens | 39/39 edits applied, build failed (93 type errors), 60-turn limit, 1.50M tokens | **repaired**, 8 edits, 39 turns, attempt limit |
| 6 | `v032-*-2` | **0.3.2**: recognised patterns next to the errors; wrong-path and export-removal fixes | **repaired**, 1 edit, 4 turns, 33k tokens | **repaired**, 13 edits in 7 files, 60-turn limit, 1.12M tokens | **repaired**, 1 edit, 5 turns, 56k tokens |
| 6 | `v032-rust-subversion-3` | 0.3.2 plus the `already_made` drop | — | **repaired**, 9 edits in 7 files, 13 turns, stopped itself, 280k tokens, 272 s | — |

libmceliece (round 2) fails on a BFD linker assertion
(`elfnn-aarch64.c:9754`), most likely a toolchain bug; it is not a useful
measure of the agent.

## Round 5: 0.3.1 against 0.3.0 (3 November, evening)

One run each, no build feedback, final validation build only. Both versions
repaired rcran and libunwind and failed rust-subversion, so the batch repair
did not change an outcome; it changed the cost and showed the next two faults.

- **rcran**: 0.3.1 took 1 attempt and 5 turns (44k tokens); 0.3.0 took 4
  attempts and 25 turns (248k) to reach the same one-line fix plus three
  unneeded edits. The "the log does not change" prompt rule is the likely
  cause: the 0.3.0 model kept re-checking its edit against the original log.
- **libunwind**: both wrote the `-mno-outline-atomics` block and passed the
  build. 0.3.1 then spent 37 turns in a loop of its own making: the model
  decided `export VAR += value` was invalid make and proposed taking the
  `export` off; the controller re-added it (turning the edit into a no-op),
  rejected it as `no_change`, and the model tried again, until the attempt
  budget ended the run. The build still succeeded because the first four
  attempts were right. A 0.3.1 defect, fixed in 0.3.2 below.
- **rust-subversion**: both versions applied every edit (0.3.1: 28 of 28,
  after 0.3.0's earlier rounds lost all of them), and both repairs were wrong
  in the same way: the compiler says "expected `*const u8`, found `*const i8`",
  and the model cast every call site to `*const i8`, the exact opposite of the
  portable fix (`c_char`) that the playbook describes, which it never used.
  The error count went from 103 to 111 (0.3.1) and 93 (0.3.0). 0.3.1 also lost
  38 of its 60 turns to one batch: 16 edits named `src/client.rs` for
  functions that live in `src/delta.rs`, each rejected as `old_text_not_found`
  and given up one at a time.

Two conclusions. First, the controller faults are now cheap to find and fix
(two more below), but they decide the cost of a run, not its outcome. Second,
the outcome on rust-subversion is decided by whether the model applies the
known portable fix, and a page of playbook in the system prompt does not make
it do that when the compiler's message points the other way.

### Changes made for 0.3.2 (3 November, evening; unit-tested, live run pending)

1. An edit that only removes `export` from a `DEB_*_MAINT_*` line is dropped
   with the reason (the one-line form is valid GNU make and the flag needs the
   export) instead of being re-exported into a no-op and rejected.
2. An edit whose `old_text` is not in the file it names, and occurs exactly
   once in exactly one other file of the same type under `input/`, is moved to
   that file and applied, with a note (`edit_repair.retarget_path`).
3. **Recognised patterns** (`src/patterns.py`): when the evidence matches a
   known cross-architecture failure, a block right after the errors names the
   evidence and the concrete repair, with the facts gathered from the worktree.
   Four recognisers, one per failure we have seen: Rust C-string pointer
   signedness (lists the files and line counts with hard-coded `i8`, says to
   use `std::ffi::c_char` with `replace_all`, and says not to cast); an x86
   intrinsics header on ARM (finds the `#if HAVE_*` guard above the include
   and where that macro is defined, the rcran fix); ARM outline-atomics
   helpers (the exact `debian/rules` lines, the libunwind fix); and a
   dpkg-gensymbols mismatch (the missing symbols from the log diff and the
   symbols file to tag, untested on a live run). The block is advice with
   evidence; nothing is edited by it.

## Round 6: 0.3.2 (3 November, 19:00 UTC)

All three cases repaired, one run each, with a fresh key.

- **rust-subversion**, repaired for the first time in any run. Turn 1 was one
  `search` for `*const i8`; turn 2 was a batch of 20 edits, `replace_all`
  per file and pattern, exactly as the pattern block said. Nine applied (the
  other eleven were duplicates of changes the first nine had made, dropped
  with notes), every `i8` C-string pointer in the seven files became
  `std::ffi::c_char`, and the final build produced
  `librust-subversion-dev_0.0.8-3_arm64.deb` with validation passed. The
  pattern block is the only difference from round 5 that bears on the
  content, so it is the cause: the same model, told the files and the
  replacement next to the errors, did in one turn what it declined to do
  from the playbook twice.
- **libunwind**: 1 edit, 5 turns (the exact `debian/rules` block from the
  pattern), stopped itself. No export loop.
- **rcran**: 1 edit, 4 turns: `HAVE_MM_PREFETCH` made 0 under
  `#if defined(__x86_64__) || defined(__i386__)` in the config header the
  pattern block pointed at.

The cost that remains is on rust-subversion: the repair was complete after
turn 2, and the run then used 58 more turns (1.1M tokens, 12 minutes) on the
turn limit, searching for `*mut i8` and `as *const i8` that no longer existed
and proposing the same `replace_all` edits again, each rejected as
`old_text_not_found` and sent back for correction. The model does not
believe its own completed change because the log it is shown still lists the
errors, which the prompt rules say but do not make it feel. Added for
`v032-rust-subversion-3`: an edit whose old_text occurs nowhere in the file
and whose new_text already does is dropped at once with the note that the
change has already been made (`edit_repair.already_made`), instead of
costing a correction and the tool turns the model spends looking for it.
Result: `v032-rust-subversion-3` repaired in 13 turns and 280k tokens (from
60 turns and 1.1M), one attempt of 9 edits, and the model stopped itself.


## Round 7: 0.3.2 on the other eight local cases (3 November, 20:09–20:48 UTC)

One run each, same settings, about a dollar of model calls in all. With the
three cases above this covers all eleven local cases (ten bundles plus
rcran). Each failure is classified by comparing the final build's errors with
the original log.

| Case | Target | Outcome | Classification |
| --- | --- | --- | --- |
| html-xml-utils | arm64 | **repaired**: `nolto` and `-fno-lto` in debian/rules, 8 edits, 40 turns | plausible repair of the original `lto1: internal compiler error`; weak evidence, because the unchanged source also built locally on 19 October (different compiler) |
| ectrans | arm64 | not repaired: 1 edit to `CMakeLists.txt`, configure fails the same way | agent miss (CMake include path from the `fiat` import target) |
| libmceliece | arm64 | not repaired: `-fPIC` added in three files; the link still ends in `BFD assertion fail elfnn-aarch64.c:9754`, 62 times before and after | toolchain (binutils 2.41 assertion), not repairable by a source edit we know of |
| pybigwig | amd64 | not repaired: `repack_failed`; the agent edited `debian/patches/prepareForExternalLibBigWig.patch` by hand and dpkg-source rejected the malformed patch | agent fault, and a controller gap: editing a quilt patch directly can never produce a valid source package |
| libbpp-core | amd64 | not repaired: symbols file edited (the pattern block's advice); build segfaults in the QEMU chroot | environment (x86 emulation on the ARM instance) |
| libbpp-qt | amd64 | `infrastructure_error`: symbols file edited; chroot segfaults | environment |
| python-intbitset | amd64 | `infrastructure_error`: `setup.cfg` and rules edited; chroot segfaults | environment |
| libevtx | amd64 | `infrastructure_error`: `tests/Makefile.am` and rules edited; chroot segfaults | environment |

**Score on the eleven local cases with 0.3.2: 4 repaired** (rcran,
libunwind, rust-subversion, html-xml-utils), **2 agent misses** (ectrans,
pybigwig), **1 toolchain failure** (libmceliece), **4 undetermined** because
x86 emulation on this instance segfaults before the build starts. On the
seven cases the environment can judge, 4 of 7; the 19 October retest of the
same set had 1 of 11 (rcran), with the same four environment failures. The
four x86 cases need an x86 instance (or a working QEMU) before they say
anything about the agent; their edits are recorded in the run directories.

Next controller rule from this round: an edit under `debian/patches/` should
be refused with the reason (edit the patched source under `input/` instead;
dpkg-source applies the series and refuses a hand-edited patch), the way a
path outside `input/` is refused now.

## Round 8: 0.6.0-rc.3 (architecture model) against main's 0.6.0-rc.2 (8 November, 20:00–21:20 UTC)

Same four cases, same model (gpt-oss-120b via Bedrock), no build feedback,
one run each. rc.3 is rc.2 plus `arch_model`, `arch_slice`, `predict` and
`verify` (`docs/architecture-model.md`).

| Case | rc.3 (architecture model) | rc.2 (main) |
| --- | --- | --- |
| libunwind | repaired, 2 turns, 17.7 k in / 1.0 k out, 206 s | repaired, 4 turns, 34.2 k / 1.9 k, 205 s |
| rust-subversion | repaired, model stop at 36 turns, 2 attempts, 29 edits, 1.13 M / 28.8 k, 435 s | repaired, turn budget at 60, 4 attempts (2 empty), 30 edits, 1.49 M / 52.7 k, 592 s |
| ectrans | not repaired, 9 turns, 78.6 k / 6.2 k | not repaired, 7 turns, 49.7 k / 5.4 k |
| rcran (r-cran-digest) | repaired, 10 turns, 122 k / 5.5 k | repaired, 3 turns, 29.3 k / 1.4 k |

What the new blocks did:

- **rust-subversion**: after the second batch the prediction read "no
  `*const i8` or `*mut i8` C-string pointer types remain in the crate ...
  reply stop", and the model stopped. rc.2 had the same two real batches and
  then spent 24 turns proposing already-made replacements until the budget
  ended the run. The verdict is the stop condition the no-build loop lacked.
- **libunwind**: the prediction confirmed the exported flags on the arm64
  path of `debian/rules` after the first edit; the model stopped at once.
- **rcran**: the same four-line change in both, and rc.3's prediction said
  "the include of xmmintrin.h at line 18 is no longer compiled on arm64 ...
  reply stop". The model still spent six tool turns checking "other
  x86-specific intrinsics headers that may be included unconditionally"
  before stopping: the slice had told it the package has 9
  architecture-dependent constructs and shown the 2 nearest, so it went to
  look at the other 7 (blake3's guarded `immintrin.h` among them). Those
  were all evaluated "not reached on arm64" already; the slice should say so
  in one line instead of leaving a count. Changed for the next run.
- **ectrans**: no block fired (the slice has nothing for CMake imports and
  the verdicts had no CMake case), and both versions made the same wrong
  repair: `set_target_properties(fiat PROPERTIES INTERFACE_INCLUDE_DIRECTORIES "")`
  for the one target the log names. The validation build then failed on
  `parkind_dp` and `parkind_sp`, two more imported targets from the same
  libfiat-dev package, with the same error. The log never mentions them,
  because CMake stops at the first.

### ectrans, the actual cause

The error is "Imported target "fiat" includes non-existent path
`/usr/lib/aarch64-linux-gnu/fortran/aarch64-linux-gnu-gfortran-13/fiat` in
its INTERFACE_INCLUDE_DIRECTORIES". From the fiat-ecmwf 1.2.0-1 arm64 build
log on Launchpad: the package installs its modules under
`/usr/include/aarch64-linux-gnu/fortran/gfortran-mod-15/{fiat,parkind_dp,parkind_sp}/`,
but its exported `fiat-targets.cmake` names the gfortran-13 directory that
was only a build-time path. Ubuntu deleted that arm64 build from noble on
2024-04-10 ("patchelf/gfortran/debhelper breakage on !amd64"). ectrans itself
links `fiat parkind_${prec}` in `src/trans` and `src/programs`, so three
targets are checked and all three carry the bad directory. Clearing the
property, as both agents did, would also drop the module directory the
Fortran `use` statements need.

Added for the next run (unit-tested, 706 tests pass on the instance):
`patterns.cmake_imported_target` names the sibling targets from the package's
own link lists, the `find_package` line to insert after, and a CMake loop
that redirects each missing directory to the one dh-fortran-mod installed;
`verify._cmake` predicts after the edit which targets remain uncovered,
whether the property was cleared instead of redirected, and whether the
repair sits before the import. Live result on ectrans: round 9.

## Round 9: ectrans with the CMake recogniser (8 November, 21:28 UTC)

**Repaired**, first time for any version. 4 model turns (one malformed reply,
one tool call, one edit, stop), 43.5 k input / 1.9 k output tokens, 225 s.
The edit is the loop from the block, placed after `ecbuild_find_package( NAME
fiat REQUIRED )` in the top-level `CMakeLists.txt`, covering `fiat`,
`parkind_sp`, `parkind_dp`; the prediction after it read "[fixed]" for all
three and the model stopped. The validation build configured, compiled,
linked and produced `libectrans-0_1.3.0-1build1_arm64.deb`, `libectrans-dev`
and `ectrans-utils`.

Two caveats, both inside the test phase that `debian/rules` ignores (`dh_auto_test
|| true`, "Ignore test failures; just log" in the changelog): 97 of 156 ctest
cases failed, and the `test_install` sub-build reports the same non-existent
fiat path on ectrans's own exported target `trans_dp` (ecbuild propagates
fiat's interface directories into ectrans's export file). Whether the 97
failures also occur on amd64 is not known from this run. A fuller repair
would also rewrite the installed `ectrans-targets.cmake` in
`override_dh_auto_install`, as the rules file already does for another path.

Local cases repaired so far: rcran, libunwind, rust-subversion,
html-xml-utils, ectrans (5 of 11). The x86 cases wait for QEMU to be
re-registered on the instance after its restart.

## What went wrong

1. **A failed correction discards the whole batch.** rust-subversion round 4
   proposed 26 edits; one (index 11) was `no_change`. Corrections go one item
   at a time and the budget is 12, so the run ended with 0 edits written,
   which is a certain failure.
2. **Identical corrections repeat.** libunwind round 4 sent the same old_text
   10 times (13,091 input tokens on each turn). The real line has three tabs;
   the model wrote two. The file is 46 KB, over the 24,000-character full-file
   limit, and the identifier-ranked windows showed the aarch64 variable, not
   line 1242. The model barely used the tools it may use during corrections.
3. **Small mistakes are not caught without a build.** libunwind round 4,
   attempt 1, followed the playbook but wrote `DEB_CFLAGS_MAINT_APPEND += …`
   without `export`. The flag reached no compile command (0 occurrences in the
   final log). It then added `-shared-libgcc` guesses and a GNU make `ifeq` to
   `Makefile.am`. In round 3, one edit split a backslash-continued variable.
   rust-subversion mixed `u8` and `c_char` across files.
4. **The original log is read as current.** rcran round 4 had the right edit
   at turn 2, then spent 25 turns re-checking it against the pre-edit log and
   made 3 more edits (commenting out, then deleting, the include). It still
   passed, but over-editing is how a passing case breaks.
5. **Fixed during these rounds:**
   - A broad `replace_all` followed by a narrower edit left the second edit
     nothing to match. In round 2, all 12 rust-subversion corrections were
     spent re-reading the file on disk, which never shows pending edits. Such
     edits are now dropped (`edit_repair.prune_batch`).
   - The 100,000-token cap ended runs after about 6 turns. The cap is now
     3,000,000; the website sets no token limit for Smoke, and time is the
     limit.
   - With 30 turns, runs used only 3–6 of their 17 minutes.

## Conclusions

1. The controller's failure handling now costs more than the model's
   reasoning. Under no-build, all-or-nothing batches are the wrong default.
2. The model's diagnosis is often right, and the misses are small details
   that one build would show. Static checks the agent can run itself would
   help more than more prompt advice.
3. The prompts must say plainly that the log predates the edits and will not
   change.
4. Turn and token limits are no longer binding.
5. These are 3 cases over 4 rounds on one model that is not the competition
   model: directions, not measured improvements. The playbook is drawn from
   patterns in the development cases, so local results on those cases are
   optimistic.

## Next steps, by expected value

These are improvements, not breakthroughs. None is likely to flip
rust-subversion. The `export` check might flip libunwind, but it is written
for the mistake we saw.

Steps 1–4 are implemented in full-agent **0.3.1** (3 November,
`src/edit_repair.py`; unit-tested, not yet run live):

1. ~~When the correction budget runs out, apply the edits that validate instead
   of none, and drop `no_change` items automatically.~~ Done: the unfixable
   item is dropped and the rest applied; no-ops and exact duplicates are
   dropped before any correction is requested.
2. ~~Say in the prompts that the log will not change during the run, and that
   the model should stop once its edit addresses the cause.~~ Done, in the
   system prompt's no-build rules and in every attempt result.
3. ~~Drop a correction item whose reply repeats an already-rejected old_text.
   Locate the intended line with whitespace-insensitive matching and show its
   exact whitespace.~~ Done: a whitespace-only mismatch is corrected to the
   file's text before any correction turn; a repeated rejected old_text drops
   the item at once.
4. ~~Add edit pre-check warnings for `DEB_*_MAINT_APPEND` without `export`, a
   line inserted into a backslash continuation, and `ifeq` in `Makefile.am`.~~
   Done: `export` is added; the continuation break is rejected with the fix;
   `ifeq` in Makefile.am is a warning on the applied edit.
5. ~~Run 0.3.1 against 0.3.0 live.~~ Done (round 5): no outcome changed;
   two further controller faults found and fixed in 0.3.2. ~~Run 0.3.2 on
   rust-subversion.~~ Done (round 6): repaired, the first time. Still to do:
   the cases not yet tried without builds (html-xml-utils, ectrans and the
   five x86 cases under QEMU). ~~A second rust-subversion run to see whether
   the `already_made` drop ends the run early.~~ Done: 13 turns, model stop.
6. More recognisers, one per failure class in the local cases and in the
   playbook: the symbols-file one is written but untested live (libbpp-core,
   libbpp-qt); `-fPIC` relocation errors (libmceliece); CMake include paths
   (ectrans). Each needs its evidence from the worktree, not just advice.

The largest lever is outside the agent. The website mentions that multiple
repair rounds with updated logs may be arranged. Every failure above is
something one build would have shown, so a second round would be worth more
than any of these changes. Ask the organizers or watch for the announcement.

## Round 10: the x86-target cases, first real runs (8 November, 22:09 UTC onward)

With QEMU 10.2 registered (next section) the amd64-target cases build for
real on the arm64 instance. 0.6.0-rc.3, no build feedback, one run each;
the emulated validator build takes 10–45 minutes per case.

| Case | Outcome | What happened |
| --- | --- | --- |
| python-intbitset | not repaired, 6 turns, 36.9 k in | `error: invalid command 'build_sphinx'` (Sphinx 7 removed its setup.py command; the step is -indep, so arm64 never ran it). The agent renamed the override target to `-disabled`; the build then failed in `dh_install` because `python-intbitset-doc.install` expects `docs/_build/html/`. |
| libevtx | not repaired, 27 turns, 404 k in | `test_tools.sh`: the `message_handle` tool test segfaults on amd64, in the original Launchpad log and in the validation build alike. 15 tool turns, then an unrelated edit (an unconditional `#include <signal.h>`). A real x86-64 bug in 4,100 lines of `evtxtools/message_handle.c`; not located. |
| pybigwig | not repaired, 9 turns, 91 k in | `libBigWig.a(bwValues.o): relocation R_X86_64_PC32 ... recompile with -fPIC`. The agent added `-fPIC` to the package's own CFLAGS, which cannot recompile the archive. Cause: the amd64 `libbigwig0t64` runtime package is 3,066 bytes and ships no shared library, so `libBigWig.so` dangles and ld used the non-PIC archive. The dfsg tarball has no bundled libBigWig sources, so no in-package repair exists. |
| libbpp-qt | **repaired**, 38 turns, 3 attempts, 487 k in | dpkg-gensymbols: two, then one more symbol missing on amd64; the model tagged them `(optional)` following the round-6 block. The first x86-target repair of any version. |
| libbpp-core | not repaired, 60 turns (budget), 7 attempts, 611 k in, 2 malformed | The same error with 13 missing symbols in a 4,131-line file. The model retyped the whole file, put `(optional)` after the version (dpkg: "(optional) is not a valid version"), and never recovered. |

Added for the next run (unit-tested):

- `patterns.sphinx_build_command` + `verify._sphinx`: names the `build_sphinx`
  line, reads `setup.cfg`'s `[build_sphinx]` source and build directories,
  quotes the `.install` line that depends on the output, and gives the
  one-line replacement `python3 -m sphinx -b html docs/ docs/_build/html`;
  the verdict calls a renamed or deleted step "NOT fixed: dh_install will
  fail with missing files" and the direct call "fixed". Checked against the
  live run's worktree: the agent's actual edit gets NOT fixed, the proper
  one gets fixed.
- a **symbols macro** (`patterns.SYMBOLS_MACRO`, `edit_repair.expand_macros`):
  the block now tells the model to send one edit whose old_text is
  `@@tag-missing-symbols@@`; the agent reads the dpkg-gensymbols diff from
  the log, finds each missing symbol's line in the named file and prefixes
  the tag in dpkg's syntax (`(optional)` or `(c++|optional)`), reporting the
  count. The model never retypes a symbols file again; `verify._symbols`
  also calls a tag placed after the version "NOT fixed". Tested in the loop.
- `patterns.nonpic_static_archive`: for the pybigwig shape, finds the
  archive and relocation in the log, looks up the runtime package's download
  size in the same log (3,066 bytes), explains that the package's own
  `-fPIC` cannot help, and tells the model to stop without an edit unless
  the library's sources are bundled. A correct "cannot be repaired here" in
  one turn is worth more than nine turns and a wrong edit.

## Round 11: re-runs with the round-10 blocks (9 November, 00:24 UTC onward)

| Case | Outcome | What happened |
| --- | --- | --- |
| python-intbitset | **repaired**, 2 turns, 1 edit, 976 s | The one-line change the block prescribed (`python3 -m sphinx -b html docs/ docs/_build/html` in the unchanged override target); the verdict read fixed; stop. |
| pybigwig | correctly not repaired: no edit, 4 turns, 18 s, 51.6 k in | The model's stop reason: the static archive is not PIC, the package has no libBigWig sources, the runtime library package is broken; "the fix must be made in the libbigwig package". Nine turns and a wrong edit in round 10. |
| libbpp-core | **build passes**, but by a degenerate edit: attempt budget (8), 25 turns, 323 k in | The model ignored the macro and prefixed `(arch=!amd64) ` to all 4,130 symbol lines without the leading space, so dpkg-gensymbols read each as a library header line named `(arch=!amd64)`; it then only warns ("some libraries disappeared: (arch=!amd64)", "new symbols appeared") and the package builds, with its amd64 symbols check effectively disabled. The validator counts it; a maintainer would not. Two causes found in the run: the symbols verdict counted every `-` line of the whole log ("202 missing") and the model took `#MISSING:` for file content. Both fixed; re-run queued. |

Local tally after round 11: rcran, libunwind, rust-subversion, html-xml-utils, ectrans,
libbpp-qt, python-intbitset, libbpp-core (degenerate; see round 12) repaired (8 of 11).
pybigwig and libmceliece cannot be repaired inside the package; libevtx
needs the crash located.

## Round 12: the symbols macro and deb_contents, live (9 November, 01:24 UTC)

| Case | Outcome | What happened |
| --- | --- | --- |
| libbpp-core | **repaired properly**, 1 attempt, 17 turns, 248 k in, 2,340 s | The model sent the one macro edit; the agent tagged the 12 distinct symbols the log reports missing with `(arch=!amd64)` in dpkg's syntax and told the model so ("Macro edit expanded: tagged 12 of the 12 symbols"); the model stopped. 13 changed lines in a 4,131-line file, against 4,130 in the degenerate round-11 edit. |
| pybigwig | correctly not repaired: no edit, 10 turns, 115 k in, 50 s | The model called `deb_contents` five times on its own and stopped with the exact cause: "the shared libBigWig.so provided by libbigwig-dev is a broken symlink (the actual shared library is missing in the runtime package). This cannot be fixed by editing pybigwig's source or build files." That is the fact the round-10 agent guessed wrong and the round-11 agent asserted from the log alone; now it is read from the dependency package. |

Local tally after round 12: 8 of 11 build, all by edits a maintainer could
accept (rcran, libunwind, rust-subversion, html-xml-utils, ectrans, libbpp-qt,
python-intbitset, libbpp-core); pybigwig and libmceliece are correctly
reported as not repairable inside the package; libevtx remains open.

## Round 13: the multi-agent campaign (9 November, 00:30-04:30 UTC)

Two orchestrated campaigns (76 Sonnet 5.5 agents, about 6 M tokens) audited every
case, reviewed the agent, swept the literature and the Launchpad archive, and
then settled the open cases by running candidate fixes through the real
validator. The deliverables are in `docs/campaign-2026-11-09/`: `brief.md`,
`case-table.md` (the authoritative per-case account, superseding the
narratives in rounds 5-12 where they differ), `experiments.md`, and
`master-plan.md`. The verified fixes are replay files in `harness/replays/`.

What changed in the picture:

- **Every local case has a validator-accepted fix (11 of 11).** Four were
  thought unrepairable or unexplained. libmceliece: strip Ubuntu's default
  `-Wl,-Bsymbolic-functions` (the BFD assertion's trigger; bindnow and LTO
  are not). libevtx: the libyal fault-injection test harness under amd64's
  default `-O3`; Ubuntu's own fix (`-O2` on amd64) and a one-token header
  guard both pass. pybigwig: the archive need not be PIC; a hidden `stderr`
  shim plus `-Wl,-Bsymbolic` links it, validator-verified. html-xml-utils:
  a parallel-make race on `cexport`; a serial prebuild passes with LTO and
  hardening kept.
- **Two accepted repairs were accidents** (libbpp-qt: unparseable tag
  silently dropped by dpkg; html-xml-utils: flags stripped). Both have
  verified proper fixes now. "Validator pass" is not proof of a good edit.
- **Structural defects in the agent**, from the reviews: the model never
  sees its own earlier turns; 91 of 543 replies are reasoning-only with no
  action; the symbols syntax guard rejected the valid form for 13 turns;
  `search` stops after 100 files; our recognisers fire on 12.5 % of 200
  public development logs. Hidden-set estimate from 108 classified Launchpad
  failures: test-suite failures are the largest class (about 27 %).
- **Wheels verified in the exact hosted image** on both architectures:
  `zstandard==0.25.0` makes 3,305 of 3,394 dependency debs readable.

Implementation of the master plan starts in round 14.

## Round 14: 0.6.0-rc.4 live on all eleven cases (9 November, 06:09-07:48 UTC)

The implemented plan (recipes, gates, readable dependencies, loop control;
`docs/campaign-2026-11-09/implementation.md`), gpt-oss-120b, no build
feedback, one run each. Instance runs dir `~/runs-060rc4`.

| Case | rc.4 | rc.3 (round 8-12 best) |
| --- | --- | --- |
| rcran | repaired, 3 turns, 20 k in | repaired, 10 turns, 122 k |
| libunwind | repaired, 4 turns, 37 k | repaired, 2 turns, 18 k |
| rust-subversion | repaired, 13 turns, 227 k | repaired, 36 turns, 1.13 M |
| ectrans | repaired, 5 turns, 57 k | repaired, 4 turns, 43 k |
| libmceliece | **repaired**, 2 turns, 12 k (first time) | not repaired |
| html-xml-utils | **repaired properly**, 4 turns, 46 k (serial cexport, LTO kept) | repaired by accident (0.3.2) |
| libbpp-qt | repaired, 22 turns, 180 k | repaired by accident, 38 turns, 487 k |
| python-intbitset | repaired, 3 turns, 26 k | repaired, 2 turns |
| libbpp-core | **not repaired**: 11 of 12 symbols tagged, stopped with a NOT-fixed verdict outstanding, 26 turns, 426 k | repaired (macro), 17 turns |
| pybigwig | **not repaired**: the model wrote its own partial unpack-the-archive edit without the stderr shim and -Bsymbolic, 15 turns, 150 k | correct stop (0) |
| libevtx | not run: HTTP 401, the Bedrock key expired at 07:48 UTC | not repaired |

Eight of ten that ran. The loop controls paid off (rust-subversion 36 to 13
turns, libbpp-qt 38 to 22, rcran 10 to 3), the gates held (no degenerate pass),
and two never-repaired cases repaired with maintainer-quality edits.

The miss that matters: **the model never invoked a recipe.** In all eleven
runs the `@@recipe:<id>@@` sentinel appears only inside its reasoning, where it
reads it as "a placeholder I must replace with real file text" and then
hand-writes the edits. libbpp-core and pybigwig were lost exactly there: the
hand-written versions were incomplete (one symbol of twelve; no shim and no
`-Bsymbolic`), the verdict named the remaining symbol truncated to 80
characters so the model could not find it, and the model stopped with a
NOT-fixed verdict outstanding. Round 15 makes the recipe path the obvious one
(a dry-run diff preview in the opening message, an invocation the model cannot
misread, controller completion of a partial symbols repair, a one-time stop
bounce on an outstanding NOT-fixed verdict, and a static-shim verdict).

## Round 15: the recipe path, live (9 November, 15:06-16:20 UTC)

The three cases rc.4 missed or did not run, re-run with the round-15 changes
(dry-run diff preview, unmistakable invocation, controller completion, NOT-fixed
stop bounce, static-shim verdict; `docs/campaign-2026-11-09/implementation.md`).
Same model, no build feedback, one run each.

| Case | Outcome | Turns | Tokens in | What happened |
| --- | --- | --- | --- | --- |
| libevtx | **repaired**, first time ever | 2 | 13.7 k | one macro edit (`o2-on-test-segfault`), the four-line `ifneq` block, stop |
| libbpp-core | **repaired** | 2 | 22.5 k | one macro edit (`symbols-tag`), 12 glued tags (`optional=templinst` / `optional`), stop |
| pybigwig | **repaired**, first time ever | 7 | 75 k | a hand-written first attempt, the static-shim verdict named the missing shim and `-Bsymbolic`, the second attempt invoked the recipe |

With round 14's eight, **all eleven local cases are repaired live by 0.6.0-rc.5**,
each by an edit the validator accepted and, pybigwig's workaround aside, one a
maintainer could upload. The decisive change between rounds 14 and 15 was not a
new recipe but making the existing ones legible to the model: a preview of the
exact diff and an invocation it cannot mistake for file text.

Caveats: one run per case, the eleven cases are in-sample (every recipe was
written after its case), and the hidden set's class mix is an estimate. The
held-out replay protocol in the master plan is the next measurement.

## Round 16: 0.6.0-rc.9 on the public cases (13 November, 05:40-07:15 UTC)

Source: the rc.5 Development Validation logs (111 cases, 53 passed; triage in
`docs/devval-rc5-triage.md`). The public cases ship no frozen dependencies, so
the validator cannot build them here. Method instead: `harness.run_public`
(live gpt-oss-120b, no builds) on 29 public cases (the 24 non-passing ones
picked from the triage plus kdsoap, grantlee5, kf6-frameworkintegration,
rocm-device-libs and qmidiarp), then for arm64-target cases a native
`dpkg-buildpackage` in an arm64 container of the case's Ubuntu release with
today's archive. That is an approximation of the validator, not the validator.
Runs: `~/bb-public-runs/{rc8,rc9,rc9b,rc9c,rc9d}` on the EC2 host, builds under
`~/scratch-verify/`.

What the runs showed, in order:

1. rc.8 baseline, lib1305: the log has the libmceliece linker assertion and
   the prompt carried the verified `strip-bsymbolic-functions` block. The model
   wrote `export DEB_LDFLAGS_MAINT_STRIP =` (empty) and edited
   `build/Makefile`. The recipe was never invoked.
2. First run with the controller applying the recipe before the model's first
   turn: the model's next edit deleted the strip line ("the flag added via
   DEB_LDFLAGS_MAINT_STRIP causes the assertion"). Cause: the static prediction
   after the recipe read "NOT fixed: debian/rules:13: DEB_BUILD_MAINT_OPTIONS
   is inside a conditional that is false on arm64 ... address it in the next
   edit". Line 13 is the maintainer's own armhf block. `verify._rules` judged
   every MAINT line in the file, not the lines the edit wrote. Fixed; the
   controller now also ends the run when the prediction is not "not fixed".
   It acts only when the best-ranked recipe is on an explicit list
   (`AUTO_APPLY_IDS` in `src/loop.py`). `o2-on-test-segfault` is not on it:
   its signature is a symptom (python-msgspec matched it and passed in rc.5
   with a different edit), so it stays the model's call as before.
3. Native builds of lib1305 (resolute, arm64): unmodified source FAILS with
   the BFD assertion (3 lines); with the controller's edit it PASSES (3 debs).
4. GCS warning (efibootmgr, kdsoap, grantlee5, kf6-frameworkintegration): not
   reproducible with today's archive, because the dependencies have since been
   rebuilt with the GCS note. Reproduced with the logs' binutils 2.45 and
   library versions from snapshot.ubuntu.com, then real builds:

   | edit | efibootmgr | kdsoap |
   | --- | --- | --- |
   | none | FAIL (same warning and collect2 error as the log) | FAIL (same) |
   | add `export` to the existing, unexported line | PASS | n/a |
   | `export DEB_LDFLAGS_MAINT_APPEND += -Wl,-z,gcs-report-dynamic=none` in `ifeq DEB_HOST_ARCH arm64` | PASS | PASS |
   | `-Wl,--no-fatal-warnings` | FAIL (the package's makefile appends `--fatal-warnings` after LDFLAGS) | PASS |
   | `-Wl,-z,nogcs` | FAIL ("-z nogcs ignored" is itself a warning) | not run |
   | `hardening=-branch` | PASS, but turns PAC/BTI off | PASS |

   grantlee5 and kf6-frameworkintegration were not built. In rc.9 all four get
   the recipe from the controller with no model turn.
5. xmbmon: the whitespace repair makes the edit apply (rc.5 and rc.8: no
   change submitted). The repair still fails natively. One run kept
   `#define SET_IOPL() ((void)0)` although `io_static.c` tests its value; the
   syntax check had found it and an unchanged resend put it through, and the
   build failed on that line. With the finding made final and the use site
   named, the model stopped writing it, and both runs then fail on the inline
   x86 port I/O (`impossible constraint in 'asm'`, or `my_inb` undeclared once
   it is guarded). Not repaired.
6. rapmap: the edit put two functions between a function's header and its
   `{`, as in rc.5. The syntax check dropped the finding because the
   neighbouring lines already had errors (SIMD types it cannot see). It now
   counts a "before '{' token" error on a line the edit wrote. The next run
   avoided that file and broke `KSW2Aligner.cpp` instead. Not repaired.

Native arm64 builds of the rc.9 repairs (today's archive):

| case | result | note |
| --- | --- | --- |
| lib1305 (resolute) | PASS | controller, `strip-bsymbolic-functions`, 0 model turns; unmodified source fails |
| liboqs (jammy) | PASS | model invoked `wno-error` (rc.8 behaviour) |
| qmidiarp (resolute) | PASS | model removed `-msse -msse2 -mfpmath=sse` from `configure.ac`, the lines the new block listed; not in the 111 |
| xmbmon (noble, questing) | FAIL | inline x86 port I/O |
| libmbim (jammy) | FAIL | gtk-doc conditionals missing after the edit, as in rc.5 |
| obs-time-source (noble) | FAIL | meson setup |
| rapmap (resolute) | FAIL | C++ edit does not parse |
| libelas (resolute) | FAIL | `emmintrin.h` on arm64, as in rc.5 |

amd64-target cases need emulation here and were not built, with one
exception. eegdev (`strip-buildflag`, applied by the controller) was built
under emulation with the log's dpkg-dev 1.22.5ubuntu4 from
snapshot.ubuntu.com: unmodified source FAILS ("C compiler cannot create
executables"), repaired source PASSES, no second failure. Today's dpkg-dev
emits `-fcf-protection=none` and no longer reproduces the failure.
rocm-device-libs (same recipe) was not built.

The validator builds differently from the original Launchpad build in two
ways that matter when reading these logs: it runs `debian/rules binary` (the
full build) on both architectures, where Launchpad's arm64 builds were
`dpkg-buildpackage -B` (`binary-arch`) and its amd64 builds `-b`; and it
passes `-j2` where the originals used `-j4` or `-j8`. In the 26 non-passing
arm64-target cases the failing step after our edit is the same step as in
the original log, so the wider build is not what lost them.

Cost of the rc.9 run on 29 cases: 6.3 M input tokens, 55 minutes of agent
time; the seven controller-applied cases used no tokens.

Regression on the local cases with the real validator (frozen dependencies,
no build feedback to the agent, one run each, `~/runs-060rc9` on the EC2 host):

| case | target | result | model turns |
| --- | --- | --- | --- |
| libmceliece | arm64 | repaired | 0 (controller, `strip-bsymbolic-functions`) |
| ectrans | arm64 | repaired | 0 (controller, `cmake-redirect-imported-dirs`) |
| html-xml-utils | arm64 | repaired | 0 (controller, `serial-helper-build`) |
| libunwind | arm64 | repaired | 0 (controller, `arch-flags`) |
| r-cran-digest | arm64 | repaired | 3 |
| rust-subversion | arm64 | repaired | 21 |
| python-intbitset | amd64 | repaired | 0 (controller, `replace-removed-command`) |

The other four amd64-target local cases (libbpp-core, libbpp-qt, libevtx,
pybigwig) were started and stopped: emulated builds on a host that was also
running six investigations took over an hour each.

## Round 17: root causes of the 58 losses, and what came of them (13 November, 07:30-13:30 UTC)

After round 16 the question was what is actually wrong in the cases that
still fail. Six investigators took the 58 non-passing cases of the rc.5 run
in groups, reproduced each failure with real builds where the host could,
and built a fix. Reports and a summary table: `docs/rootcause-2026-11-13/`.
This section records what changed in the agent because of them and what the
measurements say.

Mechanisms added (each one's source file has the evidence in its docstring):

| mechanism | cases it is for | confirmed by a real build |
| --- | --- | --- |
| recipe `promote-indep-build-dep`, controller | libmbim, python-msgspec (arm64) | libmbim arch-only: unmodified fails, repaired passes; python-msgspec: the same edit passed the validator in rc.5 |
| recipe `disable-lto`, controller (three signatures) | globalplatform, ocaml (2), kissat | globalplatform: unmodified fails, repaired 5/5 tests (emulated); ocaml arm64: 3546 tests, 0 failed; kissat: builds and runs (emulated) |
| recipe `arm64-x86-semantics`, added after the model | arm64 test failures with no compile error | xsimd 343/343, tasmanian 26/26, mlmmj 195/196, unmodified source plus the block |
| recipe `python-version-slice`, added after the model | python-reportlab | passes (`-b`, jammy) |
| verdict: x86 assembly or header still compiled | xmbmon | unit tests; live: see below |
| verdict: edited `.c` under a recursive Perl build | libcrypt-xxhash-perl | unit tests; the model has not followed it yet |
| playbook: x86-only file as a whole; quilt `.pc/` copies | xmbmon, rapmap, libcrypt-xxhash-perl | live: see below |
| block for "is not supported for this target" | golf | live: the model edits the Makefile line the block names; native build passes |

Three things did not work as first built and were changed the same day:

1. **A recipe shown to the model before its first turn changes what the model
   does.** With a specific recipe pre-applied, the model deleted it (lib1305,
   round 16), so those runs now end at once. With the arm64 flags pre-applied
   and a note saying so, the model tended to stop: fine where the flags are the
   repair (libqalculate), a loss where the model's own edit was needed
   (lua-struct on resolute). The flags are now added after the model has
   finished and it never sees them.
2. **The flags do not protect a case from the model's own edits.** libqalculate
   needs only `-fsigned-char` for C++, which the baseline supplies, and which
   plain rc.8 finds by itself. In two runs with a third new playbook entry
   ("an error message printed by the package's own code: find its text in the
   source") the model edited the test files instead and the build failed. That
   entry is removed.
3. **The baseline refused on xsimd** because debian/rules assigns the same
   variable inside an armhf-only conditional. It is now placed just before the
   first rule and only an unconditional later assignment stops it.

Measurement. The final code was run once over all 111 small cases
(`~/bb-public-runs/rc9final2`, live gpt-oss-120b, no builds): every case got a
change, 20.2 M input tokens, 19 cases were closed by the controller with no
model turn and 14 more got a baseline after the model. The Development
Validation bundle built from that run is `devval-v060rc9.zip` (111 cases).
Plain rc.8 was run on the 53 arm64-target cases for comparison
(`~/bb-public-runs/rc8arm`; ocaml left out). Both sets of repairs were then
built natively on arm64, one `dpkg-buildpackage -b` per case in a container of
the case's release, today's archive. Numbers as of 14:50 UTC, with builds
still running (results land in `~/scratch-verify/vy-*.out` and `v8a-*.out` on
the host; `python3 /tmp/compare.py v8a vy` prints the table):

| | built | pass |
| --- | --- | --- |
| rc.9, final run | 29 | 22 |
| rc.8 | 37 | 22 |
| cases built for both | 24 | rc.9 17, rc.8 16 |

Where the two differ so far: python-msgspec (arm64) and
r-cran-randomfieldsutils pass only with rc.9, libcvd only with rc.8. libcvd
is a heap overrun whose symptom varies from run to run; it passed in two of
three further rc.9 runs.

What the native builds can and cannot show:

- They are not the validator. Three cases that passed in rc.5 fail here under
  rc.8 and rc.9 alike (libgovirt, pam-tmpdir, node-node-sass), and two fail
  only because this harness cannot install their dependencies (ectrans, which
  the real validator passed in round 16, and at times qtcontacts-sqlite).
- A pass only counts if the unmodified source fails in the same harness. It
  does for lib1305, liboqs, tasmanian, xsimd, both xmbmon copies and lua-struct
  (oracular). It does not for libmbim (the harness installs the indep
  packages) and efibootmgr (the archive has moved on); those two were
  confirmed separately under the log's conditions.
- The model's own repairs vary from run to run. Twelve cases whose result
  changed between runs were run three more times with the final code: 9, 6
  and 7 of 12 passed. libgovirt, pam-tmpdir and node-node-sass failed in all
  three, libqalculate passed in all three, xmbmon (noble) in all three,
  xmbmon (questing) in two.

Cases that did not pass in rc.5 and pass in native builds of an rc.9 run:
lib1305, liboqs, tasmanian, xsimd, ocaml, xmbmon (noble and questing, not in
every run), rapmap (one run of three), r-cran-randomfieldsutils (one run),
lua-struct (oracular, also under rc.8). For amd64 targets only the controller's
edits could be checked, under emulation: eegdev, globalplatform and kissat
build with them, and python-reportlab's full build passes.

Not measured: anything on the real validator beyond the local cases of round
16. The 4 hour limit and the emulation failures on amd64 are untouched by all
of this.


## x86-target cases on the arm64 instance (fixed 8 November)

Every amd64/i386-target validator build on the arm64 instance ended in
"infrastructure error" with `Segmentation fault` lines in the chroot. The
cause is not the chroot: Ubuntu 24.04's `qemu-user-static` 8.2.2 crashes
inside itself ("QEMU internal SIGSEGV {code=MAPERR, addr=0x20}") on x86
`grep` from every Ubuntu release, and obs-build runs `grep` in the chroot.
QEMU 10.2 (the `tonistiigi/binfmt` image) runs the same binary correctly.
`harness/fix-qemu-x86.sh` (run once with sudo) replaces the two x86 binfmt
handlers with the QEMU 10.2 ones and installs a systemd unit so the change
survives the instance's stop/start cycles, which is what undid the earlier
manual fix after each restart; `--undo` restores the Ubuntu handlers. After
it, the libevtx chroot reached the package's own `configure` with no
segfault. Emulated builds are slow (minutes per configure step), so expect
20–60 minutes per x86 case.

## Reproducing

```bash
# EC2, repository root, branch agent-improvements
L() { echo ~/organizer-logs/$(basename "$1").log; }
read -s BB_MODEL_API_KEY            # on its own line; Bedrock short-term keys last 12 h
export BB_MODEL_API_KEY BB_AGENT_USE_BUILD_FEEDBACK=0
c=launchpad-plucky-amd64-arm64-libunwind-f18b55e03a91
python3 -m harness.run_case --bundle ~/bb-cases/$c --agent full-agent --live \
    --run-id <new-id> --log "$(L ~/bb-cases/$c)"
```

Run records are under `<bundle>/harness-runs/<run-id>/` on the EC2 host
(`replies.jsonl`, `record.json`, `result/build.log`, `repair.diff`).
