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
```

**No `RunRecord` type.** It was planned here but removed on 1 October: a run's
outcome (did the build succeed?) only exists after the agent exits, so the agent
cannot write a complete record of its own run. The harness writes the run record
instead — see [`harness/README.md`](../harness/README.md).

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

## Part 3 — Model adapter — **DONE**

**Owner:** Austin · `agents/one-shot/src/model_client.py`, tests in
`part3-model-client/tests/`

**What it does.** `ModelClient.propose(context) -> EditPlan`: builds the prompt,
makes **exactly one** chat-completion call, and turns the reply into `Edit`s.
It never raises; every failure becomes an empty plan whose `rationale` says why.

**Endpoint.** Amazon Bedrock's **OpenAI-compatible** Chat Completions endpoint,
billed to the team's AWS credits. The competition serves its model through an
organizer-managed endpoint in the same format, so switching is configuration:

| Setting | Where | Value |
| --- | --- | --- |
| Model | `MODEL_ID` in code | `openai.gpt-oss-120b-1:0` |
| Endpoint | `BB_MODEL_BASE_URL` (optional) | default `https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1` |
| Key | `BB_MODEL_API_KEY` | a Bedrock API key; **environment only** |

**One model, fixed in code.** The baseline and the full agent must use the same
model, or their comparison means nothing, so `MODEL_ID` is a constant rather
than a flag. When the competition model is announced, changing it means re-running
*both* agents on it; never mix results across models.

**Keys never go in the repo or the ZIP.** `./bb check` scans for private keys,
AWS access key IDs and `sk-…` keys, but **not Bedrock API keys**, so it would not
catch one pasted into the code.

**The prompt.** A system message with the rules Part 4 enforces, then a user
message with the case id, target architecture, the last 500 log lines, and a
**fixed** list of files — the same for every case, chosen without looking at the
log, so the baseline stays naive:

`input/debian/{rules,control,patches/series}`, then
`input/{CMakeLists.txt,configure.ac,Makefile,setup.py,Cargo.toml,meson.build}`.

Files appear as plain text (real tabs, not escaped JSON) under `=== path ===`
labels kept outside the file text. Each is capped at 8 KB, cut at a line boundary
and labelled as truncated; 32 KB in total. Missing, non-UTF-8 or symlinked files
are shown as `unavailable (reason)`.

**Reading the reply.** `gpt-oss` is a reasoning model, so replies may contain
thinking and draft JSON before the answer. The client strips any `<reasoning>`
block and takes the **last** JSON object with an `"edits"` key, ignoring
surrounding prose. A reply cut off at the token limit (8,192, reasoning included)
is reported as such.

**Reliability.** Throttling (429), server errors (5xx), timeouts and dropped
connections are retried up to 3 attempts — the endpoint failing is not the model
failing. Client errors (4xx) are not retried, and their message is kept, with the
key redacted. Token usage is recorded; unknown is `None`, never 0.

**No pre-flight check.** Edits go to Part 4 unfiltered. Part 4 judges each one
separately and the harness records its rejection reasons, so one bad edit can no
longer discard a good one.

**Replays live in the harness.** Offline answers for testing the pipeline are the
harness's `ReplayModel` and `harness/replays/`; the agent only talks to the model.

**First live result (3 October): rcran repaired.** I had predicted a failure,
because the config-header fix is in a file not on the fixed list. Instead the model
used the log, which prints the failing path and line
(`crc32c/crc32c_prefetch.h:18:10 … #include <xmmintrin.h>`), and added an
`override_dh_auto_configure` to `debian/rules` that keeps `dh_auto_configure` and
`sed`-guards that include for x86 only. Legitimate (no test or architecture
bypass) but not idiomatic: a maintainer would add a `debian/patches/` patch.
Repaired again in two repeat runs, each with a different and less careful fix;
see `part5-findings.md` §9.

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
generated patch, collects `build-result.json`, and writes one run record per case
plus a summary table.

**Do not build a case-materialization pipeline.** The organizers state plainly:
local validation needs the matching target buildconfig and frozen dependencies,
and *"do not generate these from the public dataset metadata."* [rcran-v1 README]
The 200 public cases ship source and failure logs only.

**Develop against `buildbench-local-rcran-v1` instead.** It is one complete local
environment for case `launchpad-mantic-amd64-arm64-r-cran-digest-7a42effc961f`
(x86_64 → aarch64), containing `case/manifest.json` (validator schema 1.0), the
original `.dsc` and archives under `case/input/`, the official buildconfig and
`dependency-lock.json` under `case/config/`, frozen `.deb` files under
`case/dependencies/`, an ARM64 runtime image, `tools/build-case-docker`, and
`run.sh`. It is 388 MB for a single case — which is why all 200 are not shipped
this way.

```bash
sha256sum -c SHA256SUMS
docker load -i runtime-image.tar.gz
bash run.sh --check                        # manifest + dependency hashes only
bash run.sh --output "$PWD/results/baseline-1"
```

For a second local case, **contact the organizers with the Case ID.** Otherwise
the only route for the other 199 is website Development Validation, which is a
web form running one task at a time.

**Host requirements are stricter than assumed.** Native **Linux ARM64** with
Docker Engine, ≥8 GiB RAM, ≥10 GiB free disk, and a disposable or dedicated
machine — validation runs a **privileged container**. An x86_64 host needs a
`qemu-aarch64` binfmt handler with the `F` flag *even for `--check`*, and the
organizers have not tested that combination end to end.

**The harness must repackage the repair.** This is the biggest new requirement.
`run.sh --input` expects `input/` to hold a **complete rebuilt Debian source
package** — the correctly named `.dsc` plus every referenced archive with updated
checksums, produced by `dpkg-source -b`, preserving the package name and version.
Editing a tarball without rebuilding the `.dsc` is invalid, and a bare `debian/`
tree where a `.dsc` is expected is rejected. So the local loop is:

1. from inside `work/`, `dpkg-source -x ../case/input/*.dsc input` — so agent
   paths read `input/src/...`, matching the `input/**` policy
2. let the agent edit `work/input/` (Part 4 applies literal edits under `input/`)
3. **`dpkg-source --auto-commit -b .`** from inside `work/input/` to rebuild the
   source package. Plain `-b` aborts on any upstream edit; `--commit` is
   interactive and hangs a script. Verified on rcran — see
   [`part5-findings.md`](part5-findings.md) §8
4. copy `case/` to `repaired-case/`, keeping `manifest.json`, `config/` and
   `dependencies/` unchanged, and replace only `repaired-case/input/`
5. `bash run.sh --input "$PWD/repaired-case" --output results/repair-N`

Success is `status == "succeeded"` with `artifact_validation_passed == true`. Ignore
`patch_applied` — it stays `false` because the repair travels inside the rebuilt
source package, not through the validator's own patch mechanism.

Step 3 is work the hosted platform does for us ("the platform supplies Case
metadata and dependencies, and packages unpacked source before the target
build"), so it exists only in our local harness.

**Results to record:** `build-result.json`, `build-diagnostics.json`, `build.log`
and `artifacts/`. Exit 0 means the build succeeded; an unrepaired case is expected
to fail, and a source compilation failure is a legitimate case result.

**Three things Part 4 explicitly hands to this part:**

1. **Canonical patch verification.** `apply_edits` warns when a file lacks a
   final newline, because rc.2 can then emit a malformed diff. Its docstring
   assigns the check here: after every run, generate the canonical patch with
   `runner/generate_patch.py` and confirm it applies to a clean copy. Record a
   compatibility failure rather than patching the official runner.
2. **`allowed_prefix` and `patch_policy`.** Confirmed by the rcran manifest:
   `allowed_paths: ["input/**"]`, `forbidden_paths: ["manifest.json", "config/**",
   "dependencies/**"]`. Part 4's `input/` default is correct, and its
   `patch_policy` argument takes these values straight from the case manifest.
3. **Rejection visibility.** `apply_edits` returns only a count; every rejection
   reason goes to `logging` at WARNING. Attach a handler that captures those
   records into the run record, otherwise failure analysis has nothing to work
   with. No change to Part 4 is needed.

**Development Validation is the only route for the other 199 cases.** It accepts
a repaired bundle — a root upload manifest (**schema_version 0.1**, with
`suite_id` and `cases`, *not* the validator's 1.0 case manifest) plus repaired
source under `cases/<case-id>/worktree/input/`, as either an unpacked tree with a
complete `debian/` directory or a rebuilt `.dsc` with all archives and updated
checksums. Do not mix the two formats, and do not upload the rcran archive or its
dependencies. The platform supplies case metadata and dependencies itself.

It will not run an unrepaired case, it is a web form with no API, and a team may
run **one task at a time** — so it cannot carry a repeated-run evaluation. Treat
it as a ground-truth spot check.

**The harness also stands in for build feedback.** In-loop feedback is confirmed
for hosted evaluation, but local tests do not simulate in-run build requests. The
one-shot agent does not use it; the full agent will, so this part eventually
serves it locally. Record the mode on every row.

**What it needs.** A Linux host with Docker, and eventually AWS for real builds.
Holds the API credentials — **the agent never does.**

**Done when.** One command runs the agent over a list of cases and produces a
results file with a complete run record per attempt, including rows for cases that
crashed, timed out, or produced unusable edits.

**Watch for.** Crashes and timeouts are rows in the output, not omissions — §4
requires them in the denominator. Record the mode (one-shot vs iterative) on
every row. Keep this out of the submission ZIP entirely.


## Integration order

| Step | Needs | Produces | Status |
| --- | --- | --- | --- |
| 1 | Part 1 alone | Agent shell | ✅ |
| 2 | + Part 2 | Agent that reads the log | ✅ |
| 3 | + Part 4 | Agent that applies edits safely | ✅ |
| 4 | + harness replays | Full pipeline with known answers, no spend | ✅ rcran repaired; negative control rejected |
| 5 | + Part 3 (live) | Real one-shot agent; `main()` wired | ✅ rcran repaired in 3 of 3 live runs, with three different fixes |
| 6 | + more cases | Measured results on real cases | ⏳ needs organizer case environments |

Steps 1–4 need no model access and no AWS. **If the protocol question stays
unanswered, we can still reach step 4.**

## Checkpoints

| Date | Target |
| --- | --- |
| 10/07 | Steps 1–3 done. One Debian case materialized and reproducing its failure. Organizer protocol question answered or documented as unanswered. |
| 10/14 | Step 4 done — full pipeline running offline against recorded responses. |
| 10/21 | Step 6 on the 10 frozen cases. Demo 2. |
| — | **Host:** native Linux ARM64 (Graviton), Docker, ≥8 GiB RAM, ≥10 GiB disk, disposable. Not x86_64. |
| 11/13 | **External:** a qualified version selected in My Submissions by end of day Beijing time (UTC+8). Team registration closed 30 September and ours is done. Qualification needs an upload that passes platform checks and the Hosted Smoke Test, which shares the Agent Runner, workspace layout and status schema with Full Evaluation. |

## Open decisions

Resolve in the next team meeting — each one blocks a part:

1. ~~**Local provider**~~ *Settled:* Bedrock's OpenAI-compatible endpoint on the
   team's AWS credits, model `openai.gpt-oss-120b-1:0` (see Part 3).
2. ~~**The fixed file list**~~ *Settled:* see Part 3. Still open: a byte cap on the
   500-line log tail (largest is ~106K tokens; fits the 128K context, but it
   changes the baseline definition, so it is a team decision).
3. ~~**Build-time commands**~~ *Settled:* allowed (no agent change). The harness
   flags added file-modifying build commands (`fix_type: build_commands`,
   `needs_review`), results are reported split by fix type, and only flagged runs
   get a manual diff review. Rejecting them in Part 4 was considered and declined:
   it would have turned all three rcran repairs into failures and also blocks
   legitimate packaging.
4. **AWS budget** → Part 5. *Settled:* the host must be native Linux **ARM64**
   (Graviton), because the rcran runtime image is `linux/arm64` and the x86_64 +
   QEMU path is untested by the organizers.
5. **Owners for the remaining parts**, and the second person on case
   materialization.

*Settled:* the edit response format. Part 4 fixed it — literal unique
`old_text`/`new_text` against existing files.


## Questions for the organizers

**Answered** (organizer reply, September 2026, plus the competition website
inspected 30 September 2026):

1. ~~How does a submitted agent reach a model?~~ Organizer-managed,
   OpenAI-compatible endpoint; same undisclosed model for every team; common
   per-case usage budget; no personal credentials in the ZIP.
2. ~~Is there in-loop build feedback?~~ Yes — modify worktree, request a
   target-architecture build, inspect outcome and diagnostic excerpts, continue
   repairing. Uniform per-case request limit, **not yet finalized**.

**Still open — send this week:**

3. ~~What does Development Validation cover?~~ Answered from the website:
   repaired worktree bundles only, no unrepaired reproduction, web form, one task
   per team at a time. It cannot carry an evaluation.
4. ~~Which paths may an agent edit?~~ Development Validation expects
   `worktree/input/` per case, which matches Part 4's `allowed_prefix="input/"`
   default. Confirm it still holds for hosted Debian cases.

**Still open — send this week:**

5. What is the per-case build-request limit, once finalized? The rules say
   build-feedback, iteration and tool-call budgets will be published before
   public evaluation opens.
6. Does the validator support execution under qemu emulation, or is native
   target-architecture hardware required?
7. Is there guidance for reconstructing a public case's build environment
   locally — particularly the historical dependency snapshot, which the released
   dataset omits?
