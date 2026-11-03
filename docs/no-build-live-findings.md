# Full agent without build feedback: live findings (2–3 November 2026)

Updated 3 November, evening, with round 5 (0.3.1 against 0.3.0) and the 0.3.2 changes.

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
