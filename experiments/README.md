# Experiments

One script per claim. Each script should be runnable from the repository root,
print what it is measuring, and finish in a bounded amount of time.

Every script here must have a matching entry in
[`../docs/design-document.md`](../docs/design-document.md) stating what it
supports, how long it takes, and what output to expect.

| Script | Question it answers | Entry |
| --- | --- | --- |
| `demo3_comparison.py` | Does the full agent repair more cases than the frozen baseline, and how much of that is the repair history? | Experiment 1 |
| `validation_bundle.py` | Do local verdicts agree with the organizers' Development Validation? | Experiment 2 |

Both need the ARM64 host and recorded runs; neither has produced a result yet.
Tests: `python3 -m unittest discover -s tests/experiments`.
