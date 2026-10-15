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
| `build_feedback.py` | Part D: the full agent's local build callback — repacks a copy of the worktree, runs `run.sh`, returns a `BuildResult` with Part A applied | Done — verified on the ARM64 host 14 October (see below) |
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
| `served_model` | the model the endpoint **reports** answered, e.g. `openai.gpt-oss-120b-1:0`; `null` for replays or when no reply arrived. Confirms `model` rather than inferring it |
| `edits_proposed`, `edits_applied`, `edit_warnings` | what the model asked for, what Part 4 accepted, and Part 4's rejection reasons |
| `fix_type`, `changed_files` | what the repair touched: `build_commands`, `source` (upstream files), `packaging` (only `input/debian/`), or `none` |
| `build_commands`, `needs_review` | added build-time commands that modify files (`sed -i`, `rm`, `>` redirects, …) and whether a person must read the diff |
| `diff_path` | `repair.diff`: the agent's actual change, or `null` if nothing changed. Taken before repacking, so it shows only the agent's edit |
| `rationale`, `usage` | the model's explanation and token counts (`null` = unknown, never 0) |
| `baseline`, `result` | the validator's verdict before and after the repair |
| `repaired`, `termination_reason`, `error` | the verdict and why the run stopped |

## Reviewing results

Commands added to a build file run during the build and can change any file;
Part 4 checks the edit, not what those commands do. `diff_scan.py` flags them so
only those runs need a person to read the diff. It is a heuristic: commands
hidden behind variables, `eval` or scripts are not detected.

```bash
python3 -m harness.summarize ~/buildbench-local-rcran-v1/harness-runs/runs.jsonl \
    --model openai-compatible:openai.gpt-oss-120b-1:0
```

```text
runs: 3   repaired: 3
tokens: 33,288 in + 4,046 out = 37,334 over 3 runs (median 12,337 per run); unknown for 0

case                                                       runs  repaired      tokens  median/run  unknown
launchpad-mantic-amd64-arm64-r-cran-digest-7a42effc961f       3         3      37,334      12,337        0

fix type          runs  repaired      tokens  median/run  unknown
build_commands       3         3      37,334      12,337        0

needs review (build-time commands): live-1, live-2, live-3
```

Report repair rates split by fix type, and count a flagged run only after its
diff has been read. Older rows are classified from their `repair.diff`.

**Tokens** are input + output per run. A run whose counts are missing (no reply,
or a replay) is reported in the `unknown` column and never added as zero, so
totals and medians cover known runs only. Filter by `--agent-version` (or
`--model`) so different agent versions are never pooled.

## Build feedback for the full agent (Part D)

`LocalBuildFeedback` implements the agent's `BuildFeedback` contract on the local
bundle. The full agent's loop calls `request_build(worktree)` after each edit:

- The agent's worktree is never changed: `input/` is copied, repacked with
  `dpkg-source --auto-commit -b`, assembled into a case and built with `run.sh`.
- A bad candidate is a **failed build, not an error**, so the loop can rank it and
  revise. Each build's `kind` says why: `succeeded`, `build_failed`, `timed_out`,
  `no_real_build` (never reached compilation, e.g. a broken `debian/control`) or
  `repack_failed` (dpkg-source rejected it). The last two rank lowest.
- Only a validator that writes no `build-result.json` raises (`validator_error`).
- `excerpt=True` gives Part A only the lines around the first 5 errors and the
  last 20 lines, our stand-in for the competition's "diagnostic excerpts".
- Every build gets `build-NN/` with the copy, the case, the validator output and
  `feedback.json`; `feedback.records` holds the same rows for the run record.

By hand, on the ARM64 host:

```bash
python3 -m harness.build_feedback --bundle ~/buildbench-local-rcran-v1 --prepare ~/wt
# edit ~/wt/input/... (or leave it unchanged to reproduce the failure), then:
python3 -m harness.build_feedback --bundle ~/buildbench-local-rcran-v1 --worktree ~/wt [--excerpt]
```

The worktree must look like the one `dpkg-source -x` leaves: `input/` plus the
upstream tarball (`*.orig.tar.*`) beside it, which format 3.0 (quilt) needs to
repack. `--prepare` and `run_case` both produce that.

### Verified on the ARM64 host (14 October)

The plan's "Done when" for Part D, on rcran with the real validator. No model
involved: the worktree was edited by hand.

| Build | Worktree | Result | Time |
| --- | --- | --- | --- |
| 1 | unchanged | failed (`build_failed`), stage `install`; first diagnostic `input/src/crc32c/crc32c_prefetch.h:18 fatal error: xmmintrin.h`, 7 in all | 95.1 s |
| 2 | known fix (`HAVE_MM_PREFETCH 0`) | **SUCCEEDED**, stage `packaging` | 98.3 s |
| 3 | unchanged, `--excerpt` | failed, stage `install`; same first diagnostic, from **25 of 1,873** log lines | 91.4 s |

In excerpt mode the first three diagnostics are labelled `unknown` instead of
`install`: the `dh_auto_install` marker is far above the error and outside the
excerpt, and Part A does not guess a stage without one. The build's
`stage_reached` is still `install`, from later diagnostics. Whether to keep the
last stage marker in the excerpt depends on the organizers' excerpt format,
which is not published.

The first attempt failed with `repack_failed`: only `input/` was copied, and
dpkg-source needs the upstream tarball beside it. The fake validator now
requires the tarball the same way, so the tests catch it.

It replaces `part_c_build.py`, Part C's checkpoint adapter for Docker Desktop on
macOS (retired 14 October with the Part C owner's agreement).

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
