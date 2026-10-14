# Design Document

*Due in the `demo-2` branch, updated for `demo-3` and `final-demo`.
**The TA will run this document.** If they cannot follow it, you lose the points.*

## 1. Architecture

What you actually built — not what you originally planned. Include a diagram.

## 2. Design decisions

The choices that mattered, the alternatives, and why you chose as you did.
Include the things that did not work.

## 3. Setup

Exact dependencies, versions, and hardware assumptions. Written so that someone
starting from a clean machine can follow it.

```bash
# every command needed to go from a fresh machine to a runnable system
```

## 4. Running the experiments

One entry per claim you make. Someone else runs these; you do not get to
explain them in person.

### Experiment 1: does the full agent repair more cases than the frozen baseline, and how much of that is the repair history? (no result yet)

| | |
| --- | --- |
| Supports | The Demo 3 comparison table ([`full-agent.md`](full-agent.md), "Demo 3 comparison"). **No result yet:** the full agent's loop is not implemented, so today the command measures only `one-shot-v1` and reports the other two configurations as `not run` |
| Needs | The ARM64 host with Docker and `dpkg-dev`, at least one local validator bundle unpacked as `~/buildbench-local-*` (see [`part5-findings.md`](part5-findings.md) §2–§3), and `BB_MODEL_API_KEY` |
| Command | `python3 experiments/demo3_comparison.py` (re-run to resume; `--report-only` reprints the table) |
| Expected runtime | Per case: one unrepaired build, then 3 runs of each configuration. Measured rcran runs of the baseline took about 2 minutes each ([`part5-findings.md`](part5-findings.md) §9). The command prints its upper bound before starting: 125 minutes per run with the rcran manifest's limits |
| Expected output | Progress lines `[n/N] <configuration> r<k> <case>: REPAIRED / not repaired (<reason>)`, then a Markdown table: runs, verified repairs per repeat and their mean, median tokens, median wall time, mean attempts and infrastructure failures per configuration, followed by per-case and per-direction counts, termination reasons, fix types, gains and regressions. Repair counts vary between runs of the same configuration (the model is not deterministic at temperature 0), so a match is the same table structure with every run recorded, not identical counts. Run records: `~/buildbench-experiments/demo3-comparison/<configuration>/runs.jsonl` |
| Check without a key | `python3 experiments/demo3_comparison.py --configurations one-shot-v1 --repeats 1 --replay harness/replays/rcran-known-fix.json --out /tmp/demo3-check` uses the harness's stand-in model with the known rcran fix; on the ARM64 host it is expected to end with `REPAIRED (build_succeeded)` and a table marked as a pipeline check |

Details: [`part-e-prompts-evaluation.md`](part-e-prompts-evaluation.md).

### Experiment 2: do the local validator's verdicts agree with the organizers' environment? (not yet performed)

| | |
| --- | --- |
| Supports | Reporting local repair counts as verified repairs. **Not yet performed** |
| Needs | Recorded runs from Experiment 1 and the team's Build-Bench account (Development Validation is a web upload, one task per team at a time) |
| Command | `python3 experiments/validation_bundle.py <runs folder> [<runs folder> ...] --sample 4 --output development-validation.zip`, upload the ZIP, enter the official verdicts in `development-validation.checks.json`, then `python3 experiments/validation_bundle.py --compare development-validation.checks.json` |
| Expected runtime | Seconds to build the ZIP. The validation run's duration is the website's and has not been measured |
| Expected output | A ZIP with a root `manifest.json` and `cases/<case_id>/worktree/input/` per case, and after `--compare` one line `k of n checked local verdicts agree with Development Validation`, with any disagreement listed |

Details: [`part-e-prompts-evaluation.md`](part-e-prompts-evaluation.md), "Ground truth".

### Experiment 3: <...>

## 5. Claims and limitations

What your artifact supports, and an honest account of what does not work or was
not tested. *An honest narrower result scores better than an impressive result we
cannot reproduce.*
