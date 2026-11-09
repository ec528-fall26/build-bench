# Implementation report: full-agent 0.6.0-rc.4 (9 November)

Four packages were built in parallel and integrated in the `OZ-arch-model` checkout. This page says what shipped,
what is behind a flag, and what was left undone. Plan: `master-plan.md` (sections 2 and 7).

## What shipped

1. Recipes (M2), `src/recipes/`. Nine verified repairs in one registry (symbols-tag, strip-bsymbolic-functions,
   o2-on-test-segfault, optimize-guard-test-memory, serial-helper-build, cmake-redirect-imported-dirs,
   replace-removed-command, arch-flags, static-archive-shim). The model invokes one with an edit whose `old_text` is
   `@@recipe:<id>@@` (alias `@@tag-missing-symbols@@`); `edit_repair.expand_macros` re-recognises the log signature,
   checks tree preconditions and returns the exact edit, or "Recipe <id> refused: <why>". `patterns.recognise` ranks
   blocks by distance from the first fatal error; each block leads with its invocation line. The loop passes the
   case's `target_arch` to `expand_macros`.
2. Edit gates (M4). `syntax_guard.symbols_lint` (whole-file dpkg grammar; hard findings cannot be waived by an
   unchanged resend); `edit_applier.normalise_symbols`; `edit_preflight` size budget (symbols file > 2x the distinct
   reported-missing symbols; any single edit > 200 changed lines unless replace_all with >= 100 sites); `verify.py`
   "unknown" for vanished or unchanged lines, over-tagging verdict; `rules_guard` additions (flag overrides on
   `dh_auto_*`, `DEB_BUILD_OPTIONS`-only tokens, `|| true` rules, `-dh_auto_test`). The loop passes
   `missing_symbol_count` (from `patterns.missing_symbols`) to the budget through `prune_batch` and `focused_feedback`.
3. Readable dependencies (M3), `dep_audit.py`, `deb_archive.py`, `tools.py`, `main.py`. DEPENDENCY FACTS block
   (dangling `.so`, empty runtime packages, missing CMake/pkg-config paths); streaming deb decompression with a
   256 MB cap; zstd through the pinned wheel `zstandard==0.25.0` (lazy import, then the `zstd` binary, then a
   sizes-only note); bounded `search` with an honest "scanned N of M" header; `run_tool` never raises; an agent
   exception with a changed worktree ends as `completed` with an `error` field instead of `no_fix`.
4. Loop control (M5), `loop.py`, `model_adapter.py`, `reply.py`, `prompts.py`. All-fixed stop gate; identical-request
   stub (uncounted); investigation notes at 8 and 12 tool turns; JSON-only reply instruction, reasoning stripped,
   malformed reply answered in-conversation; VERDICT line first in attempt feedback; per-call `finish_reason` and
   completion tokens in turn records; no-build attempt cap of 8 in the harness.

Integration fixes made in this step: loop wiring of `target_arch` and `missing_symbol_count` (handoffs from recipes and
gates); `prune_batch` accepts `missing_symbol_count`; four test expectations updated to the new behaviour
(`test_wiring` x2: the explanation is in the last message of the continued conversation; `test_arch_slice` x2: a
vanished include line is `unknown`, the symbols verdict wording); new `tests/full-agent/test_integration.py`.

## Flags

No new switch was added. Everything below is on by default and the existing switches still govern it:

- `BB_AGENT_USE_SYNTAX_GUARD` (default on): symbols lint (hard findings) and the syntax checks.
- `BB_AGENT_USE_RULES_GUARD` (default on): the new rules_guard refusals.
- `BB_AGENT_USE_BUILD_FEEDBACK=0` with `BB_AGENT_USE_NO_BUILD_CONTROL` on: the all-fixed gate and the 8-attempt cap
  (the gate is never armed when builds are on).
- Recipes, `dep_audit`, the size budget, the identical-request guard and the reply protocol are unconditional.
- `zstandard` is optional at runtime: nothing requires it, zstd debs are then listed by size only (or read through a
  `zstd` binary if the image has one).

## Verification

Full suite 885 tests on this Mac: 2 failures, both the GCC-dependent `test_syntax_guard` tests. In
`python:3.11.9-slim-bookworm` (linux/arm64): 885 tests OK without zstandard, with zstandard==0.25.0 and with
`--network none`. `deb_contents` reads real zstd debs (automake, autopoint, libtool) with the wheel and degrades to a
sizes-only note without it. All nine verified replays pass every gate with zero blocks on the pristine trees;
recipes reproduce seven of nine (the other two are different fixes for the same case, see the table in the run report).
Kit checker passes with the one pinned wheel in `requirements.lock`.

## Left undone

- No hosted or live model run of 0.6.0-rc.4; every number above is offline.
- `dep_audit` is wired inside the architecture block, so it is skipped when the target architecture is unknown to
  `arch_model`.
- libbpp-core: the accepted run used `(arch=!amd64)` in an `.amd64` file; the recipe writes `(optional=templinst)` /
  `(optional)`. Not validator-run for that case.
- strip-bsymbolic-functions is unguarded (the validated form); no guarded variant was validator-run.
- Reasoning-content length is not recorded (needs a `model_client` change); `ffi_analysis` untouched.
- The rcran "dep_audit reports nothing" oracle was checked on libunwind and libbpp-core only (no rcran bundle on the
  instance).
- The model prompt has no global description of the `@@recipe:<id>@@` syntax; the recognised blocks carry the exact
  line instead.
- Gold-linker (libmceliece) and `-j1`-style (html-xml-utils no-parallel) fixes accepted by the validator have no recipe.


## Round 15 addendum: making the recipes the obvious path (9 November, 08:00-08:30 UTC)

The live rc.4 run (findings round 14) showed the model never invoked a recipe: it read the `@@recipe:<id>@@` sentinel as a placeholder to replace with file text and hand-wrote the edits, incompletely. Five changes, 901 tests passing (the two GCC-dependent ones excepted), kit checker OK:

1. **Dry-run preview.** A verified recipe is expanded at opening-message time without writing anything, and the block shows the edit and file counts plus a unified-diff preview capped at 40 lines, followed by the invocation paragraph in a form the model cannot misread ("its old_text is a command to the controller, not text in the file, and new_text may be left empty"). `registry.parse_params` treats "", "auto" and "default" as defaults.
2. **Full detail in verdicts.** `verify._symbols` and the other symbols verdicts name every remaining symbol in full with its file line, up to 12.
3. **Controller completion** (`completion.py`, `COMPLETE_MECHANICAL_REPAIRS = True`). After an applied batch that leaves symbols from the log's missing set untagged in the file the model edited, the controller applies the symbols-tag expansion for exactly those lines through the normal gates, notes it in the attempt feedback, and re-runs the prediction.
4. **Stop bounce on an outstanding NOT-fixed verdict**, once per run, quoting the unfixed lines in full and repeating the recipe invocation; composes with the existing empty-stop bounce.
5. **Static-shim verdict** for the non-PIC archive class: after a setup.py edit, NOT fixed unless the library is no longer linked, the archive objects are linked, a hidden shim defines the relocated symbol and `-Wl,-Bsymbolic` is passed; names the missing pieces.

Not yet run live (the Bedrock key expired during round 14); libbpp-core, pybigwig and libevtx are the re-runs that test it.
