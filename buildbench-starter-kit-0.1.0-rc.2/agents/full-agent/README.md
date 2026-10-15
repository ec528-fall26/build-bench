# Full Build-Bench Agent

## Purpose
The project's full agent: it reads the failure, investigates the package with
read-only tools, proposes a repair with a stated hypothesis, tests it when build
feedback is available, and revises. Plan: `docs/full-agent.md`.

## Status: wired; repaired rcran in 4 of 4 live runs (14 October)
All parts are connected (14 October): `main.py` diagnoses the failure log (Part A),
runs Part C's loop with Part E's prompts and parser, and writes
`agent-result.json` with every attempt, model turn and token count. Submitted,
it has no build feedback, so edits are investigated and revised but submitted
untested. Locally, `harness/run_case.py --agent full-agent` runs the same loop
with real builds (Part D). Results: `docs/part5-findings.md` §9c.

| File | Part | Status |
| --- | --- | --- |
| `contracts.py` | shared | Agreed 7 October. Change only by team agreement |
| `evidence.py`, `case.py` | A | Streaming diagnostics, source-path resolution, and read-only case metadata loading. See `docs/part-a-evidence.md` |
| `tools.py`, `path_checks.py`, `diff_tracker.py`, `tool_timeout.py` | B | Six read-only tools; called by the loop, which caps tool calls per case (`max_tool_calls`) |
| `build_feedback.py` | D | `NoBuildFeedback`: returns `None` until the organizers publish the protocol. The local build callback lives in the harness, never here |
| `loop.py`, `candidates.py` | C (Juliette Jacques) | The repair loop and candidate retention. See `docs/part-c-loop.md` |
| `model_adapter.py`, `main.py` | C (wiring) | The loop's `next_turn`: rebuilds each request from the loop's snapshot with Part E's text, one model call per turn. `main.py` is the entry point; `run_agent()` is shared with the harness |
| `prompts.py`, `reply.py`, `config.py` | E (Owen Zhang) | Instructions and per-turn messages, reply text → `ModelTurn`, and the ablation switches. Used every turn through `model_adapter.py`. See `docs/part-e-prompts-evaluation.md` |
| `model_client.py`, `log_tail.py`, `edit_applier.py`, `types.py` | baseline Parts 2–4 | Copied, not shared, so the baseline stays frozen |

Tests: `tests/full-agent/` at the repository root.

Part B arguments, limits, and Part C integration:
[Read-only tools guide](../../../docs/part-b-tools.md).

Part E prompts, reply parsing, switches and the Demo 3 comparison:
[Prompts and evaluation guide](../../../docs/part-e-prompts-evaluation.md).

## Model access
Configured through environment variables, never through files in this folder:

- `BB_MODEL_API_KEY` — API key for the endpoint (required for a repair).
- `BB_MODEL_BASE_URL` — endpoint base URL (default: Amazon Bedrock's
  OpenAI-compatible endpoint in `us-east-1`).

The model is fixed in code (`MODEL_ID` in `model_client.py`). Without a key the
agent still completes, makes no request and proposes no edits.

## Ablation switches
`src/config.py` reads one variable per `AgentConfig` field. Unset means on, which
is how the competition runs the agent; experiments set them to `0` to switch one
capability off. They take effect once Part C's loop calls `config_from_env()`.

- `BB_AGENT_USE_EVIDENCE_MANAGER`, `BB_AGENT_USE_TOOLS`,
  `BB_AGENT_USE_BUILD_FEEDBACK`, `BB_AGENT_USE_HISTORY`

## Runtime
Python 3.11. The current implementation uses only the Python standard library.

## Workspace
The workspace defaults to `/workspace`. For local testing, its location
can be overridden using the `BB_WORKSPACE` environment variable.

Required inputs:
- `input/task.json`
- `input/initial-build.log`
- `work/repo/`

Output:
- `output/agent-result.json`

A status of `completed` indicates that the agent finished running.
Repair success requires independent build and artifact validation.

## Entry point
Run `python -m src.main` from the agent directory.
