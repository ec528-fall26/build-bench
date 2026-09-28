# One-Shot Agent — Build Plan

**Status:** planning · **Target:** working end to end before Demo 2 (10/21)
**Team:** Anthony Capraru, Austin Li, Joonseo Moon, Juliette Jacques, Owen Zhang

## What we are building

The simplest agent that can repair a package: read the tail of the failure log,
call the model **once**, apply the edit it proposes, exit. No hypothesis loop, no
evidence selection, no repair history, no in-loop builds.

## Why this first

1. **It is a required deliverable.** The design proposal (§4, Comparison plan)
   commits us to running a one-shot comparator on every frozen case set,
   regardless of whether the official baseline runs. It is the control for our
   claim that evidence selection and repair history earn their cost.
2. **It exercises the whole pipeline.** Submission contract, `./bb ready`, model
   access, worktree editing, canonical patch generation, validation. If this
   works, the full agent is an upgrade to a working system instead of an
   integration risk we discover in week four.
3. **It is the honest floor.** Whatever the full agent scores, we will be asked
   "is that better than just prompting a model?" This is how we answer.

## Scope

**In scope:** log tail extraction, one model call, structured edit application,
contract-compliant output, a harness that runs a case and records the result.

**Out of scope for now:** planner, hypothesis tracking, repair history, tool
calls, in-loop build feedback, multiple candidates, log slicing beyond the tail.
Those are Demo 3 material. Do not build them here.

## Shared contracts

Agree on these **before** anyone writes code, so the five parts can be built in
parallel against stubs.

```python
# types.py — owned by Part 1, used by everyone

@dataclass
class CaseContext:
    case_id: str
    worktree: Path          # /workspace/work/repo
    log_tail: str           # from Part 2
    task_metadata: dict     # parsed task.json

@dataclass
class Edit:
    path: str               # relative to worktree
    old_text: str
    new_text: str

@dataclass
class EditPlan:
    edits: list[Edit]
    rationale: str
    usage: dict             # {"input_tokens": int|None, "output_tokens": int|None}

@dataclass
class RunRecord:
    case_id: str
    agent_version: str
    model: str
    status: str             # completed | agent_error | no_edit
    edits_applied: int
    usage: dict
    wall_seconds: float
    termination_reason: str
```

Runtime paths are fixed by the platform:

| Path | Access | Contents |
| --- | --- | --- |
| `/workspace/input/task.json` | read-only | case metadata |
| `/workspace/input/initial-build.log` | read-only | the failure log |
| `/workspace/work/repo/` | writable | the package worktree — the only thing we may edit |
| `/workspace/output/agent-result.json` | writable | our completion output |

---

## Part 1 — Agent shell and submission contract

**Owner:** _______

**What it does.** Owns `agents/one-shot/`: `agent.yaml`, `requirements.lock`,
`src/main.py`. Reads the workspace, calls Parts 2–4 in order, writes
`agent-result.json`, exits 0. Defines `types.py` above.

**What it needs.** The starter kit, Docker, the hello example case. Nothing else.
Can start immediately.

**Done when.** `./bb ready --agent ./agents/one-shot --json` returns
`"status": "succeeded"` and the ZIP SHA-256 matches the reported artifact.

**Watch for.** Every dependency pinned with `==`. No credentials, caches, venvs,
or `runs/` output in the ZIP. Catch exceptions and record them as
`agent_error` — a crash must still produce output, not a silent failure.

---

## Part 2 — Log tail extractor

**Owner:** _______

**What it does.** `extract_tail(path: Path, n_lines: int) -> str`. Returns the
last N lines of the build log.

**The catch:** one log in our dataset is 2.37 GB. This must **stream from the end
of the file**, never `read()` the whole thing. The agent container has 1 GB of
memory and a 64 MB `/tmp`.

**What it needs.** One downloaded log to test against, ideally the libyuv one.
No AWS, no model, no Docker. This part can make real progress on day one.

**Done when.** It returns the last 500 lines of a 2.37 GB log in under a second
with flat memory use, and handles: missing file, empty file, non-UTF-8 bytes,
a file with fewer than N lines, and no trailing newline.

**Watch for.** Do not "clean up" the log here. Truncation and de-duplication are
Evidence Manager work for the full agent. This part stays dumb on purpose — that
is what makes it a control.

---

## Part 3 — Model adapter

**Owner:** _______

**What it does.** `ModelClient.propose(context: CaseContext) -> EditPlan`. Builds
the prompt, makes exactly one call, parses the response into `Edit` objects,
records token usage.

**What it needs.** A decided model and provider, an API key, and a spending
ceiling. **These are open team decisions — resolve them first.**

**Blocked on:** how a submitted agent reaches a model at all. The sandbox runs
with `--network none`. Until the organizers confirm the gateway, develop against
a **recorded-response stub**: a directory of saved model responses keyed by case,
so Parts 1, 4 and 5 can be built and tested without network or spend.

**Done when.** Given a `CaseContext`, it returns a parsed `EditPlan` with usage
recorded, deterministically (temperature 0, fixed prompt), and the stub mode
works offline.

**Watch for.** Unknown token usage is recorded as `None`, **never zero** — §4
depends on this. Malformed model output returns an empty `EditPlan` with a
reason, it does not raise. Decide the response format up front (search/replace
blocks are easier to validate than unified diffs) and write it down here.

---

## Part 4 — Edit applier

**Owner:** _______

**What it does.** `apply_edits(worktree: Path, edits: list[Edit]) -> int`.
Validates each edit, applies it, returns how many landed.

**Validation rules, all mandatory:**

- Resolved path stays inside the worktree. Reject traversal and symlinks.
- `old_text` occurs **exactly once** in the file. Zero or many means reject.
- File is valid UTF-8 text.
- Reject edits that disable the build rather than repair it — removing test
  invocations, adding architecture exclusions, emptying install lists. Log the
  rejection; do not silently apply.

**What it needs.** A materialized case worktree, or the hello case to start with.

**Done when.** Edits apply, and `runner/generate_patch.py` produces a patch that
applies cleanly to a fresh copy of the case.

**Watch for.** Known rc.2 bug: editing a file whose original content lacks a
trailing newline can produce a malformed diff, and adding a newline only to the
edited file is not a reliable fix. Test this case explicitly. If it breaks,
**record the compatibility failure — do not modify the official runner.**

---

## Part 5 — Harness and case runner

**Owner:** _______

**What it does.** Everything outside the sandbox. Takes a case, runs the agent
against it, invokes the official validator on a clean copy with the generated
patch, collects `build-result.json`, and writes a `RunRecord` per case plus a
summary table.

**What it needs.** A Linux host with Docker, and eventually AWS for real builds.
Holds the API credentials — **the agent never does.**

**Done when.** One command runs the agent over a list of cases and produces a
results file with every field in `RunRecord`, including rows for cases that
crashed or timed out.

**Watch for.** Crashes and timeouts are rows in the output, not omissions — §4
requires them in the denominator. Record the mode (one-shot vs iterative) on
every row. Keep this out of the submission ZIP entirely.

---

## Integration order

| Step | Needs | Produces |
| --- | --- | --- |
| 1 | Part 1 alone | Agent that passes `./bb ready` and does nothing |
| 2 | + Part 2 | Agent that reads the log and reports what it saw |
| 3 | + Part 4 | Agent that applies a hardcoded edit and repairs hello |
| 4 | + Part 3 (stub) | Full pipeline offline, no spend |
| 5 | + Part 3 (live) | Real one-shot agent |
| 6 | + Part 5 | Measured results on real cases |

Steps 1–4 need no model access and no AWS. **If the protocol question stays
unanswered, we can still reach step 4.**

## Checkpoints

| Date | Target |
| --- | --- |
| 10/07 | Steps 1–3 done. One Debian case materialized and reproducing its failure. Organizer protocol question answered or documented as unanswered. |
| 10/14 | Step 4 done — full pipeline running offline against recorded responses. |
| 10/21 | Step 6 on the 10 frozen cases. Demo 2. |

## Open decisions

Resolve in the first team meeting — each one blocks a part:

1. **Model and provider**, plus a spending ceiling → blocks Part 3.
2. **Edit response format** (search/replace blocks vs unified diff) → blocks
   Parts 3 and 4.
3. **AWS account, budget, and native ARM vs qemu emulation** → blocks Part 5.
4. **Owners for the five parts.**

## Questions for the organizers

Send this week; the answers have lead time.

1. How does a submitted agent reach a model, given the sandbox runs with
   `--network none` and credentials may not ship in the ZIP?
2. What is the in-loop build-feedback API, and how many requests are permitted?
3. Does the validator support execution under qemu emulation, or is native
   target-architecture hardware required?
4. What is the hosted Debian worktree layout, and which paths may an agent edit?
