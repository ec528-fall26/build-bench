# Docs

**Current** means the doc describes the agent and the competition as they are
now. **Historical** means it is a record of a milestone: accurate for its
date, but don't plan from it.

## Start here

| Doc | What it is | |
| --- | --- | --- |
| [hosted-submission.md](hosted-submission.md) | The hosted contract, how the agent meets it, how to package and submit, and the Smoke status | Current |
| [no-build-live-findings.md](no-build-live-findings.md) | Live runs without build feedback (2–3 Nov): what went wrong, conclusions and next steps | Current |

## Course deliverables

| Doc | What it is | |
| --- | --- | --- |
| [design-proposal.md](design-proposal.md) | Project proposal | Historical (1 Oct) |
| [design-document.md](design-document.md) | Design document and reproduction guide for Demo 2 | Historical (21 Oct) |

## How the full agent works

The full agent's parts. Each doc describes its part's design, files and tests.

| Doc | What it is | |
| --- | --- | --- |
| [full-agent.md](full-agent.md) | The build plan: parts, shared contracts, model reply format. Cited in code. | Historical (14 Oct) |
| [part-a-evidence.md](part-a-evidence.md) | Part A: case loading, failure diagnosis, evidence | Current |
| [part-a-case-analysis.md](part-a-case-analysis.md) | Part A: what the agent could not see on ectrans, libunwind and libmceliece | Current (2 Nov) |
| [part-b-tools.md](part-b-tools.md) | Part B: read-only tools | Current; `read_log` and `search_log` are in the agent README |
| [part-c-loop.md](part-c-loop.md) | Part C: repair loop, candidate retention, edit correction and batch review | Current |
| [part-e-prompts-evaluation.md](part-e-prompts-evaluation.md) | Part E: prompts, reply parsing and evaluation | Current |
| [failure-taxonomy.md](failure-taxonomy.md) | Categories of build failure across the cases | Current |

The full agent's own README
(`buildbench-starter-kit-0.1.0-rc.5/agents/full-agent/README.md`) has the
file-by-file status and the version history.

## Baseline and results

| Doc | What it is | |
| --- | --- | --- |
| [one-shot-agent.md](one-shot-agent.md) | The frozen one-shot baseline (`one-shot-v1`). Cited in tests. | Historical (7 Oct) |
| [part5-findings.md](part5-findings.md) | Environment setup and baseline findings. Cited in code. | Historical (15 Oct) |
| [demo2-results.md](demo2-results.md) | Demo 2: paired one-shot/full-agent evaluation, one-shot details, Rust budget experiments | Historical (19 Oct) |
| [full_submission_plan.md](full_submission_plan.md) | Submission and Demo 3 plan | Not the current plan (28 Oct) |

## Data

These folders are read by tests and CI. Don't move or rename them.

- `evidence/demo2/`: Demo 2 run records, patches and log audit
  (`paired-retest/`), and the Rust budget experiment fields
  (`rust-budget-experiments.json`).
- `evidence/qemu-fix-20261102/`: the x86 cases rebuilt with QEMU 10.2.3.
- `results/part-c-rcran-694ehfba/`: Part C's local rcran checkpoint.
