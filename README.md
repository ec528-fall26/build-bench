# EC528 Project — Build-Bench Challenge: Autonomous LLM Agents for Cross-Architecture Package Repair

**Team:** Joonseo Moon, Austin Li, Owen Zhang, Juliette Jacques, Anthony Capraru
**Mentor:** Minghua Ma (Microsoft)
**Project:** an LLM agent that repairs Debian source packages whose build fails
when they move between CPU architectures (x86_64, aarch64, riscv64).

## What this is

Packages that build on one architecture often fail on another: x86-only
intrinsics or `sys/io.h`, `char` signedness, symbols files written for another
architecture, hard-coded `x86_64-linux-gnu` paths. The
[Build-Bench Challenge](https://matrix.cstcloud.cn/build-bench/) gives an agent
the failing build's log and the package source, lets it edit the source once,
and then rebuilds the package cleanly on the target. The score is the share of
packages that build and produce their artifacts.

Our **full agent** reads the log (Part A), investigates the package with
read-only tools (Part B), and proposes edits in a bounded loop (Part C) with
prompts and a playbook of known cross-architecture causes (Part E). Because the
competition allows no builds during a run, every edit is checked before it is
written: a rules guard refuses test-skipping and build bypasses, and a syntax
guard compiles the edited files for the target and sends back any edit that
would not parse. The frozen **one-shot agent** (`one-shot-v1`) is the baseline.

## Quick start

Python 3.11 or newer; no third-party packages.

```bash
git clone https://github.com/ec528-fall26/build-bench && cd build-bench

# 1. The test suites (about a minute; no model, no network)
python3 -m unittest discover -s tests/full-agent
python3 -m unittest discover -s harness/tests -t .
python3 -m unittest discover -s tests/experiments

# 2. The syntax guard on real packages, without a model
#    (Debian/Ubuntu with dpkg-dev and gcc; downloads the 223 MB public case set)
curl -O https://matrix.cstcloud.cn/build-bench/releases/training-set/v0.1/buildbench-development-cases-v0.1.zip
unzip -q buildbench-development-cases-v0.1.zip
python3 experiments/syntax_guard_stress.py buildbench-development-cases-v0.1 10

# 3. The agent on public cases, as it runs when scored (no builds; needs a
#    model: an OpenAI-compatible endpoint, by default gpt-oss-120b on Bedrock)
read -s BB_MODEL_API_KEY; export BB_MODEL_API_KEY
python3 -m harness.run_public run --cases-root buildbench-development-cases-v0.1 \
    --out runs/v051 --tag v051 --case r-cran-digest-7a42effc961f
python3 -m harness.run_public status --out runs/v051
```

`BB_MODEL_BASE_URL` and `BB_MODEL_ID` select another endpoint or model. To have
the organizers build the result, `python3 -m harness.run_public bundle` writes a
Worktree Bundle for the website's Development Validation.

## Repository layout

| Path | Contents |
| --- | --- |
| `buildbench-starter-kit-0.1.0-rc.5/agents/full-agent/` | **The submitted agent.** `src/` holds every part; its [README](buildbench-starter-kit-0.1.0-rc.5/agents/full-agent/README.md) has the file-by-file status and version history |
| `buildbench-starter-kit-0.1.0-rc.5/agents/one-shot/` | The frozen baseline (`one-shot-v1`); CI guards it |
| `buildbench-starter-kit-0.1.0-rc.5/` | The organizers' Starter Kit rc.5: runner, checks, `./bb package` |
| `harness/` | Local evaluation, never part of the submission: `run_case.py` (one case with real builds), `run_public.py` (public cases without builds, bundled for Development Validation) |
| `experiments/` | One script per claim: comparisons, validation bundles, syntax-guard audit and stress test |
| `tests/` | Unit tests for the full agent, the baseline's parts and the experiments; CI runs them on Python 3.11 and 3.12 |
| `docs/` | Design proposal and document, part designs, findings and the hosted-submission guide; start at [`docs/README.md`](docs/README.md) |
| `slides/` | Demo slides |

## Reproducing our results

See [`docs/design-document.md`](docs/design-document.md) and
[`experiments/README.md`](experiments/README.md). How the agent is packaged and
qualified on the competition website:
[`docs/hosted-submission.md`](docs/hosted-submission.md).

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
