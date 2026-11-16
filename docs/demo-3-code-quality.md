# Demo 3 code-quality refactor

## Scope and provenance

This is a readability refactor of full-agent 0.3.2 from
`feaa5714d9a4c9738dee46c9190e4d81d05c43af`, carried separately in
`agents/full-agent-v032`. Its version is `0.3.2+demo3.refactor` so a run
cannot be mistaken for the byte-identical historical implementation.
The current `agents/full-agent`, baseline agent, and their tests are unchanged.

The historical 4-of-11 result (four x86 cases undetermined under QEMU)
is not a new evaluation of this refactor.

## Repair stages

`run_loop` creates one `_RepairSession`, which owns per-run state. Its `run`
method advances the existing policy in this order:

1. Check remaining budget and construct a model request.
2. Account for model usage and handle malformed replies or stop actions.
3. Execute investigation tools, or resolve the currently pending review.
4. Merge a correction with its original batch.
5. Check paths, capture source, prune/repair unambiguous edits, and validate anchors.
6. Simulate the batch and request review when enabled.
7. Capture and apply the batch; restore partial reviewed applications.
8. Record the attempt, optionally request build feedback, select the retained
   candidate, and report the result.

The `_NEXT_TURN` marker means the controller should ask the model again;
`LoopResult` means it should stop. Helper calls preserve the original exception
boundaries and order of deadline checks. Temporary edit/build data stays local
and is passed explicitly; correction/review state survives between turns.

Measured as AST function spans (including comments and blank lines):

| Function | Original | Refactored |
| --- | ---: | ---: |
| `run_loop` | 634 lines, 68 `if` nodes | 23 lines, 0 `if` nodes |
| `_run_tool` | 338 lines, 36 `if` nodes | 6 lines, 1 `if` node |

The session's orchestration method is 16 lines; its largest stage is 66 lines.
These are structural measurements, not evidence of faster builds or better repairs.

## Tool dispatch

`_WORKTREE_HANDLERS` maps names to separate read-file, directory-listing,
file-finding, search, patch-series and current-diff handlers. `run_tool` retains
shared timeout/error conversion. The two log tools retain their common bounded
log reader, since they share the same streaming and truncation behavior.

Response text, path validation, symlink restrictions, search limits and timeout
policy are preserved. The obsolete module description claiming every tool was
an unavailable placeholder has been corrected.

## Model-client duplication: intentionally not changed in this patch

The TA's duplication concern remains valid. However, baseline and historical
agents are independently packaged: each submission contains its own `src/`.
Importing a sibling agent's client would break standalone packaging and couple
the frozen baseline to this refactor. The copied clients also represent different
version histories, so byte duplication alone is not a sufficient migration plan.

This patch leaves their model clients untouched. A separate change can establish
a shared development source and reproducible vendoring into each standalone
package, with explicit compatibility and drift checks. That work is not claimed
as completed here.

## Verification

Run each suite in a separate process because agents use the same `src` name:

    python3 -m unittest discover -s tests/full-agent-v032
    python3 -m unittest discover -s harness/tests -t .

- Original historical suite: 490 tests passed before refactoring.
- Refactored historical suite: 492 tests passed on Python 3.11 and Python 3.13.
- Harness: 80 tests passed on Python 3.11 and Python 3.13.
- The two added characterization tests cover 17 scripted controller scenarios
  and 11 tool requests. Expected data was recorded using the original loop and
  tools, before running it against the refactored code.
- Comparisons include complete model requests, result records, final source and
  tool responses. Temporary worktree paths and filesystem-dependent review hashes
  are normalized; repeated/equal review signatures retain their relationships.
- Three historical harness-integration tests were updated to explicitly load the
  separate historical agent directory. Assertions were not weakened.
- No model requests, real package builds or hosted evaluations were performed.
- Starter-kit check/package commands were attempted but blocked by Docker socket
  access in the local environment. No validated submission ZIP is claimed.

CI now includes this agent directory in its sparse checkout and runs its suite
separately on the existing Python 3.11/3.12 matrix, including demo-3 pushes.
Python 3.12 CI results remain to be observed after the user pushes.

## Technical-depth feedback

This refactor addresses readability, not novelty or measured repair performance.
The Demo 3 explanation should distinguish the bounded investigation/edit loop,
recognized architecture patterns, mechanical batch correction and final build
validation. In the configured no-build-feedback experiment the model does not
rebuild repeatedly until success. Any claim of improvement needs a separately
recorded evaluation; this patch does not add such a claim.
