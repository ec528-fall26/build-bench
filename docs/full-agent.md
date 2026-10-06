# Full Agent — Build Plan

**Status:** planning · **Hard dates:** competition freeze **13 November** (UTC+8),
Demo 3 **16 November**
**Team:** Anthony Capraru, Austin Li, Joonseo Moon, Juliette Jacques, Owen Zhang

The agent we actually compete with, and the one Demo 3 compares against the
frozen baseline ([`one-shot-agent.md`](one-shot-agent.md), tag `one-shot-v1`).
Design source: [`design-proposal.md`](design-proposal.md) §2 and the Demo 3
milestones; lessons from [`part5-findings.md`](part5-findings.md) §9–§9a.

---

## What we are building

A loop that forms a hypothesis about the failure, gathers evidence, tries a fix,
checks it with a real build, and revises using what it learned — within fixed
budgets.

```text
1. Read the failure     Evidence Manager: each error with its file, line and build
                        stage, with the path checked against the real files
2. Investigate          Tools: list / find / search / read files (read-only)
3. Hypothesize + fix    Model: "I think X because Y", plus edits
4. Test                 Build feedback: repack + build; result and new errors
5. Revise or stop       Repair history: what was tried and why it failed, fed into
                        the next attempt. Stop on success, budget, or a repeated
                        failure. On stop, keep the best attempt.
```

Demo 3 must show the **repair-history** mechanism helps, by running the agent with
it on and off ("ablation") against the frozen baseline (proposal, Demo 3 rows).

## Why — what the baseline taught us

From the official `one-shot-v1` runs on rcran (1 of 3 repaired; 4 of 6 pooled):

| Baseline failure | Full-agent answer | Part |
| --- | --- | --- |
| Copied `crc32c/crc32c_prefetch.h` from the log; the file is at `src/crc32c/…` | Resolve log paths against the real tree before using them | A |
| Declined a feasible repair, believing only the two files it was shown existed | Let the model list, find, search and read files itself; say plainly that any existing file under `input/` may be edited | B, E |
| Never saw its own fix fail | Build, read the new error, try again | C, D |
| A different fix every run, quality varying | Record each hypothesis, edit and outcome; prefer direct source edits (they become Debian patches automatically) over build-time `sed` | C, E |

## Relation to the Demo 1 architecture

This plan implements the architecture in the design proposal (§2), revised with
what the baseline taught us. The proposal's loop — Observe, Diagnose and inspect,
Repair, Validate, Revise or stop — is the five steps above under plainer names.

| Proposal component | Here |
| --- | --- |
| Case Manager | Part A (case metadata) |
| Evidence Manager | Part A |
| Controlled Tools | Part B |
| LLM Planner | Part C (loop) and Part E (prompts) |
| Repair Generator | Part 4's edit applier, copied into the new folder |
| Validation/Feedback Adapter | Part D |
| History/Metrics | Part C (history) and the harness run record |
| Budget Controls | `Budget` contract, enforced by Part C |

**Revisions since Demo 1**, to state at Demo 2:

- **Token budget 30,000 → 100,000 (draft).** The proposal's caps are "proposed
  research caps" to be calibrated before freezing; the baseline's single call
  already used ~12,300 tokens on rcran.
- **Additions from evidence:** checking log paths against real files
  (`v1-rcran-2`); the prompt lessons (`v1-rcran-3`); keeping the best attempt
  (organizers); working from build-log excerpts and without build feedback
  (organizers); a JSON tool-request format.

## Organizer guidance

From the organizers' reply to our Demo 1 questions (proposal reference [4]):

| Guidance | How this plan follows it |
| --- | --- |
| One undisclosed model for all teams, via an organizer-managed OpenAI-compatible endpoint | Same request format as now. Prompts stay **model-agnostic**: nothing tuned to `gpt-oss` quirks (Part E) |
| No personal keys or credentials in the ZIP | Keys only in environment variables, as in the baseline |
| A common per-case usage budget, announced later | `Budget` values are **research settings**; replaced by the organizers' numbers once published |
| In-run builds return the outcome and **diagnostic excerpts**, within a bounded, uniform number of requests | Part A works on excerpts as well as full logs; Part D can run locally in an **excerpt mode**, so the agent is not tuned to information it won't have; `max_builds` follows the organizers' limit |
| The platform verifies the repair from the final worktree | The final worktree is the best attempt, with nothing else in it: Part D repacks a *copy* |
| Keep the host-side file bridge in the local research harness, not the agent contract | The local build callback lives in `harness/` and is **never shipped in the agent ZIP**; the agent only sees the `BuildFeedback` interface |
| Identical decisions across runs are not required; the entrypoint must run non-interactively | Variance is measured (3 runs per configuration), not engineered away. The agent never waits for input |
| Restoring the original worktree at a timeout could discard a good repair; keep the best candidate | `reserve_seconds` and the best-attempt rule (Part C) |
| Development Validation checks repaired public cases in the official environment | Spot-check local verdicts against it (Part E) |
| Evaluate the two directions in the public release; no RISC-V or hidden-case claims | Demo 3 results are reported as "N of 10 development cases", never as a hidden-case or leaderboard estimate |

## Ground rules

- **New folder: `agents/full-agent/`.** It starts as a copy of `agents/one-shot/`.
  Parts 2 and 4 are *copied*, never imported from the baseline: the baseline is
  frozen and CI fails if it changes.
- **Same model and endpoint as the baseline** (`openai.gpt-oss-120b-1:0`, Bedrock's
  OpenAI-compatible endpoint). A comparison across models means nothing.
- **Every capability behind a configuration flag**, so Demo 3 can show what each
  one contributes. With every flag off, the agent should behave like the baseline.
- **Every attempt is recorded** in the harness run record — hypothesis, edits,
  build outcome, tokens — including attempts that never reach a build.
- **The agent reports its own token use.** `agent-result.json` includes total and
  per-attempt input/output tokens (`null` when unknown, never 0), plus the number
  of attempts and builds. The baseline omits tokens there — adding them would have
  changed the frozen `one-shot-v1` — so the harness's record is the only source
  for the baseline; the full agent should not depend on that.
- **Standard library only**, as now; keys only in environment variables.
- **Non-interactive.** The agent never waits for input; every failure becomes a
  recorded outcome, as in the baseline.

---

## Shared contracts

Agree on these **first**, so the five parts can be built in parallel against stubs
(the same approach that worked for the baseline). Draft — expect changes in the
first meeting.

```python
# agents/full-agent/src/contracts.py

@dataclass
class Diagnostic:                 # Part A produces, everyone reads
    message: str                  # "fatal error: xmmintrin.h: No such file or directory"
    stage: str                    # configure | build | test | install | packaging | unknown
    log_path: str | None          # path as the log printed it: "crc32c/crc32c_prefetch.h"
    line: int | None
    resolved: list[str]           # matching real files: ["input/src/crc32c/crc32c_prefetch.h"]
    source_line: str | None       # the line the compiler quoted, if any
    log_offset: int               # where in the log it came from (provenance)

@dataclass
class ToolRequest:                # model -> Part B
    tool: str                     # list_dir | find_files | search | read_file
    args: dict

@dataclass
class ToolResult:                 # Part B -> model
    request: ToolRequest
    text: str                     # labelled, size-capped, never raises
    truncated: bool

@dataclass
class BuildResult:                # Part D -> Part C
    succeeded: bool
    stage_reached: str            # furthest build stage, used to rank attempts
    diagnostics: list[Diagnostic] # Part A applied to the new build log
    duration_seconds: float

@dataclass
class Attempt:                    # Part C keeps these: the repair history
    number: int
    hypothesis: str               # falsifiable: "the x86 header is included unconditionally"
    edits: list[Edit]             # Part 4's Edit, unchanged
    applied: int
    build: BuildResult | None     # None if no build was run
    tokens: dict                  # {"input_tokens": int | None, "output_tokens": int | None}

@dataclass
class Budget:                     # research settings, calibrated on rcran (see below)
    max_attempts: int = 5
    max_builds: int = 3
    max_tokens: int = 100_000
    max_seconds: int = 1_800
    reserve_seconds: int = 120    # kept back for a graceful stop

@dataclass
class AgentConfig:                # the ablation switches
    use_evidence_manager: bool = True
    use_tools: bool = True
    use_build_feedback: bool = True
    use_history: bool = True

class BuildFeedback(Protocol):    # Part D implements; Part C calls
    def request_build(self, worktree: Path) -> BuildResult | None: ...  # None = unavailable
```

---

## Part A — Case and Evidence Manager

**Owner:** _______

**Case.** Normalise what the case says about itself — package, target
architecture, paths — from `task.json` (and the case manifest locally), without
altering the original evidence (the proposal's Case Manager).

**What it does.** `diagnose(log_path, worktree) -> list[Diagnostic]`. Streams the
build log and extracts each error with its file, line, message and build stage,
deduplicated, earliest first (the first error is usually the cause). Recognises
at least: GCC/Clang `file:line:col: (fatal )error:` with the quoted source line;
linker `undefined reference`; `make[N]: *** … Error`; and the Debian stage markers
(`dh_auto_configure`, `dh_auto_build`, `dh_auto_test`, `dh_auto_install`).

**Path resolution — the fix for `v1-rcran-2`.** Compilers print paths relative to
their working directory. Match the printed path as a *suffix* against files under
`input/`; keep every match in `resolved`, and none if the file doesn't exist.

**What it needs.** Nothing external. **Can start today**: the materials archive
holds all **200 dataset logs** and their unpacked source trees.

**Done when.** rcran yields `crc32c/crc32c_prefetch.h:18` resolved to
`input/src/crc32c/crc32c_prefetch.h` with source line `#include <xmmintrin.h>`;
a report states on how many of the 200 logs at least one diagnostic is found, and
a sample is checked by hand.

**Excerpts, not just full logs.** In the competition, in-run builds return
"relevant diagnostic excerpts", not the whole log. `diagnose` must work on a
fragment, and must not assume the log starts at the beginning of the build.

**Watch for.** One dataset log is 2.37 GB — stream it, never read it whole.
Repeated errors (the same message for every file) must collapse to one.

---

## Part B — Tools

**Owner:** _______

**What it does.** Read-only tools the model can request, all limited to `input/`:
`list_dir(path)`, `find_files(name_glob)`, `search(regex, path_glob)` (capped
matches), `read_file(path, start_line, end_line)` (capped size), plus two the
proposal names: `current_diff()` — what the agent has changed so far, which the
history needs anyway — and `patch_series()` — the package's existing
`debian/patches/` and their order. Results use the
baseline's convention: labels outside file text, `unavailable (reason)` instead of
errors.

**How the model asks — decide in week 1.** Native tool calling may not behave the
same on Bedrock and on the organizers' unknown endpoint. The proposal is a plain
JSON request in the reply (`{"tool": "read_file", "args": {...}}`), parsed with the
same last-JSON-object rule that fixed Part 3. One quick test on Bedrock settles it.

**What it needs.** Nothing external. Reuse the baseline's symlink and escape
checks (`model_client._read_source`), copied into the new folder.

**Done when.** Tests cover symlinks, `..` escapes, size caps, non-UTF-8 files and
the tool-call budget.

**Watch for.** No tool may write. Edits only ever go through Part 4.

---

## Part C — Loop and repair history

**Owner:** _______

**What it does.** The state machine above. Each turn sends the model the evidence,
any tool results, and — with `use_history` on — every earlier `Attempt`. Applies
edits through Part 4, asks Part D for a build, records the `Attempt`, and decides:
continue, revise or stop.

**Stop rules.** Build succeeded; any budget exhausted; the same leading diagnostic
twice in a row after a change; or the model proposing no edits twice.

**Keeping the best attempt.** The organizers advised a graceful stop that keeps the
best candidate rather than reverting. Rank attempts by: succeeded > furthest
`stage_reached` > fewest diagnostics. This ranking is a **heuristic**: the
proposal warns that "build-stage movement and repeated diagnostics are clues, not
proof that an edit helped or harmed." It only chooses what to keep when time runs
out; it never counts as a repair. Attempts are cumulative on the best state so
far; an attempt that does worse is undone by applying its edits in reverse (Part
4's edits are literal replacements, so the reverse is exact). Never undo the best
attempt.

**What it needs.** The contracts; stubs for A, B and D at first.

**Done when.** On rcran with the real model: a failed attempt is followed by a
revised one using the new error, and the run record lists every attempt. With
`use_history` off, each attempt sees only the current log.

**Watch for.** Time: reserve `reserve_seconds` so the agent can stop cleanly
before any outside limit. Tokens: history grows each turn — summarise older
attempts rather than resending everything.

---

## Part D — Build feedback

**Owner:** _______ (suggested: the harness owner)

**What it does.** Implements `BuildFeedback.request_build`.

- **Locally:** the harness — never the agent ZIP — hands the agent a callback that copies the current
  worktree, repacks it with `dpkg-source --auto-commit -b` (on the *copy*, so the
  agent's own tree and diff stay clean), builds with `run.sh`, and returns a
  `BuildResult` with Part A applied to the new log. About 90 s per build on rcran.
  An **excerpt mode** passes only the diagnostic excerpts instead of the full log,
  mirroring what the competition returns.
- **In the competition:** the organizers confirmed in-run builds that return the
  outcome and diagnostic excerpts, within a per-case request limit that is the same
  for every team but not yet published; the protocol is also unpublished. Until then the
  hosted implementation returns `None`, and the loop must still work: investigate
  and revise, but submit without testing.

**What it needs.** The ARM64 instance and the rcran bundle — both working today.

**Done when.** On rcran, a deliberately wrong edit returns `succeeded: false` with
the new error parsed, and the known fix returns `succeeded: true`. Each build is
recorded in the run record.

**Watch for.** `patch_applied` stays `false` on success — use `status` and
`artifact_validation_passed` (`harness/record.py` already does).

---

## Part E — Prompts and evaluation

**Owner:** _______

**What it does.**

- **Prompts.** Built on the baseline's, plus today's lessons: any existing file
  under `input/` may be edited, including source files named in the log; prefer
  direct source edits (the harness turns them into Debian patches) over build-time
  commands; every reply states a hypothesis and the evidence for it. Prompts stay
  **model-agnostic**: the competition model is undisclosed, so nothing may depend
  on `gpt-oss` quirks; reply parsing stays tolerant, as in Part 3.
- **Configuration flags and experiments.** A script in `experiments/` that runs
  each configuration on every available case, 3 runs each, and summarises per
  case and configuration, using `harness.summarize` and the fix-type flags.
- **Ground truth.** Spot-check a sample of local verdicts with Development
  Validation, the organizers' official-environment check for repaired public cases.
- **Failure taxonomy.** Categories with concrete traces, starting from §9a's two
  (path copied from the log; declining a feasible repair).

**Done when.** One command produces the Demo 3 comparison table below.

---

## Demo 3 comparison

Same 10 frozen cases, same model and budgets, **3 runs per configuration**
(proposal, Demo 3 rows):

| Configuration | What it shows |
| --- | --- |
| `one-shot-v1` (frozen) | the naive baseline |
| full agent, history **on** | the improvement claimed |
| full agent, history **off** | how much of the gain the history causes |

Primary metric: verified repairs. Target: at least one more verified repair per 10
cases on average than the baseline. Secondary (proposal): tokens, median wall
time, attempts per case. Report fix types alongside, and regressions as well as
gains. Results describe the 10 public development cases only — no RISC-V, hidden-case
or leaderboard claims.

---

## Budgets — research settings, calibrated before freezing a comparison

The proposal's provisional caps (5 candidates, ≤3 builds, 30 min, **30,000
tokens**) predate any measurement. The baseline's single call already used ~12,300
tokens on rcran, so a multi-step agent needs more: the draft above uses 100,000.
At ~$0.15 / $0.60 per million input/output tokens, a case that used the whole
100,000 would cost between about 1.5¢ (all input) and 6¢ (all output).
Measure on rcran, then fix the numbers; use the organizers' per-case budget once
published.

## Timeline

| Date | Target |
| --- | --- |
| **Oct 10** | Contracts agreed. A tested on the 200 logs. B tested. D returns real build results on rcran. Tool-request format decided. |
| **Oct 17** | C's loop running end to end on rcran with the real model. |
| **Oct 24** | History, best-attempt tracking and all flags working; first full-agent runs recorded. |
| When environments arrive | All configurations on the 10 cases; failure taxonomy. |
| **Nov 6** | Competition dry run: `./bb check`, `./bb package`, Hosted Smoke Test. |
| **Nov 13** | **Competition freeze** — select the qualified version (UTC+8). |
| **Nov 16** | Demo 3. |

## Open questions

**For the team (week 1):**

1. Owners for A–E.
2. Tool-request format: JSON in the reply, or native tool calling (one Bedrock test).
3. Budgets, after measuring on rcran.

**For the organizers / mentor:**

1. In-run build feedback: protocol, availability date, per-case request limit.
2. The per-case token and time budget, and the endpoint's configuration names.
3. Packaging: `./bb ready` requires repairing hello with networking off, which no
   model-based agent can do. Is `./bb check` + `./bb package` the intended route?
4. Status of the 10 case environments (the mentor has agreed to provide them).
