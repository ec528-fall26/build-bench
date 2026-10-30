# Full Agent — Next Sprint Build Plan

**Scope:** the next sprint only · **Updated:** 30 October 2026

**Team:** Anthony Capraru, Austin Li, Joonseo Moon, Juliette Jacques, Owen Zhang

This sprint follows the [Demo 2 evaluation](demo2-comparison-diagnostics.md).
It uses the same Parts A–E structure as the [full-agent build plan](full-agent.md)
and narrows the [submission roadmap](full_submission_plan.md) to work we can
integrate and evaluate this sprint.

## What we are building

An integrated agent that receives useful failure evidence, corrects invalid edits
without getting stuck, preserves its final worktree, and can be evaluated on a
small set of reproducible package failures.

```text
1. Establish the failure   Unchanged source reaches a real package failure
2. Read the evidence      Correct package format, failure stage and source context
3. Investigate and edit    Current source, exact replacements, useful rejection feedback
4. Correct or stop        Bounded retries, accurate records, final candidate retained
5. Validate externally    Independent clean build and artifact verdict
```

**Sprint outcome:** one integrated revision, regression checks for the known
failures, and a recorded pilot on the cases with working validation environments.
The repair target is at least one additional autonomous verified repair while
checking that the existing rcran success has not regressed. This is an experiment
target; an applied edit alone does not satisfy it.

## Why — what the evaluation taught us

The recorded 19 October evaluation ran eleven cases per agent. The full agent
repaired rcran; neither agent repaired a development-bundle case.

| Finding | Work this sprint | Part |
| --- | --- | --- |
| Five x86-target cases failed during QEMU/setup before package compilation | Reproduce and isolate environment failures before further repair runs | D |
| One case already built unchanged | Identify this before model calls and award no repair credit | D, E |
| Rust proposed an 18-edit batch, then exhausted 12 correction responses without a candidate build | Replay the failed proposals and improve correction feedback and stopping | B, C, E |
| Fortran and linker cases remained broken after guessed paths or flag changes | Preserve the specific error, relevant command, and producer/consumer context | A, E |
| A later local example run assumed Debian packaging for an RPM case | Detect package format and use it in the prompt | A, E |

The [diagnostics](demo2-comparison-diagnostics.md) distinguish the four genuine
development-package failures from setup failures. Keep that distinction in every
result table.

## Ground rules

- Preserve `one-shot-v1` and the recorded Demo 2 revision. Develop changes in the
  full agent and its supporting harness/tests.
- Confirm the current hosted contract at sprint start. The latest
  [hosted notes](hosted-submission-notes.md) describe no agent-requested in-run
  builds, unlike the earlier organizer email. Until confirmed otherwise, the
  submission configuration must work without intermediate builds.
- Keep local build-feedback experiments separately labeled. Independent final
  validation remains required in either mode.
- Keep inspection read-only and route changes through the edit applier. Preserve
  permitted-path checks and rules against disabling tests or masking failures.
- Keep manual fixes and case-specific answers out of autonomous prompts and the
  submitted agent. Label human-assisted experiments separately.
- Freeze code, model, budgets and validation settings before each measured run.
- Keep all outcomes, including rejected edits, setup failures and unknown usage.

## Shared contracts — agree at the start

Extend the existing interfaces; use fixtures or stubs while another part is in
progress.

| Handoff | Required information | Producer → consumer |
| --- | --- | --- |
| Case and failure context | Package format, target architecture, permitted paths, failure stage, diagnostic and source/log locations; uncertainty where needed | A → B, C, E |
| Edit-check result | Failing edit, rejection reason, match count and bounded current-source context | B → C, E |
| Attempt record | Proposals submitted/rejected/applied/retained, model turns, stop reason and token usage | C → E |
| Runtime configuration | Confirmed model interface, workspace mapping, limits and intermediate-build availability | D → C, E |
| Validation result | Original and final verdicts, failure stage, artifact check and environment identity | D → E |

An accepted edit is still unvalidated. Missing build feedback is not a failed
build. History settings change model context, not whether attempts are recorded.

---

## Part A — Case understanding and diagnostic evidence

**Owner:** Anthony Capraru

**What it does.** Gives the agent the correct package context and enough specific
failure evidence to investigate a cause rather than guess a flag or path.

**Tasks.**

1. Detect Debian/RPM packaging from metadata and files; retain `unknown` when the
   evidence is insufficient. Add the RPM example's Debian-assumption failure as
   a regression fixture.
2. Distinguish setup, dependency installation, configuration, compilation,
   linking, tests and packaging. Preserve the evidence supporting the stage.
3. Retain the useful compiler/linker diagnostic and relevant command rather than
   only the final generic make error. Resolve paths against the actual worktree.
4. Inspect ectrans and one linker case with their logs/source. Identify which
   module, object or library should supply the missing item and what context the
   agent lacked. Turn that lesson into general evidence handling.

**What it needs.** Existing evidence code and saved logs/source. Can start
immediately; live builds are not required for the initial fixtures.

**What it supplies.** Structured case/failure context for C and E; a checked
diagnostic record for each ready pilot case.

**Done when.** Fixtures distinguish RPM from Debian and setup from package
failures; pilot diagnostics have checked source/log locations; the Fortran/linker
examples retain the specific diagnostic and available command context.

**Watch for.** The first reported error is evidence, not proof of the root cause.
Keep log processing bounded and preserve uncertainty.

---

## Part B — Inspection and edit correction

**Owner:** Austin Li

**What it does.** Turns repair proposals into applicable edits and gives the
model actionable feedback when a proposal cannot be applied.

**Tasks.**

1. Replay the saved Rust proposal/correction sequence offline. Classify each
   rejection: wrong path, no match, ambiguous match, stale text, overlapping
   replacements or policy violation.
2. Return the failing edit's index/path, reason, match count and bounded source
   excerpts. Explain when an earlier replacement changes a later edit's target.
3. Ensure inspection returns current file contents after changes; invalidate any
   affected cached observations. Supply repeat-rejection information to C.
4. Test rejected batches, accepted replacements and recovery from partial
   application. Make atomicity or partial-application behavior explicit.

**What it needs.** Saved proposals and source snapshots; existing tools,
preflight checks and edit applier. Can start immediately with local fixtures.

**What it supplies.** Structured edit feedback for C and E, plus replay fixtures
that expose the original correction failures.

**Done when.** Regression checks cover the observed rejection categories; valid
corrected replacements apply to the intended locations; ambiguous replacements
remain rejected; rejected preflight batches leave source unchanged.

**Watch for.** Do not weaken path or policy checks, silently select an ambiguous
match, or count successful application as successful repair.

---

## Part C — Controller, history and budgets

**Owner:** Juliette Jacques

**What it does.** Connects investigation, correction and stopping into a bounded
run that retains a coherent candidate and explains every attempt.

**Tasks.**

1. Consume A's context, B's feedback and E's prompts through the existing
   interfaces. Integrate against stubs early, then replace them with live parts.
2. Detect repeated identical invalid proposals. Request fresh source or stop
   after a bounded correction allowance; avoid spending the entire run repeating
   the same failed replacement.
3. Support the hosted configuration confirmed by D. With no intermediate builds,
   keep accepted candidates labeled unvalidated and define which worktree is
   retained; do not rank them using nonexistent build results.
4. Preserve the retained worktree and write the required result before timeout.
   Count submitted, rejected, applied and retained edits separately; record model
   turns and stop reasons even if no candidate build occurs.
5. Keep history behavior explicit and preserve run records when history is off.

**What it needs.** Shared contracts and fixtures first; A, B, D and E for live
integration. Controller tests can start before those implementations are ready.

**What it supplies.** One runnable integrated agent and complete attempt records.

**Done when.** Scripted checks cover rejection → correction → application,
repeated-invalid stopping, absent build feedback and budget termination. Each
leaves the expected final worktree and accurate result output.

**Watch for.** The submitted agent must not depend on the host harness or Docker.
Only actual validation results can establish a verified repair.

---

## Part D — Validation environments and hosted integration

**Owner:** Joonseo Moon

**What it does.** Establishes usable validation environments and tests the
official model/runtime path early enough to expose submission blockers.

**Tasks.**

1. Verify the current official kit, model-access instructions, workspace mapping,
   limits and build-feedback availability. Record the source/version and give
   C and E one explicit submission configuration.
2. Rebuild the pilot's unchanged originals. Record setup, package failure and
   already-building outcomes separately, with runtime and source identities.
3. Reproduce one x86-target setup failure and investigate the supplied runtime
   and emulation setup. Test a compatible native x86_64 environment if useful
   for isolation. Record environment changes separately; extend to the other
   blocked cases once a working route is demonstrated.
4. Implement/test the injected model configuration and packaging path against
   the current contract. Once C is runnable, package that exact revision and
   attempt Hosted Smoke; retain its identifiers, verdict and redacted diagnostics.
5. Provide an independent final-build path for E's pilot and confirm the expected
   output artifacts, not just the build exit code.

**What it needs.** Official instructions/account access, released case assets and
the existing harness. Environment diagnosis and client fixtures can start now;
the hosted agent run needs C's integrated revision.

**What it supplies.** A readiness manifest, validation verdicts and a recorded
hosted compatibility result or a precise blocking condition.

**Done when.** Every selected case has a baseline/readiness record; the reverse
setup investigation is reproducible; the current runtime contract is recorded;
the integrated package has a recorded Hosted Smoke result or a documented access
blocker with the next action. A failed or blocked attempt remains unqualified.

**Watch for.** A native-host test is an isolation experiment, not a guaranteed
remedy. A local readiness pass does not establish hosted qualification.

---

## Part E — Prompts and pilot evaluation

**Owner:** Owen Zhang

**What it does.** Uses the new evidence and correction feedback in prompts and
measures whether the integrated agent gets past the observed blockers.

**Tasks.**

1. Update prompts to consume package format, failure stage and current-source
   feedback. Remove unconditional Debian assumptions and unsupported build
   requests. Distinguish edit rejection, application and verified repair.
2. Start the pilot with rcran as a known regression case and the four previously
   evaluable development failures: ectrans, libmceliece, libunwind and
   rust-subversion. Reconfirm readiness with D. Add up to five cases only after
   their unchanged builds reproduce a package failure; record selection before
   measured runs and keep cases not used for tuning separately identified.
3. Freeze the revision, prompt/configuration identity, model, case/source hashes,
   budgets, feedback mode and validation environment. Record a minimum of one
   candidate run per ready case, including failures and interrupted runs.
4. Produce the scorecard below. Use saved traces to distinguish failed diagnosis,
   rejected edits, application failures and unsuccessful candidate builds.
5. If capacity permits, run a matched frozen-baseline comparison. Otherwise show
   historical results separately and make no controlled-improvement claim.

**Pilot scorecard.**

| Case / run | Baseline readiness and stage | Submitted / rejected / applied / retained edits | Final verdict and artifacts | Tokens / time | Stop reason |
| --- | --- | --- | --- | --- | --- |
| One row per scheduled run | Preserve raw status plus diagnostic category | Keep proposal and edit counts distinguishable | Verified repair, failed, blocked, already building or unvalidated | Unknown stays unknown | Include failures before an edit/build |

**What it needs.** A/B feedback fixtures for prompt work; C's integrated revision
and D's ready environments for measured runs. Experiment design can start now.

**What it supplies.** Versioned prompts, a reproducible pilot configuration and
a result table linked to run evidence.

**Done when.** Every scheduled pilot run has an explicit outcome; the rcran
regression result is recorded; failures can be traced to a stage in the repair
process; verified repairs and application-only successes are clearly separated.

**Watch for.** Do not tune on manual answers and present the run as autonomous.
A single run is pilot evidence, not a reliable estimate of improvement.

---

## Integration order and sprint checkpoints

| When | Checkpoint | Evidence |
| --- | --- | --- |
| Start | Agree shared contracts, pilot list and official runtime assumptions | A–E handoffs, E's case list, D's current-contract record |
| First half | Develop parts against fixtures and stubs | A diagnostic fixtures; B proposal replay; C controller checks; D baseline/client checks; E prompt fixtures |
| Mid-sprint | Connect A + B + C + D + E and run one ready case end to end | One integrated revision, final worktree and independent verdict; begin the hosted check once packaging works |
| End | Freeze and run the ready pilot cases | Code/config identities, readiness manifest, scorecard, regression results and hosted-check status |

Fix integration blockers before adding more cases. Each part's implementation PR
should state the interface it changes, the checks run and the evidence satisfying
its completion criteria.

## Scope limits for this sprint

New MCP integrations, a controller rewrite, a 200-case campaign, broad parameter
sweeps and a full repeated history comparison are outside this sprint. Preserve
the existing history controls and frozen references so later comparisons remain
possible. This sprint's priority is reliable case execution, corrected edit
handling, useful diagnosis and an integrated pilot with defensible results.
