# Full agent without build feedback: live findings (2–3 November 2026)

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

libmceliece (round 2) fails on a BFD linker assertion
(`elfnn-aarch64.c:9754`), most likely a toolchain bug; it is not a useful
measure of the agent.

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
     edits are now dropped (`drop_covered`).
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

1. When the correction budget runs out, apply the edits that validate instead
   of none, and drop `no_change` items automatically.
2. Say in the prompts that the log will not change during the run, and that
   the model should stop once its edit addresses the cause.
3. Drop a correction item whose reply repeats an already-rejected old_text.
   Locate the intended line with whitespace-insensitive matching and show its
   exact whitespace.
4. Add edit pre-check warnings for `DEB_*_MAINT_APPEND` without `export`, a
   line inserted into a backslash continuation, and `ifeq` in `Makefile.am`.
5. Before tuning further, run 0.3.0 on the cases not yet tried without
   builds (html-xml-utils, ectrans and the five x86 cases under QEMU), against
   0.2.0, to see whether 0.3.0 is better at all.

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
