# Design Proposal

**Build-Bench Challenge: Autonomous LLM Agents for Cross-Architecture Package Repair**
**Team:** Anthony Capraru, Austin Li, Joonseo Moon, Juliette Jacques, Owen Zhang
**Mentor:** Minghua Ma, Microsoft

**Demo 1 presenters:** Owen Zhang and Joonseo Moon

**Demo 1:** September 23, 2026. Materials are due at noon on `demo-1`.

This is a design proposal. All agent modules and evaluation targets below are proposed; the completed work is the dataset inspection and starter-kit smoke run. We retain the course template's six sections. Milestones are proposed commitments. Team approval is pending before the proposal is frozen for submission.

## 1. Problem

Package maintainers must diagnose software that builds on one instruction set architecture but fails on another. Failures can arise from source assumptions, dependencies, compiler flags, build-system configuration, tests, or packaging. Manual repair requires moving between long logs, unfamiliar source trees, and build metadata, then rebuilding to determine whether a change helped.

We propose a general LLM repair agent that gathers evidence, forms and revises a root-cause hypothesis, edits permitted package files, and requests validation within a fixed resource budget. The desired benefit is less manual investigation with independently verifiable repairs. We will measure repair success and resource cost; this project does not yet measure developer time saved.

**Scope.** Our downloaded release has 200 Debian source-package cases across 188 packages: 100 x86_64-to-aarch64 and 100 in the reverse direction. The competition also advertises RISC-V migrations, but this release contains no RISC-V cases. Initial course experiments cover the two available directions. RISC-V support depends on released cases and permitted infrastructure. [1, 3]

**Non-goals for Demo 1.** Implementing an agent, making case-specific hard-coded fixes, modifying the organizer's runner to pass, training a model, and claiming hidden-case performance. No competition registration or qualification is claimed.

## 2. Proposed design

### Execution contract

An agent receives `task.json` and initial failure evidence in `/workspace/input`, edits only the permitted repository under `/workspace/work/repo`, and writes structured completion output under `/workspace/output`. The platform derives a canonical patch, applies it to a fresh case, runs the official target build, and checks expected artifacts. Agent completion alone does not establish repair success. We will follow the submission checks, including `agent-result.json`, a README, and exactly pinned dependencies. [1, 4]

The agent will use only permitted tools. It must not receive a Docker socket or start the validator directly. The controller adapts to the organizer's model-access and build-feedback protocol. The supplied local runner disables agent networking and does not expose a usable iterative model/validation client, so those interfaces must be confirmed before implementation. In-loop build feedback depends on the supported interface; final clean-build validation and artifact checks are always required for a verified repair. Local hello testing is already available; arbitrary public-case execution is not yet established.

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

We choose bounded evidence retrieval over placing the entire repository and log in context. One downloaded libyuv log is 2.37 GB; noisy repetition would crowd out the relevant source and diagnostics. We choose iterative diagnosis over a single prompt because validation can contradict the first hypothesis. We choose a small explicit state machine and append-only attempt records over an unconstrained conversation so experiments can explain failures and enforce limits. We choose official fresh-build validation over the model's own confidence or an incremental workspace build. [3, 4]

Initial configurable research limits are **5 repair candidates, at most 3 in-loop validation builds where permitted, 30 minutes total wall time per case, 30,000 model tokens, and 2 repeated identical failure/patch pairs**. These are proposed caps, not published competition limits. Use the stricter organizer cap where applicable and freeze the actual settings before comparison. Record token categories supported by the provider; unknown usage stays unknown, never zero. The wall-clock budget includes tool and build requests. Calibrate these caps on the development cases before freezing a comparison; historical time to failure is not an estimate of successful build duration.

## 3. What makes this hard

The hardest challenge is selecting the next useful evidence or repair action under an expensive, incomplete feedback loop. A compiler error may be downstream of a dependency problem, and a cross-architecture failure may actually occur during packaging. Repeated noisy logs can hide the causal diagnostic. The planner must connect architecture and build-system facts to a hypothesis, choose an informative tool call, and revise after a failed build without cycling through the same edits.

This requires more than an API call: bounded log extraction with provenance, tool and state management, reproducible validation integration, failure classification, and cost-aware stopping must work together. Our central experiment will test whether evidence selection and repair history increase verified repairs or reduce model usage compared with the Demo 2 prototype. Failure labels from the current log scan are heuristic signals, not established root causes.

## 4. How you will know it worked

**Primary metric:** Verified Build Success Rate = cases whose canonical patch applies, clean target build succeeds, and required artifacts pass verification / all cases in the frozen evaluation set. Report numerator and denominator. Timeouts, invalid patches, crashes, and unresolved cases remain in the denominator. Report infrastructure failures separately without silently removing them; any secondary rate restricted to runnable cases must identify that denominator.

**Secondary metrics:** total wall time, model token usage, repair candidates, tool calls by type, termination reason, and results by migration direction. Report per-case records and medians, plus failure categories only when manually supported. Build success demonstrates the benchmark outcome, not unrestricted semantic correctness. Patch application requires explicit evidence such as `patch_applied: true`; absence of `invalid_patch` is insufficient when infrastructure failed before application.

Each case/run record includes case and agent versions, split, run ID, model/configuration, budgets, validator status, patch-application evidence, agent termination/error, candidate count, validation requests, tool counts, token usage or unknown, total wall time and paths to the transcript, final diff and validator result. Preserve each row, including attempts that never reach validation.

**Comparison plan.** Inspect the organizer-linked baseline at a recorded commit and reproduce it under matching cases, model, tool access and budgets where compatible. The hello example is a hard-coded smoke test and cannot establish a general-agent baseline. If the linked repository contains only that example or its model protocol is incompatible, document the limitation and use a named one-shot LLM comparator with the same final validator; do not label it official. Compare later versions against the frozen Demo 2 prototype, and put the selected improvement behind a flag for an ablation. [1, 4]

Freeze 10 public cases for Demo 2, aiming for five per direction. For the final, target 40 cases, 20 per direction: the original 10 plus 30 held out from prompt/strategy tuning. Group repeated package names across splits, choose cases before inspecting repair outcomes, and preserve the selection manifest. Freeze the final held-out set before Demo 3 optimization. If environment availability prevents these targets, announce and justify the revised scope at a demo rather than silently changing the set. These small public subsets do not estimate hidden leaderboard performance.

Use the same model/configuration and case snapshot for paired comparisons. Run every compared configuration three times on **all 10 development cases**, with a fixed run schedule recorded before optimization. Report per-case outcomes and each run's repair count, plus the mean across all three runs. The Demo 3 repair target is at least one additional repair per 10 cases on average, with a positive net gain in at least two of the three paired runs. For the token target, retain every case repaired by Demo 2 in the corresponding runs, have at least one verified repair overall, and reduce aggregate tokens across all 30 attempts by at least 20%; missing usage cannot satisfy it. Report regressions even when the repair target is met. This avoids selecting only favorable cases for repeats. Evaluate the 40-case final set once per configuration and report the 30-case held-out result separately; disclose the smaller repeat coverage outside the development set. Freeze the improvement criterion before optimization.

### Current evidence, September 21

- Downloaded materials were inventoried and archived. Dataset checks covered 1,510 listed checksums and 705 per-case source entries with no mismatches. The release contains 200 cases and 188 packages. [3]
- The unmodified rc.2 starter kit, with documented runtime-image overrides, ran its supplied hello example on the team's Linux VPS. The initial build failed, the example agent completed, the canonical patch applied, and final validation succeeded with build exit code 0 and two verified RPM artifacts. Both downloaded artifact hashes were checked. [4]
- **We reproduced the organizer's example workflow; this is not a Build-Bench repair result from our agent.** The final validator build reported **9 seconds**. This is one x86_64 hello run, with an architecture-independent RPM and a known marker-replacement repair. It is not a cross-architecture experiment, total agent runtime, LLM benchmark, or repair-rate result on the development cases.
- General repair-agent implementation, public-case baseline evaluation, model/protocol integration, and competition qualification remain pending. The mentor direction in the team brief supports diagnosis, harness design, repair and validation; no meeting transcript is present in the repository.

## 5. Milestones

Dates below follow the course repository template. Targets are intentionally measurable and must be reviewed by the team before submission. [2]

| Demo | Date | Milestone | How we will demonstrate it |
| --- | --- | --- | --- |
| Demo 2 | 10/21 | Compatible end-to-end LLM prototype; reproduce starter workflow and baseline or document compatibility blocker. | Runnable packaged agent and exact command. Trace on at least one materialized Debian case shows a reproduced original diagnostic, model call, file inspection, candidate edit, completion output and clean validation attempt. Preserve versions, adapter mode and limits; record `./bb ready` honestly, including compatibility failures. |
| Demo 2 | 10/21 | Freeze and evaluate 10 public cases, aiming for 5 per direction. | Checked-in case manifest and reproducible experiment command. Per-case verified status, wall time, tokens, attempts and tool calls, including failures. No minimum repair rate promised. |
| Demo 3 | 11/16 | Categorize Demo 2 failures and implement one evidence-driven improvement. | Failure taxonomy with concrete traces, changed module behind a configuration flag, and an ablation isolating the change. |
| Demo 3 | 11/16 | Measured improvement on the same 10 cases. | Target at least 1 additional verified repair per 10 cases on average, or at least 20% lower aggregate tokens while preserving repaired cases, under the three-full-run criteria above. Hold model and budget ceilings fixed, disclose regressions, and retain all attempts. Missing usage cannot satisfy the token criterion. |
| Final | 12/09 | Stable agent and reproducible evaluation on the proposed 40-case set. | Pinned dependencies, agent version, case manifests, exact setup/commands, expected outputs, raw records, limits and failure analysis. Compare official baseline where reproducible, Demo 2, Demo 3 and final versions under a documented common setup. |
| Final | 12/09 | Competition-ready packaging and qualification attempt if infrastructure and progress permit. | Archive validation report and submission/qualification receipt or a specific blocker. A competition version must be ready **before the currently published November 13 freeze**, not at the December final. Registration remains unverified. |

Milestone changes must be announced with justification at the demo. A missed target is reported as a miss; it is not retroactively relabeled as success. The competition website lists results by November 20. Recheck those external dates before entering. [1]

## 6. Risks

| Risk | Mitigation and evidence needed |
| --- | --- |
| Public development inputs are not complete runnable validator cases | First reconstruct one permitted Debian case and reproduce its original failure. Confirm manifests, frozen dependencies, source materialization and build configuration with organizers. AWS does not resolve historical dependency reproduction. Keep the hello smoke result separate. |
| Local runner has no demonstrated model/iterative feedback client | Resolve supported model gateway and feedback API before implementing the loop. Preserve runtime isolation; do not add a Docker socket or assume outbound networking. |
| Sparse, misleading or huge logs | Stream and deduplicate with source offsets; measure extraction failures and inspect representative traces. |
| Hallucinated edits or package-specific overfitting | Validate clean builds, retain required tests/artifacts, review recurring failure categories, and hold out package groups from tuning. |
| AWS capacity, cost and target environments | We plan to run course-scale experiments on AWS using reproducible x86_64 and ARM64 environments. Before comparisons, pin instance type, machine image, Docker version, container image digests and configuration. Provisioning, cost and capacity still need validation. The VPS was used only for the initial Starter Kit smoke test. RISC-V resources remain outside the current public-case plan. |
| Performance gain does not materialize | Keep the predefined comparison, show negative results/regressions, and announce any scope revision. Do not cherry-pick successful cases. |
| Competition and course timelines differ | Decide participation early. Prepare qualification before the November 13 external freeze, ahead of Demo 3. |

**Team decisions still open:** milestone approval; model/provider and spending budget; protocol and target-build access; case selection and attainable case counts; final per-case caps; module ownership; and competition participation. Demo 1 presenters are **Owen Zhang and Joonseo Moon**. The course permits at most two presenters per demo and requires every member to present at least once across the three demos. [2]

### References

1. [Official Build-Bench Challenge](https://matrix.cstcloud.cn/build-bench/), inspected September 21, 2026: execution/scoring, architecture scope, organizer baseline link and timeline. [Organizer-linked baseline repository](https://github.com/AIOps-Lab-NKU/BuildBench-Agent-Baseline). Baseline compatibility and results have not been verified. The site cites Zhao et al., [Can Language Models Go Beyond Coding?](https://arxiv.org/abs/2511.00780); no literature performance number is used as our baseline.
2. [EC528 grading and presentation requirements](https://ec528.github.io/ec528/fall26/grading/) and [submission instructions](https://ec528.github.io/ec528/fall26/setup/). Demo dates are from this repository's original proposal template. Rubric and presenter/quiz requirements were checked on the official grading page September 21.
3. [Dataset summary and provenance](evidence/demo-1/dataset-summary.json), derived from `analysis/dataset-analysis.json` inside the [downloaded materials snapshot](https://github.com/ec528-fall26/build-bench/blob/624f2b6/materials/README.md). Full analysis and input hashes reside in that snapshot.
4. [Archived hello results and reproduction instructions](evidence/demo-1/README.md). Interface descriptions also come from the supplied rc.2 starter kit's README, AGENTS.md and runner inspection. The runtime contract is documented in the [included starter kit](../buildbench-starter-kit-0.1.0-rc.2/README.md), its `runner/` and `AGENTS.md`.
