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
| `syntax_guard_stress.py` | Does the 0.5.1 syntax guard pass harmless edits and catch broken ones on real packages? No model needed | Agent README, 0.5.1 |
| `syntax_guard_audit.py` | Which recorded no-build repairs (a `run_public` folder) would the syntax guard have sent back? No model needed | Agent README, 0.5.1 |
| `sample-0.5.1.txt` | The 20 cases for a live check of 0.5.1: 8 lost to broken edits in 0.3.3, 8 passed, 4 architecture failures | — |

Both need the ARM64 host and recorded runs; neither has produced a result yet.
Tests: `python3 -m unittest discover -s tests/experiments`.
