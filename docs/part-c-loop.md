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

Under `buildbench-starter-kit-0.1.0-rc.2/agents/full-agent/src/`:

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
