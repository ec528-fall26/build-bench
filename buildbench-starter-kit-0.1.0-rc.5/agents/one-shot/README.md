# One-Shot Build-Bench Agent

## Purpose
The project's naive baseline: it reads the end of the build log and a fixed set
of package files, asks an LLM once for a repair, and applies the permitted edits
to the package worktree. One repair attempt, no repair history, no file
selection. (Failed network requests are retried; the repair is not.)

## How it works
1. `log_tail.py` reads the last 500 lines of the failure log.
2. `model_client.py` sends one OpenAI-compatible chat completion with the log
   and a fixed file list, and parses the reply into edits.
3. `edit_applier.py` checks and applies each edit separately.
4. `main.py` writes `agent-result.json` with status `completed`, the model's
   rationale, and how many edits were proposed and applied.

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