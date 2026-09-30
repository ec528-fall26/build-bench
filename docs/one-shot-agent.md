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

**Owner:** Juliette

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

**Graceful stop — raised directly by the organizers.** On a self-imposed timeout,
keep the best candidate in the worktree. **Never restore the original files**: a
revert discards a repair that might have built. Reserve wall-clock time for the
stop path rather than being cut off mid-write.

---

## Part 2 — Log tail extractor

**Owner:** Anthony

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

**Owner:** Austin

**What it does.** `ModelClient.propose(context: CaseContext) -> EditPlan`. Builds
the prompt, makes exactly one call, parses the response into `Edit` objects,
records token usage.

**The edit format is now fixed by Part 4.** `apply_edits` accepts only literal
replacements in files that already exist:

- `old_text` must appear **exactly once** in the file — zero matches or two
  matches are both rejected;
- no new files, no deletions, no renames, no symlinks, no permission changes;
- UTF-8 text only, resulting file under 8 MiB;
- paths must sit under the case's allowed prefix (`input/` by default).

The prompt must say all of this, and must tell the model to quote enough
surrounding context to make `old_text` unique.

**Context handed to the model.** The log tail from Part 2, plus a **fixed,
non-adaptive** set of files — the same file list for every case, regardless of
what the log says. Choosing files based on the failure is evidence selection,
which belongs to the full agent. If the comparator gets clever it stops being a
control and the Demo 3 comparison means nothing. Write the chosen file list into
this document once decided.

**Pre-flight validation.** Before returning, check each proposed edit against the
real file: does `old_text` occur exactly once? A one-shot agent gets no retry, so
an edit that Part 4 will reject is a wasted case. Report these as a distinct
outcome — `unusable_edit` — separate from "applied but did not repair." The rate
of unusable edits is a result worth reporting on its own.

**The interface is now known — this part is unblocked.** The organizers confirmed
hosted model access is an **organizer-managed, OpenAI-compatible endpoint**
serving the same undisclosed model to every team, with a common per-case usage
budget. Build against the chat-completions request shape with a **configurable
base URL and no hardcoded model name**, so the same client points at a local
provider during development and at theirs at evaluation. No credentials in the
ZIP.

Because every team gets the identical model, prompts must be **model-agnostic** —
do not tune to one provider's quirks.

**What it needs.** A provider and spending ceiling for local development only.

**Still worth building:** a **recorded-response stub** — saved responses keyed by
case — so Parts 1 and 5 can be tested offline with no spend.

**Done when.** Given a `CaseContext`, it returns a parsed `EditPlan` with usage
recorded, and the stub mode works offline. Use temperature 0 and a fixed prompt
for our own comparison repeatability — the organizers have confirmed determinism
is **not** a competition requirement, only non-interactive execution, so treat it
as a research setting we can relax if it costs repairs.

**Watch for.** Unknown token usage is recorded as `None`, **never zero** — §4 of
the proposal depends on this. Malformed model output returns an empty `EditPlan`
with a reason; it does not raise.

---

## Part 4 — Edit applier

**Owner:** Owen

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

**Owner:** Joonseo (plus one — see below)

**What it does.** Everything outside the sandbox. Materializes a case, runs the
agent against it, invokes the official validator on a clean copy with the
generated patch, collects `build-result.json`, and writes a `RunRecord` per case
plus a summary table.

**This part carries case materialization**, which is risk #1 in the design
proposal and the longest pole in the project. The packed cases are compressed
source packages and historical logs, not runnable validator cases — the harness
has to reconstruct the source tree, build configuration and dependency snapshot,
then reproduce the original failure. Nothing downstream can be measured until one
case works. **Put two people on this**; the Part 3 owner is a natural pair while
the protocol question is unanswered.

**Three things Part 4 explicitly hands to this part:**

1. **Canonical patch verification.** `apply_edits` warns when a file lacks a
   final newline, because rc.2 can then emit a malformed diff. Its docstring
   assigns the check here: after every run, generate the canonical patch with
   `runner/generate_patch.py` and confirm it applies to a clean copy. Record a
   compatibility failure rather than patching the official runner.
2. **`allowed_prefix` per case layout.** It defaults to `input/`, which matches
   the released RPM hello case. Debian cases will differ, and the hosted worktree
   layout is still unconfirmed with the organizers. The harness sets this.
3. **Rejection visibility.** `apply_edits` returns only a count; every rejection
   reason goes to `logging` at WARNING. Attach a handler that captures those
   records into the `RunRecord`, otherwise failure analysis has nothing to work
   with. No change to Part 4 is needed.

**Ask about Development Validation first.** The organizers named it as the route
for official-environment checks of repaired public cases. If it covers enough,
the reconstruction scope here shrinks substantially — so find out what it can
build, whether it covers all 200 cases, and its rate limits **before**
reconstructing anything by hand.

**The harness also stands in for build feedback.** In-loop feedback is confirmed
for hosted evaluation, but local tests do not simulate in-run build requests. The
one-shot agent does not use it; the full agent will, so this part eventually
serves it locally. Record the mode on every row.

**What it needs.** A Linux host with Docker, and eventually AWS for real builds.
Holds the API credentials — **the agent never does.**

**Done when.** One command runs the agent over a list of cases and produces a
results file with every field in `RunRecord`, including rows for cases that
crashed, timed out, or produced unusable edits.

**Watch for.** Crashes and timeouts are rows in the output, not omissions — §4
requires them in the denominator. Record the mode (one-shot vs iterative) on
every row. Keep this out of the submission ZIP entirely.


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

Resolve in the next team meeting — each one blocks a part:

1. **Local provider and spending ceiling** for development → blocks Part 3.
   The hosted interface is settled (OpenAI-compatible, organizer-managed); this
   is only about what we develop against.
2. **The fixed file list** handed to the model alongside the log tail → blocks
   Part 3. Must be identical for every case.
3. **AWS account, budget, and native ARM vs qemu emulation** → blocks Part 5.
4. **Owners for the remaining parts**, and the second person on case
   materialization.

*Settled:* the edit response format. Part 4 fixed it — literal unique
`old_text`/`new_text` against existing files.


## Questions for the organizers

**Answered** (reply received September 2026):

1. ~~How does a submitted agent reach a model?~~ Organizer-managed,
   OpenAI-compatible endpoint; same undisclosed model for every team; common
   per-case usage budget; no personal credentials in the ZIP.
2. ~~Is there in-loop build feedback?~~ Yes — modify worktree, request a
   target-architecture build, inspect outcome and diagnostic excerpts, continue
   repairing. Uniform per-case request limit, **not yet finalized**.

**Still open — send this week:**

3. **What does Development Validation cover?** Can it build an unrepaired public
   case, does it cover all 200, and what are its rate limits? This directly
   determines how much case reconstruction Part 5 must do.
4. What is the per-case build-request limit, once finalized?
5. Does the validator support execution under qemu emulation, or is native
   target-architecture hardware required?
6. What is the hosted Debian worktree layout, and which paths may an agent edit?
   (Part 4 defaults `allowed_prefix` to `input/`, which matches the RPM hello
   case.)
