# Part C — Repair loop and candidate retention

## Status

Part C's core loop is implemented and tested offline on `part-c`.
A local integration run on 14 October 2026 also repaired rcran using the
real Bedrock model and the supplied ARM64 validator environment.

This is a checkpoint, not completion of every Part C requirement.
The successful run had one edited candidate, so revision after a failed
edited candidate remains to be demonstrated. A live history-disabled run
also remains outstanding.

The full-agent offline suite last reported 112 tests before the later
retained-candidate fields were added. After those additions, all 27 loop
tests passed, followed by a focused retained-candidate assertion check.
The newer local harness adapters were syntax-checked and exercised in the
local build and live-model runs described below.

## Files

> **Update, 14 October:** with the Part C owner's agreement, the temporary
> `model_turns.py`, `harness/part_c_model.py` and `harness/part_c_build.py` were
> retired. Replies are parsed by Part E's `reply.py` (all nine `model_turns`
> tests passed against it before removal), prompts come from Part E's
> `prompts.py`, and local builds use Part D's `harness/build_feedback.py`.
> References to those files below describe the 14 October checkpoint.
>
> **Integration, 14 October:** items 1–6 of "Integration remaining" are
> done. `src/main.py` calls `run_loop` through `src/model_adapter.py` (Part E's
> prompts and parser), passes the case's patch policy, and serializes attempts,
> turns, usage and the retained candidate. `harness/run_case.py --agent
> full-agent` runs the loop with Part D's build feedback. `LoopRequest` gained
> `latest_attempt`, so the latest edit's result reaches the model with history
> off. Item 8 (a live revise on rcran) is next.

Under `buildbench-starter-kit-0.1.0-rc.5/agents/full-agent/src/`:

- `loop.py`: orchestrates model turns, investigation tools, edits, build
  feedback, budgets, and candidate selection.
- `candidates.py`: captures affected files before an edit batch and restores
  their bytes and permissions when needed.
- `model_turns.py`: converts model text into the shared `ModelTurn`
  contract. This overlaps with Part E's reply parser and must be reconciled
  during integration.

Tests:

- `tests/full-agent/test_loop.py`
- `tests/full-agent/test_candidates.py`
- `tests/full-agent/test_model_turns.py`

## Loop interface

`run_loop` receives a `CaseContext` and a `next_turn` callback. The callback
accepts a `LoopRequest` and returns a `ModelTurn` plus reported input/output
token usage.

Optional inputs include `AgentConfig`, `Budget`, a Part D build-feedback
adapter, trusted case patch policy, tool/turn limits, and an injectable clock.

A model turn can:

- Request Part B tools.
- Propose edits through the existing edit applier.
- Stop.
- Return a malformed response, which receives corrective feedback and
  consumes a turn.

The loop returns a `LoopResult`. The caller must serialize the result and
write the required agent output files; the loop does not perform that
integration itself.

## Evidence and history

Every model request includes the case context, available tool results,
and feedback.

With `use_history=True`, requests also include copies of previous attempts.
With history disabled, the returned run record still retains every
recorded attempt.

The latest edit/build feedback remains available across tool turns and
malformed replies. Tool results are cleared after an edit attempt because
they may contain outdated source text.

The initial `CaseContext.log_tail` is not replaced automatically.
Integration must distinguish the initial log from newer build feedback.

## Candidate selection

Edits are applied to the actual worktree. Build results rank candidates by:

1. Build success.
2. Furthest reported build stage.
3. Fewest diagnostics.

This is a heuristic for choosing which files to retain, not proof that
a repair is correct.

When a changed attempt ranks below the retained candidate, the pre-edit
snapshot is restored. Ties retain the newer candidate. Once a candidate
has a build result, a later changed attempt without a build result is
restored to the preceding retained state.

Without build results, the loop cannot rank repair quality; edits remain
unvalidated.

Snapshots are used instead of reversing replacement strings, because
partial acceptance and repeated text can make reverse replacements
ambiguous. Restoration is performed per file; it is not an atomic
transaction across the entire batch.

## Stop conditions and failures

The loop stops for model-requested termination, reported build success,
exhausted budgets, repeated leading diagnostics, or two successive empty
edit proposals.

The repeated-diagnostic comparison uses the first diagnostic's message,
log path, and line number from adjacent changed attempts with failed builds.

Handled model or build-adapter errors return a termination reason and
preserve recorded attempts. Restoration errors are reported explicitly.

## Batch repair before a correction (3 November)

Before an edit batch is sent back to the model for correction, the loop
repairs it mechanically where the intended result is unambiguous
(`src/edit_repair.py`, `prune_batch`), repeating until the first remaining
problem is one only the model can resolve:

| Problem | What happens | Why |
| --- | --- | --- |
| `DEB_*_MAINT_*` or `DEB_BUILD_MAINT_OPTIONS` added to debian/rules at make level without `export` | `export` is added | dpkg-buildflags reads the environment; an unexported line reaches no compile (libunwind, round 4). Tab-indented recipe lines are left alone: there `VAR=value command` is shell |
| `no_change` (old_text equals new_text) | dropped | it changes nothing (rust-subversion, round 4: this one item cost the other 25) |
| an exact duplicate of an earlier edit | dropped | the first copy already makes the change |
| a change an earlier edit in the batch already made | dropped | a broad replace_all followed by a narrower one |
| old_text absent, but one run of lines matches it apart from indentation; no indented line written at column 0; each model indent stands for one real indent; new_text uses only those indents | old_text replaced by the file's text; new_text re-indented the same way | the model wrote two tabs for three ten times (libunwind). Anything less clear-cut is not touched |
| old_text absent, but one run of lines matches it apart from leading or trailing whitespace (any other shape) | the correction shows those exact lines with tabs and spaces made visible | the model copies the real whitespace instead of guessing again |
| an assignment inserted between a line ending in `\` and its continuation (Makefile, Makefile.am, debian/rules, *.mk), or appended as a continued item | rejected: `breaks_backslash_continuation`, with where to put the line | the 19 October libunwind diff split `noinst_PROGRAMS_cdep` |
| `old_text_not_found`, old_text occurs nowhere in the file on disk and new_text (12+ characters) already does (0.3.2) | dropped: the change has already been made, with a note saying so | after the batch that replaced every `i8` with `c_char`, the model proposed the same `replace_all` 35 more times and searched for the text between corrections (rust-subversion, round 6) |
| an edit that only removes `export` from such a line (0.3.2) | dropped, with the reason | the model decided `export VAR += value` was invalid make and spent 37 turns on it while the controller re-exported each attempt into a no-op (libunwind, round 5) |
| `old_text_not_found`, the text is not in the file the edit names, and it occurs exactly once in exactly one other file of the same type under `input/` (0.3.2) | the edit is moved to that file (`retarget_path`), with a note | 16 edits named `src/client.rs` for functions in `src/delta.rs`; each cost one to three correction turns (rust-subversion, round 5). The text must be at least 24 characters, and the target must pass the path policy |

What remains goes to the focused correction as before, with two new ways out:
a correction that repeats an `old_text` already rejected for that item, and
the item's correction budget running out, both **drop that item alone and
continue validating the rest of the batch**, which is then applied
(`termination_reason` `edit_correction_budget` no longer exists). The budget
belongs to the item being corrected: a batch has 12 corrections in all, and
a later item always gets at least 3 of them, so one bad item cannot keep the rest
of the batch from being applied, and a second bad item is not dropped unseen.
The same applies to an edit whose path lacks the `input/` prefix. A batch
with nothing left to apply is recorded as an attempt with no edits, so the
attempt budget still bounds the run, and the model is told to propose another
batch or stop. Every repair and drop is reported to the model with the
attempt's result (`LoopRequest.notes`).

A GNU make conditional (`ifeq`, `ifdef`) added to a `Makefile.am` is applied
with a warning that automake owns `if`/`else`/`endif` there and the regenerated
Makefile will fail (`static_warnings`).

Separately from the batch repair, `src/patterns.py` (0.3.2) looks at the
diagnostics and the log once per case and, when they match a known
cross-architecture failure, renders a block right after the errors in the
opening message: the evidence, the files involved (gathered from the
worktree), and the usual repair. It is the playbook's advice moved to where
the model reads, with the facts filled in; it edits nothing. See
`docs/no-build-live-findings.md`, round 5, for why.

## Budget limitations

Time checks prevent new work from starting after the working deadline,
reserving time for shutdown. They do not interrupt an already-running
model, tool, or build call. Adapter timeouts still need to be coordinated
during integration.

Token accounting uses reported usage:

- `usage` becomes unknown for any total with missing contributing counts.
- `known_usage` retains the sum of counts actually reported.
- The known total is used to prevent another model call once the token
  budget is reached.

This is not a guaranteed hard token cap: a single call may exceed the
remaining allowance, and missing usage may understate consumption.

All local limits are research settings, not official competition limits.

## Integration remaining

1. Connect the full-agent entrypoint to `run_loop`.
2. Adapt Part E's prompts and model client to `LoopRequest`, preserving
   current feedback even when history is disabled.
3. Reconcile `model_turns.py` with Part E's parser and retain relevant
   parser tests.
4. Connect Part A's case/evidence output to the loop's context and pass
   trusted case patch policy.
5. Connect Part D's real build adapter and coordinate request timeouts.
6. Serialize attempts, usage, termination reason, and retained-candidate
   information for the run record.
7. Check history size management and ensure restored candidates are
   clearly identified in prompts and records.
8. Demonstrate a failed repair followed by a revised attempt using real
   model and build feedback on rcran.

Part E's comparison experiments and failure taxonomy remain outside this
Part C implementation.

## Run the offline tests

From the repository root:

```bash
python3 -m unittest discover -s tests/full-agent -p 'test_*.py' -v
```


## Local integration checkpoint — 14 October 2026

### Local harness files

- `harness/part_c_build.py`: copies the worktree, repacks the copy using a
  separate packaging container, assembles a validator case, and returns
  real build feedback.
- `harness/part_c_model.py`: connects the loop to the existing Bedrock
  request function and Part C parser without importing Part E.

These adapters are local research infrastructure, outside the agent ZIP.
They have not been wired into the submitted agent's `main.py`.
Part E was not merged.

### Environment and build verification

The supplied rcran bundle was extracted outside Git and its internal
checksums verified. Docker Desktop provided a Linux ARM64 engine.
All 361 frozen dependencies passed the validator's preflight checks.

The original package reached real compilation and failed because
`crc32c/crc32c_prefetch.h` included `xmmintrin.h` on ARM64.

GNU coreutils supplied the `realpath -m` behavior required by the wrapper.
A separate `buildbench-source-tools:part-c` image supplied `dpkg-source`
and `patch`. The organizer's validator image and dependencies were not
modified.

The local build adapter reproduced the baseline failure. A fingerprint
comparison verified that it preserved the original worktree's file
contents, permissions, and symlink targets.

Timestamp prefixes in validator logs initially prevented source-path
resolution. The adapter now preserves the raw log and creates a separate
normalized log for Part A. Diagnostic byte offsets refer to that
normalized log. The missing-header diagnostic resolved to
`input/src/crc32c/crc32c_prefetch.h`, line 18.

### Retained-candidate information

`LoopRequest` and `LoopResult` expose `current_build` and `kept_attempts`.
These distinguish the retained candidate from discarded attempts.
The result retains this information even when model-visible history is off.

A retained attempt may contain partially accepted edits; its `applied`
count records how many edit operations were accepted.

### Real-model result

Run: `live-part-c-694ehfba`

- Model reported by the access check: `openai.gpt-oss-120b-1:0`.
- Model service: Amazon Bedrock, using the participant's account.
- Configuration: history enabled.
- Model turns: malformed response, tool request, edit.
- Repair attempts: 1.
- Build requests: 1.
- Retained attempt: 1.
- Usage: 24,804 input tokens and 1,846 output tokens.
- Elapsed time: approximately 44 seconds.

The edit guarded the x86-specific header include with architecture
conditions. The validator reported `status: succeeded`,
`artifact_validation_passed: true`, and `build_exit_code: 0`, and produced
the expected ARM64 binary package.

`patch_applied: false` is expected for this workflow: the edit was included
in the rebuilt source package rather than supplied through the validator's
separate patch option.

Evidence is committed under
[`results/part-c-rcran-694ehfba/`](results/part-c-rcran-694ehfba/).
Large runtime images, frozen dependencies, source archives, and binary
artifacts remain outside Git.

### Interpretation and limitations

This establishes a successful local Part C integration run, not a
competition result, Development Validation verdict, or measured repair rate.

The temporary model adapter summarizes older attempts and includes bounded
edit excerpts for recent attempts. It does not implement Part E's
evidence-manager switch. The recorded `use_evidence_manager: true` therefore
does not establish that the intended full evidence-manager integration ran.

The successful run was launched through a terminal script that has not yet
been saved as a reusable runner. Its JSON evidence is preserved, but full
reproducibility still requires saving the runner and environment details.

### Remaining work

1. Save a reusable local runner and add focused adapter tests.
2. Demonstrate a real failed edited candidate followed by revision using
   its new build errors. Clearly label any deliberately injected failure.
3. Verify the live history-disabled path and ensure only appropriate
   current-candidate evidence reaches the model.
4. Finish deadline handling and Docker cleanup after interrupted builds.
5. Connect the submitted entrypoint and coordinate the final Part D/E
   interfaces, without duplicating their responsibilities.
6. Update documentation and run the relevant regression checks before
   declaring Part C complete.

## Explicit replacement and proposed-batch review

Merged from `reviewed-repairs.md` (19 October) on 2 November, unchanged apart
from headings. Edits that an earlier edit in their batch already made are now
dropped before correction (`edit_repair.prune_batch`); see
`no-build-live-findings.md`.

This full-agent extension contains no package-specific solution. The frozen one-shot agent is unchanged.

### Activation

Set `BB_REPAIR_STRATEGY=shared-cause` and `BB_REVIEW_EDITS=1` in the actual experiment process. Review defaults to off to preserve existing comparison modes. Record both settings in experiment metadata. `run_agent` validates the review flag; callers of `run_loop` can pass `review_edits=True` and `initial_diagnostics` explicitly.

Existing time, token, attempt, build, tool-call and turn budgets still apply. This patch neither increases them nor implements resumable sessions. Configure generous experimental budgets separately and record them; do not mistake more turns for more allowed builds.

### Replacement protocol

An ordinary edit still requires one unique literal match. An edit may explicitly set the JSON boolean `replace_all` to `true` to replace every non-overlapping occurrence in its named file. This is literal Python-style left-to-right replacement, not regex, syntax-aware rewriting, or permission to edit other files. Counts are computed by the controller, not asserted by the model. Subsequent edits operate on the result of earlier edits.

Example:

```json
{"action":"edit","hypothesis":"All uses of this deprecated symbol in this file require the replacement","evidence":["compiler message and inspected source"],"edits":[{"path":"input/example.c","old_text":"old_symbol","new_text":"new_symbol","replace_all":true}]}
```

The model must justify every affected use. Substring matching can affect longer identifiers or comments; the diff review is important. Existing path, symlink, UTF-8, file-size and build-policy protections remain in force. There is one shared replacement implementation for simulation and application.

### Review cycle

1. Validate paths and simulate the entire batch against a snapshot. Invalid edits continue through the existing focused correction flow.
2. Before any write or build, show the proposed diff, measured occurrence counts, diagnostics from the retained build (or initial/historical evidence), and remaining short literal matches across permitted source files.
3. The model may request read-only tools, submit a complete revised batch, stop, or return `{"action":"approve","reason":"why the repair is worth building"}`. An approval cannot contain edits. Approval is accepted only while a batch is pending.
4. Revalidate the exact pending batch and affected source snapshot. If affected source changed since review, request review again. Apply only the approved batch, then build through the existing loop. A partial application is restored and no build is requested in review mode.
5. On failure, continue using the existing candidate-selection and rollback behavior. Subsequent review uses retained-candidate errors; a discarded build's errors are not described as current.

Read-only `current_diff` still describes already applied changes, not the simulated pending batch. Review consumes ordinary model turns and tokens. No separate fixed review retry limit is added; all loop budgets still apply. A plausible partial repair may be approved. Review does not require agreement with a known answer and does not prove correctness.

### Coverage and logging

Coverage searches are literal hints, not semantic proof. They may surface an untouched file, but no remaining match is changed automatically. Only short single-line old-text patterns (4–200 characters) are searched. Symlinks, Git/quilt backups and disallowed files are skipped. Coverage has explicit time (3 seconds), file (1000), byte (16 MiB) and result safeguards; incomplete scans and skipped entries are reported. Proposed diff display is capped at 80,000 characters with an explicit truncation notice; coverage display at 20,000 characters. Existing diagnostic rendering limits remain. Use tools to investigate omissions rather than interpreting absence as proof.

Run records include `review_events` with simulated feedback, per-edit counts, approval reasons and proposal signatures. Adapter turn records include `proposal_review`. Explicit replacement counts are also logged during application. Raw model requests can still be captured with `BB_MODEL_TRACE`.

### Verification

Offline tests cover ordinary ambiguous-edit rejection, explicit all-occurrence replacement, sequential and overlapping-match semantics, unchanged-file and policy checks, safe coverage, approval gating, full-batch revision, stale-source re-review, budget exit without writes, partial-application restoration, review-time tools, adapter approval parsing, retained-candidate rollback, and a failed-build/revise/successful-build sequence. Mocked build success is an integration test, not a benchmark repair result.

Run the full-agent and harness suites in a complete checkout, including the frozen one-shot files.
