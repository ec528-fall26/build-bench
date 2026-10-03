# Harness (Part 5)

The local test setup around the agent: unpack a case, run the agent, repack the
source package, build it with the organizers' validator, and record the result.

**Never part of the submission.** The agent ZIP is built from
`buildbench-starter-kit-0.1.0-rc.2/agents/one-shot/`; this folder sits outside
the starter kit so it cannot be packaged by mistake, and so a newer starter-kit
release can replace that folder without touching our code.

Background and measured results: [`docs/part5-findings.md`](../docs/part5-findings.md).

## Contents

| File | Purpose | Status |
| --- | --- | --- |
| `record.py` | Read a validator result folder into one outcome; append JSONL rows | Done |
| `tests/` | Unit tests, using real `build-result.json` files from the rcran runs | Done |
| `run_case.py` | One case end to end: baseline build → `dpkg-source -x` → agent edits → `dpkg-source --auto-commit -b` → `run.sh` → record | Done — verified on the ARM64 host 1 October (see below) |
| `replays/` | Edit files for the stand-in model: `rcran-known-fix.json` (the verified repair) and `rcran-comment-only.json` (negative control) | Done |

## What each run records

One row per attempt in `runs.jsonl`, and the same row as `record.json` in the run
folder. This is the project's run record; the agent's `types.py` has no
`RunRecord`, because a run's outcome only exists after the agent has exited.

| Field | Meaning |
| --- | --- |
| `run_id`, `case_id`, `started_at`, `wall_seconds` | which run, which case, when, how long |
| `agent_version` | `agent.yaml` version + fingerprint of the agent's source, e.g. `0.1.0+3f9a1c2b4d5e`. Changes whenever any agent `.py` file changes, committed or not |
| `model`, `mode` | the model used (`replay:<file>` for the stand-in) and `one-shot` |
| `edits_proposed`, `edits_applied`, `edit_warnings` | what the model asked for, what Part 4 accepted, and Part 4's rejection reasons |
| `diff_path` | `repair.diff`: the agent's actual change, or `null` if nothing changed. Taken before repacking, so it shows only the agent's edit |
| `rationale`, `usage` | the model's explanation and token counts (`null` = unknown, never 0) |
| `baseline`, `result` | the validator's verdict before and after the repair |
| `repaired`, `termination_reason`, `error` | the verdict and why the run stopped |

## Repaired means

`status == "succeeded"` **and** `artifact_validation_passed == true`.

`patch_applied` is ignored on purpose. It reports the validator's own `--patch`
option, which we never use — our repair is inside the rebuilt source package —
so it stays `false` even on a successful repair.

## Verified on the ARM64 host

| Run | Replay | Result | Time |
| --- | --- | --- | --- |
| `fix-auto-1` | `rcran-known-fix.json` | **REPAIRED** (`build_succeeded`), 1/1 edits | 174.5 s (baseline + repair build) |
| `control-1` | `rcran-comment-only.json` | not repaired (`build_failed`), 1/1 edits | 83.2 s (`--log`, repair build only) |

The control applies a real edit that fixes nothing; the harness reports it as a
failure. That is what makes a REPAIRED verdict meaningful.

## How the agent is wired in

Parts 1, 2 and 4 always run for real: `run_case` calls Part 1's `run_repair`,
which calls Part 2's `extract_tail` and Part 4's `apply_edits`. The model is one of:

| Flag | Model | Use |
| --- | --- | --- |
| `--replay FILE` | `ReplayModel`: returns the edits stored in `replays/*.json` | Checking the pipeline with answers known to be right (`rcran-known-fix.json`) or wrong (`rcran-comment-only.json`) |
| `--live` | Part 3's `ModelClient`: one call to `openai.gpt-oss-120b-1:0` through Bedrock's OpenAI-compatible endpoint | Measuring the agent |

## Live runs

`--live` needs a Bedrock API key in the environment (enable `gpt-oss-120b`
model access in the Bedrock console, `us-east-1`, then create the key there):

```bash
export BB_MODEL_API_KEY=...          # never commit it; ./bb check does not detect Bedrock keys
python3 -m harness.run_case --bundle ~/buildbench-local-rcran-v1 --live --run-id live-1 \
    --log ~/buildbench-local-rcran-v1/harness-runs/fix-auto-1/baseline/build.log
```

`--log` reuses an earlier baseline's failure log to skip the baseline build. The
run record's `model` field is `openai-compatible:openai.gpt-oss-120b-1:0` and
`usage` holds the token counts. About 1¢ per case.

## Usage

```bash
# from the repository root
python3 -m unittest discover -s harness/tests -t .
python3 -m harness.record results/baseline-1 results/fix-1
```

```text
baseline-1           failed          not repaired    89s  build failed; expected binary artifact pattern ...
fix-1                succeeded       REPAIRED        87s  build completed successfully
```

On the ARM64 host, with the rcran bundle unpacked:

```bash
python3 -m harness.run_case --bundle ~/buildbench-local-rcran-v1 \
    --replay harness/replays/rcran-known-fix.json --run-id fix-auto-1
```

Without `--log` it first builds the unrepaired case to get the failure log the
agent reads, so a full run is two builds, about three minutes. Results go to
`<bundle>/harness-runs/<run-id>/`, and every run appends one row to
`<bundle>/harness-runs/runs.jsonl`. A run id can't be reused.

Exit code is 0 whenever the pipeline completes, repaired or not, and 1 only for
infrastructure failures (`unpack_failed`, `repack_failed`, `build_error`,
`baseline_error`).

Standard library only; Python 3.10 or newer.
