# Full Build-Bench Agent

## Purpose
The project's full agent: it reads the failure, investigates the package with
read-only tools, proposes a repair with a stated hypothesis, tests it when build
feedback is available, and revises. Plan: `docs/full-agent.md`.

## Status: scaffold
Copied from the frozen one-shot baseline (`agents/one-shot`, tag `one-shot-v1`).
`main.py` still runs the baseline's single attempt until Part C's loop replaces it.

| File | Part | Status |
| --- | --- | --- |
| `contracts.py` | shared | Agreed 7 October. Change only by team agreement |
| `evidence.py` | A | Stand-in: last log line as one unresolved diagnostic |
| `tools.py` | B | Stand-in: every tool answers `unavailable` |
| `build_feedback.py` | D | `NoBuildFeedback`: returns `None` until the organizers publish the protocol. The local build callback lives in the harness, never here |
| `main.py` | C | Baseline behaviour for now |
| `model_client.py`, `log_tail.py`, `edit_applier.py`, `types.py` | baseline Parts 2–4 | Copied, not shared, so the baseline stays frozen |

Tests: `full-agent-tests/tests/` at the repository root.

## Model access
Configured through environment variables, never through files in this folder:

- `BB_MODEL_API_KEY` — API key for the endpoint (required for a repair).
- `BB_MODEL_BASE_URL` — endpoint base URL (default: Amazon Bedrock's
  OpenAI-compatible endpoint in `us-east-1`).

The model is fixed in code (`MODEL_ID` in `model_client.py`). Without a key the
agent still completes, makes no request and proposes no edits.

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