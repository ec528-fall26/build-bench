# Design Proposal

**Build-Bench Challenge: Autonomous LLM Agents for Cross-Architecture Package Repair**
**Team:** Anthony Capraru, Austin Li, Joonseo Moon, Juliette Jacques, Owen Zhang
**Mentor:** Minghua Ma, Microsoft

**Demo 1 presenters:** Owen Zhang and Joonseo Moon

**Demo 1:** September 23, 2026. Materials are due at noon on `demo-1`.

This proposal defines the repair-agent design, evaluation plan and milestones for Demo 2, Demo 3 and the final. General repair-agent implementation and public-case evaluation are scheduled for Demo 2.

## 1. Problem

Package maintainers must diagnose software that builds on one instruction set architecture but fails on another. Failures can arise from source assumptions, dependencies, compiler flags, build-system configuration, tests, or packaging. Manual repair requires moving between long logs, unfamiliar source trees, and build metadata, then rebuilding to determine whether a change helped.

We propose a general LLM repair agent that gathers evidence, forms and revises a root-cause hypothesis, edits permitted package files, and requests validation within a fixed resource budget. The desired benefit is less manual investigation with independently verifiable repairs. We will measure repair success and resource cost; this project does not yet measure developer time saved.

**Scope.** Our downloaded release has 200 Debian source-package cases across 188 packages: 100 x86_64-to-aarch64 and 100 in the reverse direction. The competition also advertises RISC-V migrations, but this release contains no RISC-V cases. Initial course experiments cover the two available directions. RISC-V support depends on released cases and permitted infrastructure. [1]

**Scope boundaries.** Agent implementation begins after Demo 1. The project will use an existing LLM rather than train a new model, and repairs must pass the organizer's unchanged validation process. Evaluation claims will be limited to the cases tested.

## 2. Proposed design

### Execution contract

An agent receives `task.json` and initial failure evidence in `/workspace/input`, edits only the permitted repository under `/workspace/work/repo`, and writes structured completion output under `/workspace/output`. The platform derives a canonical patch, applies it to a fresh case, runs the official target build, and checks expected artifacts. Agent completion alone does not establish repair success. We will follow the submission checks, including `agent-result.json`, a README, and exactly pinned dependencies. [1, 3]

The agent will use only permitted tools. It must not receive a Docker socket or start the validator directly. The controller adapts to the organizer's model-access and build-feedback protocol. The supplied local runner disables agent networking and does not expose a usable iterative model/validation client, so those interfaces must be confirmed before implementation. In-loop build feedback depends on the supported interface; final clean-build validation and artifact checks are always required for a verified repair. Public-case execution is not yet established.

**Separate agent and development harness.** The submitted agent contains the repair loop and two adapters: `ModelClient` for model requests and usage, and `BuildFeedback` for an optional build request, structured status and log. A separate Linux harness materializes public cases, holds API credentials, invokes the official validator outside the agent sandbox, and records experiments. For local research we propose a file request/response bridge: the isolated agent writes bounded requests under its output mount; the host validates them and supplies responses through a separate agent-read-only input mount. This bridge is not implemented or an organizer-provided interface. Hosted compatibility remains a gate until the organizers confirm their model and feedback protocol. The agent receives neither credentials nor a Docker socket. If iterative feedback is unavailable, it produces a bounded candidate for post-run validation, and results identify that mode. `./bb check` validates the submission, `./bb package` creates its ZIP, and `./bb ready` also runs the example test; packaging alone does not establish end-to-end compatibility.

**Debian case preparation.** The public cases contain compressed source packages and historical logs, not complete runnable validator cases. The harness must reconstruct an unpacked source tree, build configuration and dependency snapshot, then reproduce the original diagnostic as well as the failing build step. The hosted Debian worktree layout and allowed edit paths still need confirmation. Read architecture information from supplied metadata where available, retain its provenance, and mark missing fields unknown. Debian build/host architecture fields describe compilation roles; they do not establish the architecture on which a package previously succeeded.

### Architecture

```mermaid
flowchart TD
    C[Case Manager: metadata, source, initial failure] --> E[Evidence Manager: log slices, files, build context]
    E --> P[LLM Planner: hypothesis and next action]
    P --> T[Controlled Tools: search and inspect]
    T --> E
    P --> R[Repair Generator: permitted worktree edits]
    R --> V[Validation/Feedback Adapter: supported in-loop feedback]
    V -->|failure evidence, when available| E
    V -->|feedback pass or budget stop| F[Final Worktree and completion output]
    F --> O[Organizer: mandatory canonical patch, clean build and artifact checks]
    H[History/Metrics: hypotheses, diffs and outcomes] -.-> P
    P -.-> H
    V -.-> H
    B[Budget Controls: tokens, time, builds and repeats] -.-> P
    B -.-> V
```

| Component | Responsibility and boundary |
| --- | --- |
| Case Manager | Normalize package, architecture, build-stage and input paths without altering the original evidence. |
| Evidence Manager | Stream logs, retain diagnostic windows and source locations, collapse repetitions, and retrieve relevant files within the context budget. |
| LLM Planner | Record a falsifiable hypothesis, choose the next tool or candidate repair, and explain which observed failure it addresses. |
| Controlled Tools | Use bounded Python tools for search, file ranges, snapshot diffs, `debian/rules`, `debian/control`, patch series and architecture guards. Do not assume Git, a compiler or unrestricted shell commands exist in the managed runtime. |
| Repair Generator | Apply candidate UTF-8 text edits to allowed worktree paths and inspect the diff. Preserve required tests and artifacts. |
| Validation/Feedback Adapter | Request supported in-loop feedback when available and classify the outcome. Final clean-build validation and artifact checks remain mandatory for verified success. |
| History/Metrics | Persist hypotheses, patch hashes, failure signatures, validation outcomes, tool calls, usage and termination reasons. |
| Budget Controls | Enforce provisional candidate, build, token, wall-time and repeat limits; use stricter organizer limits where applicable. |

### Repair loop and decisions

1. **Observe:** identify the failing stage and preserve diagnostic provenance, including file paths and log offsets.
2. **Diagnose and inspect:** record a suspected cause, supporting log/file locations, repair layer and next discriminating check. Select relevant files/tools and gather confirming or contradicting evidence. A different patch to the same file remains a valid candidate; a coarse hypothesis label alone must not block it.
3. **Repair:** make a bounded candidate change and inspect its diff. Preserve tests and required artifacts; do not treat disabling checks as a general repair strategy.
4. **Validate:** request in-loop build feedback when the supported interface permits it and record the status and evidence. Otherwise finish the bounded candidate for mandatory final clean-build validation.
5. **Revise or stop:** update the hypothesis on failure. Build-stage movement and repeated diagnostics are clues, not proof that an edit helped or harmed. Inspect the new evidence and diff before retaining or reverting a candidate. Stop on verified feedback success, exhausted budgets, or a repeated unchanged failure/patch pair. The platform still performs the final clean validation after exit.

Keep an internal termination reason separate from the runner's `completed` protocol status. Caught and uncaught agent exceptions are recorded as agent errors and remain in evaluation counts. A protocol-complete run may still produce no repair. Preflight text edits with the kit's canonical patch generator and clean application in the harness. In rc.2, edits to a file whose original contents lack a final newline can produce a malformed diff; adding a newline only to the edited file is not a guaranteed remedy. Record the compatibility failure rather than modifying the official runner.

We choose bounded evidence retrieval over placing the entire repository and log in context. One downloaded libyuv log is 2.37 GB; noisy repetition would crowd out the relevant source and diagnostics. We choose iterative diagnosis over a single prompt because validation can contradict the first hypothesis. We choose a small explicit state machine and append-only attempt records over an unconstrained conversation so experiments can explain failures and enforce limits. We choose official fresh-build validation over the model's own confidence or an incremental workspace build. [3]

Initial configurable research limits are **5 repair candidates, at most 3 in-loop validation builds where permitted, 30 minutes total wall time per case, 30,000 model tokens, and 2 repeated identical failure/patch pairs**. These are proposed caps, not published competition limits. Use the stricter organizer cap where applicable and freeze the actual settings before comparison. Record token categories supported by the provider; unknown usage stays unknown, never zero. The wall-clock budget includes tool and build requests. Calibrate these caps on the development cases before freezing a comparison; historical time to failure is not an estimate of successful build duration.

### Implementation and model selection

We will build the agent in Python and evaluate candidate LLMs based on repair performance, reliability, and cost. If access permits, we will compare two candidate models through an approved endpoint. The pilot will use preselected development cases with identical prompts, tools, cases, and resource limits for each model. The model with the strongest verified-repair performance will be the initial choice, with cost and reliability considered. The pilot will guide the decision rather than establish a general model ranking.

## 3. What makes this hard

The hardest challenge is selecting the next useful evidence or repair action under an expensive, incomplete feedback loop. A compiler error may be downstream of a dependency problem, and a cross-architecture failure may actually occur during packaging. Repeated noisy logs can hide the causal diagnostic. The planner must connect architecture and build-system facts to a hypothesis, choose an informative tool call, and revise after a failed build without cycling through the same edits.

This requires more than an API call: bounded log extraction with provenance, tool and state management, reproducible validation integration, failure classification, and cost-aware stopping must work together. Our central experiment will test whether evidence selection and repair history increase verified repairs or reduce model usage compared with the Demo 2 prototype. Failure labels from the current log scan are heuristic signals, not established root causes.

## 4. How you will know it worked

**Primary metric:** Verified Build Success Rate = cases whose canonical patch applies, clean target build succeeds, and required artifacts pass verification (numerator) / all cases in the frozen evaluation set (denominator). Results will report the number of verified repairs, total cases, and verified build success rate percentage. Timeouts, invalid patches, crashes, and unresolved cases remain in the denominator. Infrastructure failures will be reported separately without silently removing them; any secondary rate restricted to runnable cases must identify that denominator.

**Secondary metrics:** Total wall time, model token usage, repair candidates, tool calls by type, termination reason, and results by migration direction. Report per-case records and medians, plus failure categories only when manually supported. Build success demonstrates the benchmark outcome, not unrestricted semantic correctness. Patch application requires explicit evidence such as `patch_applied: true`; absence of `invalid_patch` is insufficient when infrastructure failed before application.

Each case/run record includes case and agent versions, split, run ID, model/configuration, budgets, validator status, patch-application evidence, agent termination/error, candidate count, validation requests, tool counts, token usage or unknown, total wall time and paths to the transcript, final diff and validator result. Preserve each row, including attempts that never reach validation.

**Comparison plan.** The team will inspect the organizer-linked baseline at a recorded commit and reproduce it using matching cases, model, tool access, and budgets where compatible. If no compatible general-agent baseline is available, the team will document the limitation and use a clearly identified one-shot LLM baseline with the same final validator. Later versions will be compared against the frozen Demo 2 prototype. The selected improvement will also be tested with the feature enabled and disabled to measure its contribution. [1, 3]

**Evaluation Cases:** Freeze 10 public cases for Demo 2, aiming for five per direction. For the final evaluation, target is 40 cases with 20 per direction: the original 10 plus 30 held out from model selection, prompt development and strategy tuning. Cases from the same package will remain in the same split. Selection will be recorded before examining repair outcomes, and held-out set will be frozen before Demo 3 optimization.

**Success targets and repeated runs:** Use the same model/configuration and case snapshot for paired comparisons. Run every compared configuration three times on **all 10 development cases**, with a fixed run schedule recorded before optimization. Report per-case outcomes and each run's repair count, plus the mean across all three runs. The Demo 3 repair target is at least one additional repair per 10 cases on average, with a positive net gain in at least two of the three paired runs. For the token target, retain every case repaired by Demo 2 in the corresponding runs, have at least one verified repair overall, and reduce aggregate tokens across all 30 attempts by at least 20%; missing usage cannot satisfy it. Report regressions even when the repair target is met. This avoids selecting only favorable cases for repeats. Evaluate the 40-case final set once per configuration and report the 30-case held-out result separately; disclose the smaller repeat coverage outside the development set. Freeze the improvement criterion before optimization.

### Current evidence (September 21)

- Downloaded materials were inventoried and archived. Dataset checks covered 1,510 listed checksums and 705 per-case source entries with no mismatches. The release contains 200 cases and 188 packages. [2]
- We reproduced the organizer's hello example on Linux, confirming the starter workflow. Our LLM repair agent and public-case evaluation are scheduled for Demo 2. [3]

## 5. Milestones

The following milestones define the deliverables and verification criteria for each course deadline.

| Demo | Date | Milestone | How we will demonstrate it |
| --- | --- | --- | --- |
| Demo 2 | 10/21 | Compatible end-to-end LLM prototype; reproduce starter workflow and baseline or document compatibility blocker. | Runnable packaged agent and exact command. Trace on at least one materialized Debian case shows a reproduced original diagnostic, model call, file inspection, candidate edit, completion output and clean validation attempt. Preserve versions, adapter mode and limits; record `./bb ready` honestly, including compatibility failures. |
| Demo 2 | 10/21 | Freeze and evaluate 10 public cases, aiming for 5 per direction. | Checked-in case manifest and reproducible experiment command. Per-case verified status, wall time, tokens, attempts and tool calls, including failures. No minimum repair rate promised. |
| Demo 3 | 11/16 | Categorize Demo 2 failures and implement one evidence-driven improvement. | Failure taxonomy with concrete traces, changed module behind a configuration flag, and an ablation isolating the change. |
| Demo 3 | 11/16 | Measured improvement on the same 10 cases. | Target at least 1 additional verified repair per 10 cases on average, or at least 20% lower aggregate tokens while preserving repaired cases, under the three-full-run criteria above. Hold model and budget ceilings fixed, disclose regressions, and retain all attempts. Missing usage cannot satisfy the token criterion. |
| Final | 12/09 | Stable agent and reproducible evaluation on the 40-case target set. | Pinned dependencies, agent version, case manifests, exact setup/commands, expected outputs, raw records, limits and failure analysis. Compare official baseline where reproducible, Demo 2, Demo 3 and final versions under a documented common setup. |
| Final | 12/09 | Competition-ready packaging and qualification attempt if infrastructure and progress permit. | Archive validation report and submission/qualification receipt or a specific blocker. A competition version must be ready **before the currently published November 13 freeze**, not at the December final. Registration remains unverified. |


## 6. Risks

| Risk | Mitigation and evidence needed |
| --- | --- |
| Public development inputs are not complete runnable validator cases | First reconstruct one permitted Debian case and reproduce its original failure. Confirm manifests, frozen dependencies, source materialization and build configuration with organizers. AWS does not resolve historical dependency reproduction. |
| Local runner has no demonstrated model/iterative feedback client | Select the model/provider and resolve the supported model gateway and feedback API before implementing the loop. Preserve runtime isolation; do not add a Docker socket or assume outbound networking. |
| Sparse, misleading or huge logs | Stream and deduplicate with source offsets; measure extraction failures and inspect representative traces. |
| Hallucinated edits or package-specific overfitting | Validate clean builds, retain required tests/artifacts, review recurring failure categories, and hold out package groups from tuning. |
| AWS capacity, cost and target environments | We plan to run course-scale experiments on AWS using reproducible x86_64 and ARM64 environments. Before comparisons, pin instance type, machine image, Docker version, container image digests and configuration. Set the model and compute spending budget, then verify provisioning, cost and capacity before running comparisons. RISC-V resources remain outside the current public-case plan. |
| Performance gain does not materialize | Keep the predefined comparison, show negative results/regressions, and announce any scope revision. Do not cherry-pick successful cases. |
| Competition and course timelines differ | Decide participation early. Prepare qualification before the November 13 external freeze, ahead of Demo 3. |

Demo 1 presenters are **Owen Zhang and Joonseo Moon**.

### References

1. [Official Build-Bench Challenge](https://matrix.cstcloud.cn/build-bench/), inspected September 21, 2026: execution/scoring, architecture scope, organizer baseline link and timeline. [Organizer-linked baseline repository](https://github.com/AIOps-Lab-NKU/BuildBench-Agent-Baseline). Baseline compatibility and results have not been verified. The site cites Zhao et al., [Can Language Models Go Beyond Coding?](https://arxiv.org/abs/2511.00780); no literature performance number is used as our baseline.
2. [Dataset summary and provenance](https://github.com/ec528-fall26/build-bench/blob/5f5a9f946f5da3ef42925b6fe6e8d1d2f0914db9/docs/evidence/demo-1/dataset-summary.json), derived from `analysis/dataset-analysis.json` inside the [downloaded materials snapshot](https://github.com/ec528-fall26/build-bench/blob/624f2b6/materials/README.md). Full analysis and input hashes reside in that snapshot.
3. [Archived run results and reproduction instructions](https://github.com/ec528-fall26/build-bench/blob/5f5a9f946f5da3ef42925b6fe6e8d1d2f0914db9/docs/evidence/demo-1/README.md). Interface descriptions also come from the supplied rc.2 starter kit's README, AGENTS.md and runner inspection. The runtime contract is documented in the [archived starter kit](https://github.com/ec528-fall26/build-bench/blob/5f5a9f946f5da3ef42925b6fe6e8d1d2f0914db9/buildbench-starter-kit-0.1.0-rc.2/README.md), its `runner/` and `AGENTS.md`.
