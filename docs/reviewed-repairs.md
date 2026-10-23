# Explicit replacement and proposed-batch review

This full-agent extension contains no package-specific solution. The frozen one-shot agent is unchanged.

## Activation

Set `BB_REPAIR_STRATEGY=shared-cause` and `BB_REVIEW_EDITS=1` in the actual experiment process. Review defaults to off to preserve existing comparison modes. Record both settings in experiment metadata. `run_agent` validates the review flag; callers of `run_loop` can pass `review_edits=True` and `initial_diagnostics` explicitly.

Existing time, token, attempt, build, tool-call and turn budgets still apply. This patch neither increases them nor implements resumable sessions. Configure generous experimental budgets separately and record them; do not mistake more turns for more allowed builds.

## Replacement protocol

An ordinary edit still requires one unique literal match. An edit may explicitly set the JSON boolean `replace_all` to `true` to replace every non-overlapping occurrence in its named file. This is literal Python-style left-to-right replacement, not regex, syntax-aware rewriting, or permission to edit other files. Counts are computed by the controller, not asserted by the model. Subsequent edits operate on the result of earlier edits.

Example:

```json
{"action":"edit","hypothesis":"All uses of this deprecated symbol in this file require the replacement","evidence":["compiler message and inspected source"],"edits":[{"path":"input/example.c","old_text":"old_symbol","new_text":"new_symbol","replace_all":true}]}
```

The model must justify every affected use. Substring matching can affect longer identifiers or comments; the diff review is important. Existing path, symlink, UTF-8, file-size and build-policy protections remain in force. There is one shared replacement implementation for simulation and application.

## Review cycle

1. Validate paths and simulate the entire batch against a snapshot. Invalid edits continue through the existing focused correction flow.
2. Before any write or build, show the proposed diff, measured occurrence counts, diagnostics from the retained build (or initial/historical evidence), and remaining short literal matches across permitted source files.
3. The model may request read-only tools, submit a complete revised batch, stop, or return `{"action":"approve","reason":"why the repair is worth building"}`. An approval cannot contain edits. Approval is accepted only while a batch is pending.
4. Revalidate the exact pending batch and affected source snapshot. If affected source changed since review, request review again. Apply only the approved batch, then build through the existing loop. A partial application is restored and no build is requested in review mode.
5. On failure, continue using the existing candidate-selection and rollback behavior. Subsequent review uses retained-candidate errors; a discarded build's errors are not described as current.

Read-only `current_diff` still describes already applied changes, not the simulated pending batch. Review consumes ordinary model turns and tokens. No separate fixed review retry limit is added; all loop budgets still apply. A plausible partial repair may be approved. Review does not require agreement with a known answer and does not prove correctness.

## Coverage and logging

Coverage searches are literal hints, not semantic proof. They may surface an untouched file, but no remaining match is changed automatically. Only short single-line old-text patterns (4–200 characters) are searched. Symlinks, Git/quilt backups and disallowed files are skipped. Coverage has explicit time (3 seconds), file (1000), byte (16 MiB) and result safeguards; incomplete scans and skipped entries are reported. Proposed diff display is capped at 80,000 characters with an explicit truncation notice; coverage display at 20,000 characters. Existing diagnostic rendering limits remain. Use tools to investigate omissions rather than interpreting absence as proof.

Run records include `review_events` with simulated feedback, per-edit counts, approval reasons and proposal signatures. Adapter turn records include `proposal_review`. Explicit replacement counts are also logged during application. Raw model requests can still be captured with `BB_MODEL_TRACE`.

## Verification

Offline tests cover ordinary ambiguous-edit rejection, explicit all-occurrence replacement, sequential and overlapping-match semantics, unchanged-file and policy checks, safe coverage, approval gating, full-batch revision, stale-source re-review, budget exit without writes, partial-application restoration, review-time tools, adapter approval parsing, retained-candidate rollback, and a failed-build/revise/successful-build sequence. Mocked build success is an integration test, not a benchmark repair result.

Run the full-agent and harness suites in a complete checkout, including the frozen one-shot files.
