# EC528 Project — Build-Bench Challenge: Autonomous LLM Agents for Cross-Architecture Package Repair

**Team:** Joonseo Moon, Austin Li, Owen Zhang, Juliette Jacques, Anthony Capraru

**Mentor:** Minghua Ma (Microsoft)

**Project:** An LLM agent that repairs Debian source packages whose builds fail
when moving between CPU architectures.

## Demo 3: selected agent

**For this demo, use agent 0.3.2 in
`buildbench-starter-kit-0.1.0-rc.5/agents/full-agent-v032/`.**

Our demo milestones were completed earlier to meet the competition deadline
of November 14, 2026. This branch preserves the historical agent selected
for Demo 3 rather than substituting later development.

The agent is taken from commit
`feaa5714d9a4c9738dee46c9190e4d81d05c43af`.
Its recorded evaluation findings are in commit
`7774267f14b2eaafecbf11332660e4d223fa1b75`.

The historical evaluation reported **4 of 11 frozen local cases repaired**,
with **four x86 cases undetermined under QEMU**. These are historical results,
not a new evaluation of this branch.

See [Demo 3 configuration and validation notes](docs/demo-3-agent.md).

## How the Demo 3 agent works

The agent reads the original failure log, investigates package source with
read-only tools, and proposes edits in a bounded loop. Version 0.3.2 includes
batch-edit repair and recognized cross-architecture failure patterns.

In the demo configuration, the agent receives no iterative build feedback.
The local harness validates the final edited package with a clean build.

## Quick start

Use Python 3.11 or newer.

```bash
git clone --branch demo-3 --single-branch https://github.com/ec528-fall26/build-bench.git
cd build-bench

# Harness checks: no model calls or package builds.
python3 -m unittest discover -s harness/tests -t .
```

### Run a frozen local case

Run this on the configured build host with Docker, the case bundle's runtime
image, and any required cross-architecture emulation already installed.
A frozen validator bundle is required; public source and log files alone
are not sufficient for this local build evaluation.

Set BUNDLE to the existing validator bundle containing run.sh and case/.
For example, on our experiment host:

```bash
export BUNDLE="$HOME/buildbench-local-rcran-v1"
```

Set the model credentials and demo configuration:

```bash
read -s BB_MODEL_API_KEY
export BB_MODEL_API_KEY

export BB_AGENT_USE_EVIDENCE_MANAGER=1
export BB_AGENT_USE_TOOLS=1
export BB_AGENT_USE_BUILD_FEEDBACK=0
export BB_AGENT_USE_HISTORY=1
export BB_REPAIR_STRATEGY=default
export BB_REVIEW_EDITS=0
```

Run the explicitly selected Demo 3 agent:

```bash
python3 -m harness.run_case \
  --bundle "$BUNDLE" \
  --agent full-agent-v032 \
  --live \
  --run-id "demo3-v032-$(date -u +%Y%m%dT%H%M%SZ)" \
  --runs-dir "$HOME/buildbench-experiments/demo3-v032"
```

This command runs a baseline build, invokes the agent, and validates the
final result when edits are applied. Judge repair success from the harness
verdict and record.json, not merely the command's exit code.

Use the command above for this demo. Other launchers and experiments retained
in the repository may select a different agent or enable build feedback.

## Repository layout

| Path | Contents |
| --- | --- |
| `buildbench-starter-kit-0.1.0-rc.5/agents/full-agent-v032/` | **Demo 3 agent, version 0.3.2.** Includes its historical README and source. |
| `docs/demo-3-agent.md` | Version selection, historical results, configuration, and validation limits. |
| `harness/` | Local case execution and final build validation. |
| `harness/tests/` | Harness tests, including historical-agent configuration compatibility. |
| `docs/design-document.md` | Project design document. |
| `slides/` | Demo slides. |

Other development code and tests remain in this branch for continuity.
The Demo 3 entry point is the explicitly selected agent above.

## Submission checklist

Deliverables are collected from a **branch named for the demo**, at **12:00 noon**
on the day of that demo. See the
[submission instructions](https://ec528.github.io/ec528/fall26/setup/).

| Deliverable | Branch | Must contain |
| --- | --- | --- |
| Demo 1 | `demo-1` | slides, code, `docs/design-proposal.md` |
| Demo 2 | `demo-2` | slides, code, `docs/design-document.md`, demo video |
| Demo 3 | `demo-3` | slides, code, updated `docs/design-document.md`, demo video |
| Final | `final-demo` | slides, code, artifact documentation, recorded video presentation |
