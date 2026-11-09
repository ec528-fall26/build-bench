# An architecture model for cross-architecture repair (full agent 0.4.0)

*Owen Zhang, 6 November 2026. Status: implemented and unit-tested (`tests/full-agent/test_predict.py`, `test_arch_slice.py`); not yet run live.*

## The problem with the loop

The full agent through 0.3.3 is a loop: read the failure log, edit files, and (locally) rebuild. The Demo 2 feedback called it a standard agent loop, and it is. What made the 3 November repairs happen was not the loop but facts put next to the errors: which files hard-code `i8`, which header defines `HAVE_MM_PREFETCH`, which lines `debian/rules` needs. Those facts were produced by four hand-written recognisers (`patterns.py`), one per failure we had seen. That does not generalise, and the model still could not see most of the package: it reads three excerpts and whatever it asks for, so on files it never opened it guesses.

## The idea

A cross-architecture build failure has a specific shape: the package built on one architecture and fails on another, and **what differs between the two is a small, enumerable set of facts**. So:

1. **Make the facts explicit.** `arch_model.py` is a table: for each architecture, its compiler macros (with values where they matter), whether `char` is signed and therefore what Rust's `c_char` is, the GNU triplet, the dpkg name, whether x86 intrinsics headers exist, whether GCC emits outline-atomics helpers, `long double` width, page size, endianness. `differences(source, target)` lists the facts that differ and what each breaks. This block opens the evidence.

2. **Find where the package depends on them.** `arch_slice.py` scans the whole package once for every architecture-dependent construct: `#if` on architecture macros, includes of x86-only headers and calls to x86 intrinsics, inline assembly, Rust `cfg(target_arch)` and `*const i8`/`u8` C-string pointers, make conditionals on `DEB_HOST_ARCH`, exported build flags, `Architecture:` fields, per-architecture symbols files, hard-coded triplets. Each construct is **evaluated for the target**: is this line compiled on arm64? is this flag exported on the path make takes for arm64? The constructs nearest each error are shown under it, with the defining line of any project macro in their guards. An x86 include that is reached on arm64 with no guard is named as such. The model now sees the relevant places in files it never read.

3. **Evaluate, don't guess.** `predict.py` holds two exact interpreters: a C preprocessor conditional evaluator (the target's macros defined, every other architecture's and every non-Linux platform's undefined, the project's own `#define`s resolved from its headers under the target) and a GNU make conditional evaluator (`ifeq`/`ifneq`/`ifdef`/`else ifeq`, `$(filter)`, `$(findstring)`, `+=`, `?=`, `export`, with dpkg-architecture's variables set for the target). Both are tri-state: an expression that uses something not modelled (a `$(shell ...)`, a macro from a system header) is **unknown**, and every consumer says so rather than guessing.

4. **Give the loop its feedback back.** The hosted run applies edits untested. `verify.py` re-examines each initial error in the edited files under the target's facts after every batch: the failing include is or is not still reached (and if it is, which macro enables it and where that macro is defined); the failing line is unchanged and still compiled, or changed, or gone; the added flag is exported on the arm64 path or sits in a conditional that is false there; `i8` C-string pointers remain in these files or none do; the symbols the log reports missing are tagged or still unconditional; a file under `debian/patches/` was edited and `dpkg-source` will refuse it. The verdicts are shown with the attempt result, and when every construct is predicted fixed the model is told it may stop. This is the build-feedback loop rebuilt without a compiler, for the constructs that decide cross-architecture builds.

The four recognisers remain for now as repair templates; the mechanism above is what finds and checks the constructs they used to hard-code.

## What it is not

It is not a compiler and does not claim to be: a changed line that is still compiled gets "may or may not recur", not a verdict. It models GCC on Debian Linux; a package built with clang or a non-Debian layout gets more unknowns. The slice is bounded (4,000 files, 1 MB each) and skips `.pc/`, `.git/` and build directories.

## Verification so far

- Unit tests: the model's facts, the two evaluators on the rcran and libunwind shapes and on nested, continued, commented and `else ifeq` conditionals, the slice on a synthetic package with every construct kind, the verdicts for each failure class (including the CMake imported-target one below), and the loop and opening message carrying both. The full suite passes on the ARM64 instance (the two compiler-dependent syntax-guard tests need GCC and are skipped on macOS).
- Live, 8 November (`docs/no-build-live-findings.md`, round 8): 0.6.0-rc.3 against main's 0.6.0-rc.2 on libunwind, rust-subversion, ectrans and rcran. Same outcomes (three repaired, ectrans not). The verdicts changed how the runs ended: rust-subversion stopped by itself at 36 turns after the prediction said every `i8` pointer was gone (rc.2 ran to the 60-turn budget re-proposing edits already made), libunwind took 2 turns instead of 4. Tokens: 1.13 M input against 1.49 M on rust-subversion, 17.7 k against 34.2 k on libunwind; rcran went the other way (122 k against 29 k: the model investigated the slice for six turns after its one edit, then stopped).
- ectrans is the case the model cannot fix from the errors alone, and the live run showed why: the error names one imported target, `fiat`; the dependency's export file is outside `input/`; and both rc.3 and rc.2 cleared that one target's include directories, after which the hidden validation build failed on `parkind_dp` and `parkind_sp`, the same package's other two targets. The cause (confirmed from the fiat-ecmwf 1.2.0-1 arm64 build log on Launchpad): the package installs its Fortran modules under `/usr/include/aarch64-linux-gnu/fortran/gfortran-mod-15/<target>` but exports `/usr/lib/aarch64-linux-gnu/fortran/aarch64-linux-gnu-gfortran-13/<target>`; Ubuntu later deleted that build as broken on non-amd64. The agent now derives the sibling targets from the package's own link lists, says where the repair must go (after the `find_package` line, not in a subdirectory, not before the import), renders a loop that redirects each missing directory to the one that exists, and predicts after the edit which targets are still uncovered. Live (round 9): ectrans repaired in 4 turns with that loop placed after the `find_package` line; the verdict read fixed for all three targets and the model stopped. Five of the eleven local cases now repair.

## Files

| File | Role |
| --- | --- |
| `src/arch_model.py` | the facts table, `differences`, `render` |
| `src/predict.py` | `cpp_active_lines`, `cpp_reached`, `project_defines`/`define_locations`, `make_active_lines` |
| `src/arch_slice.py` | `scan`, `near`, `render` |
| `src/verify.py` | `predict` (verdicts incl. `_cmake`), `render` |
| `src/patterns.py` | `cmake_imported_target`, `imported_targets`, `cmake_repair_snippet` (next to the 0.3.x recognisers) |
| `src/loop.py` | `_Loop._predict` after an unbuilt batch; `LoopRequest.prediction`; the correction tool cap |
| `src/model_adapter.py` | `_architecture_block` in the opening message; the prediction with each attempt |
