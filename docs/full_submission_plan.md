# Full Agent — Submission and Demo 3 Build Plan

**Status:** proposed plan following Demo 2  
**Target dates:** competition selection November 13; Demo 3 November 16. Confirm the exact submission deadline and timezone in the current portal.  
**Ownership:** unassigned; parts define work boundaries rather than individual assignments.

## What we are building

A submission-ready repair agent that investigates and edits a package **without requiring intermediate builds**, followed by independent validation of its final worktree.

```text
1. Read the case       Identify package format, architecture and failure evidence
2. Investigate         Inspect relevant files through controlled tools
3. Propose and check   Produce edits; check paths, matching text and policy
4. Correct or revise   Use inspection results and edit feedback within budgets
5. Finish              Preserve the final candidate and write the required result
6. Validate externally Build and verify the final candidate outside the agent
```

The local research harness remains available for separate build-feedback experiments. It is not required by the submitted agent.

### Starting evidence

- The full agent produced a verified rcran repair.
- The October 19 evaluation recorded eleven cases per agent.
- Four evaluable bundle cases remained unrepaired; five were setup-blocked; one already built unchanged.
- Both agents passed the local `hello` readiness example on October 21 using a modified runner with credential forwarding and networking.
- A teammate’s subsequent run exposed an incorrect Debian assumption on the RPM-based `hello` case.
- Hosted qualification has not been established by those local passes.

## Ground rules

- Preserve `one-shot-v1` and the Demo 2 full-agent reference.
- Develop submission changes separately from the frozen references.
- Disable intermediate build feedback in the submission configuration; do not remove external final validation.
- Never describe an applied but unbuilt edit as a verified repair.
- Use the current official model-access and workspace contract.
- Keep case-specific answers, credentials and experiment outputs out of the submission.
- Record unsuccessful runs, rejected proposals and infrastructure failures.
- Freeze code and settings before each comparison.
- Distinguish processing 200 logs, running an agent on 200 cases, and validating 200 repairs.

## Shared contracts — agree before integration

Extend the existing contracts rather than replacing every interface.

| Contract | Required information | Producer → consumer |
| --- | --- | --- |
| Case context | Case ID, permitted paths, architecture metadata, package format and evidence supporting it | A → B, C, E |
| Failure context | Diagnostic, relevant source/log context, failure stage and uncertainty | A → C, E |
| Edit-check result | Accepted/rejected status, rejection reason, match count and relevant source context | B → C, E |
| Attempt record | Proposal, edits submitted/applied/retained, rejection reason, token usage, validation state | C → E |
| Model connection | Endpoint, model identifier, credential source and request limits | D → C |
| Run result | Code/configuration identity, completion reason, usage, final worktree reference and external verdict | C, D → E |

**Important distinctions**

- Package format can be `unknown`; detection must not invent certainty.
- Missing build feedback means `unvalidated`, not `failed` or `succeeded`.
- History on/off controls what the model receives, not whether records are saved.
- Final build results are attached by the evaluator after the agent finishes.
- Baseline and candidate revisions must remain identifiable in every result.

**Done when:** each part can develop against small fixtures or stubs using these interfaces.

---

## Part A — Case understanding and diagnostic evidence

### What it does

Gives the agent accurate package context and useful failure evidence before it chooses a repair.

### Tasks

**A1. Identify package format**

- Prefer explicit case metadata when available.
- Inspect packaging files when metadata is insufficient.
- Recognize RPM specification files and Debian packaging structures.
- Preserve uncertainty when files are missing or conflicting.
- Add the `hello` Debian-assumption failure as a regression example.

**A2. Classify the failure stage**

- Use structured validator information where supplied.
- Otherwise extract supported stage information from the log.
- Distinguish environment setup, dependency installation, compilation, linking and packaging.
- Retain the evidence supporting the classification.
- Do not infer that every setup failure is caused by QEMU.

**A3. Improve diagnostic context**

- Preserve relevant compiler commands and nearby error details.
- Resolve paths against the real worktree.
- Include useful context for Fortran-module and linker failures.
- Keep log processing bounded and capable of handling large files.

**A4. Inventory the public cases**

- Process the 200 cases into a manifest.
- Record package format, architecture metadata, evidence availability and classification.
- Mark uncertain cases for manual inspection.

### What it needs

- Shared contracts.
- Public case metadata, logs and source trees.
- Existing evidence-manager implementation.

**Can start immediately:** A1–A4 do not require hosted model access or functioning build environments.

### What it supplies

- Package and failure context for Part C.
- Structured evidence for Part E’s prompts.
- Case categories for Part D’s validation inventory and Part E’s evaluation sampling.

### Done when

- RPM `hello` is not incorrectly presented as requiring Debian files.
- Known setup failures are distinguished from source-compilation failures.
- Representative diagnostics are checked against their logs and source.
- All 200 cases have an inventory entry, including unknowns.

### Watch for

Finding a diagnostic is not proof that it is the root cause. Stage classification should guide investigation without preventing legitimate packaging or dependency repairs.

---

## Part B — Inspection and edit correction

### What it does

Helps the agent obtain current source information and turn a repair proposal into an applicable edit batch.

### Tasks

**B1. Audit existing tools**

- Confirm path restrictions and size limits.
- Check behavior across RPM and Debian package layouts.
- Ensure missing files and unsupported content produce useful results.
- Verify that inspection tools do not modify source files.

**B2. Investigate the Rust correction failure**

- Read each rejected proposal and its correction feedback.
- Categorize the actual rejection reasons.
- Identify whether the model lacked context, reused stale text or ignored the feedback.
- Preserve a small reproducible fixture for each observed problem.

**B3. Improve preflight feedback**

- Return precise missing-match and ambiguous-match information.
- Include bounded excerpts around relevant matches.
- Identify the failing edit within a batch.
- Explain when an earlier edit changes the text expected by a later edit.

**B4. Refresh stale evidence**

- Invalidate cached file observations after affected files change.
- Provide a way to retrieve fresh source before another correction.
- Supply repeated-proposal information to Part C.

**B5. Test edit behavior**

- Check that rejected preflight batches leave the worktree unchanged.
- Explicitly test and document any partial-application behavior.
- Verify recovery or restoration where atomic batch behavior is intended.

### What it needs

- Shared contracts.
- Existing tools, preflight checks and edit applier.
- Archived invalid-edit traces.

**Can start immediately:** tools and correction tests can use saved source fixtures.

### What it supplies

- Inspection results and structured rejection feedback for Part C.
- Correction details for Part E’s prompts.
- Repeated-invalid-proposal signals for the stopping policy.

### Done when

- Known rejection cases produce actionable feedback.
- Accepted edits change only the intended files.
- Records explain why a proposal was rejected.
- Tests distinguish successful application from successful repair.

### Watch for

Preflight proves applicability and policy compliance, not that the change repairs the build.

---

## Part C — Submission-mode controller, history and budgets

### What it does

Runs the full agent without depending on intermediate build feedback, while retaining bounded investigation and edit correction.

### Tasks

**C1. Establish a submission configuration**

- Disable the intermediate build callback.
- Ensure the entrypoint does not require the research harness.
- Retain inspection, edit correction, history and multiple model turns.
- Keep the build-enabled research configuration separate.

**C2. Define candidate handling without builds**

- State that applied candidates remain unvalidated.
- Do not use absent build scores to claim a “best” candidate.
- Specify whether the normal policy retains the latest accepted worktree.
- Define rollback behavior for invalid or partially applied batches.
- Ensure later revisions operate on the actual retained files.

**C3. Define stopping rules**

- Model stop.
- Turn, token, tool and time limits.
- Correction budget.
- Bounded handling of repeated identical invalid proposals.
- Graceful completion before the external timeout.

Repeated-build-diagnostic stopping applies only when build feedback exists; it is not the submission-mode stopping mechanism.

**C4. Correct attempt accounting**

- Count proposals submitted, rejected, applied and retained separately.
- Record model turns independently from candidate revisions.
- Record unknown token usage as unknown.
- Preserve all attempts even when history is disabled.

**C5. Make history behavior explicit**

- History on includes bounded accumulated attempt information.
- History off omits accumulated attempts but retains defined immediate feedback.
- Neither mode receives hidden or unavailable validation results.
- Document exactly what context differs for the controlled comparison.

**C6. Integrate the agent**

- Connect Part A’s evidence, Part B’s tools and corrections, Part D’s model client and Part E’s prompts.
- Verify clean exit and required result output.
- Preserve the final worktree on normal budget termination.

### What it needs

- Shared contracts.
- Part B’s edit-result interface.
- Part D’s model-client interface.
- Part A and Part E interfaces.

**Can start immediately:** use scripted model replies and stub tools. Live integration follows as the other parts become available.

### What it supplies

- A runnable submission-mode agent.
- Consistent attempt records for Part E.
- An exact configuration for packaging and hosted tests.

### Done when

- The agent runs without any intermediate build callback.
- Multiple inspection and correction turns work.
- No unvalidated candidate is reported as repaired.
- Budget termination leaves a coherent final worktree and result file.
- History on/off changes context without disabling logging.

---

## Part D — Hosted integration and external validation

### What it does

Connects the agent to the approved hosted model service and provides independent evaluation of final candidates.

This part has two parallel tracks.

### D1. Hosted model and packaging integration

- Verify the current official Starter Kit and model-access contract.
- Read the injected endpoint, model name and token file.
- Keep personal/local model settings out of the hosted configuration.
- Handle authentication, rate limits, timeouts and retries according to the published limits.
- Confirm non-interactive startup and permitted filesystem access.
- Verify required files, dependencies and result schema.
- Package an exact revision and record its SHA-256.
- Run Hosted Smoke early; preserve a qualified fallback.
- Repeat qualification for the exact final submission version.

### D2. Validation environments and campaign runner

- Diagnose the five setup-blocked x86-target cases.
- Check the supplied runtime and emulation requirements.
- Investigate compatible native amd64 validation separately if useful.
- Record runtime changes as different experimental environments.
- Build unchanged originals where local environments are available.
- Determine which of the 200 cases have usable local or official validation.
- Confirm official validation format, allocation and availability.
- Build a resumable runner with isolated workspaces, timeouts and complete records.
- Validate final candidates after agent exit.
- Preserve infrastructure failures separately from repair outcomes.

### What it needs

- Current organizer documentation and account access.
- Existing local harness and run evidence.
- Part C for an integrated live agent.
- Part A’s case inventory for broad validation planning.

**Can start immediately:** model-client fixtures, packaging checks and environment diagnosis can proceed independently.

### What it supplies

- Hosted model access for Part C.
- A qualified submission artifact.
- External build/artifact verdicts for Part E.
- A case manifest showing available validation routes.

### Done when

- The exact uploaded agent passes Hosted Smoke.
- External validation is reproducible for supported cases.
- Setup-blocked, already-building and unvalidated cases are clearly labeled.
- The agent package contains no research harness dependency or credentials.

### Watch for

A local readiness pass is not hosted qualification. Published settings for 200 cases are not automatically 200 complete offline build environments.

---

## Part E — Prompts, controlled experiments and submission evidence

### What it does

Turns the other parts into a measured, reproducible submission candidate and a defensible Demo 3 comparison.

### E1. Update prompts

- Consume Part A’s package format and failure-stage information.
- Remove unconditional Debian assumptions.
- Remove expectations of intermediate hosted builds.
- Clearly separate edit rejection, application success and repair success.
- Incorporate Part B’s correction feedback.
- Reduce repeated evidence without dropping necessary context.
- Keep prompts independent of case-specific answers.

### E2. Define experiments before running them

Record:

- Code revision and configuration.
- Model and endpoint.
- Case list and source hashes.
- Per-case budgets.
- Intermediate build feedback setting.
- Repeat count and retry policy.
- Final validation environment.

Preserve both the frozen one-shot and Demo 2 full-agent references. Distinguish a historical build-enabled result from a new matched no-build comparison.

### E3. Run a representative pilot

- Choose approximately 20 cases using Part A’s inventory.
- Cover available formats, directions and failure categories.
- Confirm the complete execution-and-validation pipeline.
- Measure token usage, runtime and build cost.
- Resolve runner failures before scaling.

### E4. Run the 200-case campaign

- Freeze one candidate configuration.
- Run the agent on all 200 available public cases.
- Validate final candidates wherever a valid route exists.
- Report execution coverage and validation coverage separately.
- Run the frozen baseline across the same cases if resources permit.

A candidate and baseline sweep requires 400 agent runs before repeats.

### E5. Run controlled history comparisons

- History on versus history off.
- Same model, cases, budgets and other settings.
- Intermediate build feedback disabled in both groups.
- Three repeats per configuration on a fixed subset initially.
- Expand only if time and resources permit.

Report verified repairs, tokens, time, rejection rate, stop reasons and variation across repeats.

### E6. Select and document the final version

- Review regressions as well as gains.
- Choose settings from recorded evidence.
- Send the exact candidate revision to Part D for final qualification.
- Record the package hash, submission ID and hosted verdict.
- Update the design document, instructions and slides.
- Verify final portal selection before the deadline.

### What it needs

- Part A for case categories and diagnostic context.
- Part B for correction behavior.
- Part C for stable configurations and attempt records.
- Part D for hosted compatibility and validation verdicts.

**Can start immediately:** prompt audit, experiment design, result schema and case selection. Full campaigns wait for integration and pilot acceptance.

### Done when

- One reproducible procedure produces the comparison tables.
- All scheduled cases have explicit outcomes.
- Claims distinguish verified repairs from unvalidated edits.
- The selected package matches the qualified revision.

---

## Dependencies and integration order

```text
Shared contracts + frozen references
        │
        ├── A: Case understanding and diagnostics
        ├── B: Tools and edit correction
        ├── C: Controller development against stubs
        ├── D1: Hosted client and packaging
        ├── D2: Environments and validation runner
        └── E: Prompt audit and experiment design
                    │
          A + B + C + D1 + E prompts
                    │
           Integrated submission agent
                    │
          ┌─────────┴───────────┐
          │                     │
   Early Hosted Smoke     Pilot with D2
          │                     │
 Qualified fallback      Stable evaluation pipeline
                                │
                   200-case campaign + controlled repeats
                                │
                     Final configuration selection
                                │
                  Exact-version Hosted Smoke and freeze
```

### Dependency table

| Task | Depends on | Can proceed before that dependency is complete? |
| --- | --- | --- |
| A: diagnostic work | Shared contracts and case materials | Yes, with local fixtures |
| B: correction work | Shared contracts and saved traces | Yes |
| C: controller unit tests | Contract stubs | Yes |
| C: live integration | A, B, D1 and E prompts | Only partially |
| D1: hosted-client tests | Current API contract | Yes, independent of repair quality |
| D2: environment diagnosis | Case assets and runtime documentation | Yes |
| Early Hosted Smoke | Runnable C + D1 + compatible prompts | Does not need the 200-case campaign |
| Pilot evaluation | Integrated agent + usable D2 environments + E configuration | No |
| 200-case campaign | Passing pilot + frozen revision + resource estimate | No |
| History comparison | Stable C history controls + E experiment design + D2 validation | No |
| Final qualification | Selected exact code/settings/package | No |
| Final selection | Qualified immutable version | No |

---

## Checkpoints

| Checkpoint | Required evidence |
| --- | --- |
| **Contract checkpoint** | Frozen references, interfaces and current hosted requirements |
| **Integration checkpoint** | Agent completes without intermediate builds; model access works |
| **Early qualification checkpoint** | First Hosted Smoke result and a qualified fallback if successful |
| **Pilot checkpoint** | Representative cases execute reproducibly with clear final verdicts |
| **Evaluation checkpoint** | Broad case results and controlled comparisons, with coverage limitations stated |
| **Submission checkpoint** | Exact final artifact qualified, recorded and selected |

Suggested scheduling: complete contracts and initial integration first; begin Hosted Smoke and environment diagnosis early; target the broad evaluation during the following week; reserve the final days before November 13 for qualification and selection rather than new architecture changes.

## What counts as submission-ready

- [ ] Agent runs without the local research harness.
- [ ] Intermediate build requests are disabled under the applicable hosted contract.
- [ ] Injected model settings work.
- [ ] Inspection, correction, budgets and graceful stopping work.
- [ ] Final changes remain in the correct worktree.
- [ ] Required result output is valid.
- [ ] Submission checks pass.
- [ ] Exact package passes Hosted Smoke.
- [ ] Code, settings, package hash and evidence are preserved.
- [ ] Qualified version is selected before the deadline.

**The 200-case campaign measures capability; it is not a substitute for hosted qualification.** If validation resources prevent complete coverage, report that limitation and still complete the submission requirements.
