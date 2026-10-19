# Demo 2 development handoff — October 19, 2026

## Current status

No verified repair has been achieved in the Rust experiments documented here.
The full-agent test suite passed 237 tests before these budget experiments.
Temporary EC2 settings were restored to 100,000 tokens and 20 turns afterward.
The EC2 working tree was clean and no Docker containers were running at the
last recorded check. Instance shutdown must be confirmed separately in AWS.

Start with [the experiment results](./results.md).
Selected machine-readable evidence is in [evidence.json](./evidence.json).

## Scope and earlier results

The frozen one-shot baseline must remain separate from full-agent development.
The earlier full-agent evaluation reported 0 repairs across 12 runs:
four public development cases, three repeats each. This earlier result comes
from the prior session report; its records are not in this four-run JSON file.

Only four of the ten planned cases were measured in that earlier evaluation.
The other cases need eligibility and environment review before claims about
all ten are appropriate. Do not present these experiments as a complete
ten-case evaluation or as evidence of improved repair success.

## Completed changes

The following changes were committed and pushed to `demo-2`:

- `935e646`: Rust diagnostic parsing and configuration reporting fixes.
- `cca1763`: skip a model follow-up when reported token usage exhausts the budget.
- `100ebc6`: bound fallback build-log context and mark truncation.
- `044c1b2`: exclude Rust warnings from error diagnostics.
- `1561f69`: exclude quilt backup files from diagnostic source resolution.
- `dc6810e`: clarify edit batches and unique replacement context.
- `7e1f19c`: bounded feedback for rejected full-agent edits.
- `bf39923`: regression coverage for correction after an ambiguous edit rejection.
- `474ad0c`: individual edit outcomes and recorded attempt feedback.

This is a milestone list, not a complete Git log.
The baseline agent was not intentionally changed by this work.

## What the evidence shows

The agent applies some useful edits, but continues to encounter missing or
ambiguous replacement text, repeated investigation, and remaining type errors.
Token, turn, and build limits each stopped different experiments.
Raising limits alone has not produced a verified repair.

The final experiment applied five replacements across three attempts and
reached the remaining checkout-call error. Another run applied 23 replacements
in one attempt but also failed. Edit count alone is not a success metric.

## Next work, in order

1. Inspect the full compiler explanation and patch around `svn_client_checkout3`,
   including expected versus actual argument types and the binding declaration.
2. Check whether the model received that detailed explanation or only a headline.
   Improve focused compiler feedback if necessary.
3. Inspect repeated reads, searches, and truncated outputs. Provide precise file
   ranges and better definition locations where the evidence supports it.
4. Add logging for prompt-component sizes and tool activity needed to measure
   investigation costs. Protect credentials and avoid unnecessary source logging.
5. Test targeted changes offline, including partial edit rejection and rollback.
6. Run one fresh Rust experiment with explicit settings and a new output folder.
7. Once a repair succeeds, repeat it and evaluate other eligible cases.

A manual development repair can help establish solvability, but must be labeled
as manual. Autonomous evaluation must start from the original failing source
without being supplied that repair.

## Evaluation discipline

- Keep original evidence and the frozen baseline unchanged.
- Record commit, local diff, model, configuration, and all resource limits.
- Compare tokens to first edit, rejected edits, turns, builds, and verified repairs.
- Separate exploratory tuning on this public case from broader evaluation.
- Do not infer general improvement from a single run.
- Download and verify evidence before stopping EC2.

## Resuming work

The code milestone is `474ad0c` on `demo-2`; this documentation commit comes later.
Use a clean checkout and review settings before any new paid run.
Set the model key in the actual tmux session without putting it in documentation
or Git. EC2 public IP addresses may change after restarting.

Full-agent tests:

    python3 -m unittest discover -s tests/full-agent -p 'test_*.py'

Harness tests, when relevant:

    python3 -m unittest discover -s harness/tests -t .
