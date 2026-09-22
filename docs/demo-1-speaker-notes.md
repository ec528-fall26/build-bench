# Demo 1 speaker notes

Target: 9 minutes 40 seconds, leaving 20 seconds of margin. Q&A: 5 minutes. **Owen Zhang presents slides 1–4 (4:50). Joonseo Moon presents slides 5–8 (4:50).** Handoff after slide 4: “Joonseo will explain how we control the repair loop and measure whether it improves.” Both presenters take questions.

## 1. Project and objective (45 seconds)

**Presenter: Owen Zhang**

We are Owen Zhang and Joonseo Moon. We are designing an autonomous agent to repair software packages that fail after migration between hardware architectures. Our team is Anthony Capraru, Austin Li, Joonseo Moon, Juliette Jacques, and Owen Zhang, with mentor Minghua Ma at Microsoft. Today we will explain the problem, the proposed repair loop, how success is independently checked, and our semester milestones. This is a design proposal; our general LLM agent is scheduled for Demo 2.

Sources: Team roster supplied by the team. Project title and mentor: repository README and Demo 1 brief.

## 2. Problem and motivation (65 seconds)

**Presenter: Owen Zhang**

A package that builds on one architecture can fail on another because architecture-specific source, dependencies, compiler behavior or packaging assumptions change. The maintainer must determine which failure is causal and where to make the repair. The visible compiler error is not always the root cause. Our objective is to automate that investigation while preserving an independent executable check of the outcome. We will measure verified repairs and resource usage. We are not yet claiming measured developer time savings. The downloaded cases come from real Debian source packages and historical build failures.

Sources: Official challenge overview, https://matrix.cstcloud.cn/build-bench/ (checked 2026-09-21). docs/evidence/demo-1/dataset-summary.json.

## 3. Build-Bench verification model (75 seconds)

**Presenter: Owen Zhang**

Read the diagram from the upper left across the top row, then down and back across the bottom. Cases describe a package that worked on a source architecture and failed on a target architecture. We have the target-failure evidence; we have not independently reproduced source success for the development dataset. The agent edits its permitted working repository. The organizer then derives a canonical patch, applies it to a fresh case, and runs the target build with expected artifact verification. Completion status and a plausible patch are insufficient. The downloaded public release contains 100 cases in each x86_64/ARM64 direction. The broader competition includes RISC-V, but RISC-V is outside our available public-case evidence.

Sources: Official challenge overview, https://matrix.cstcloud.cn/build-bench/. Supplied starter kit rc.2 README and AGENTS.md. Dataset evidence summary.

## 4. Proposed system architecture (105 seconds)

**Presenter: Owen Zhang**

The Case Manager normalizes metadata and paths. The Evidence Manager selects log slices and relevant files. The LLM Planner records a hypothesis and chooses the next action. Controlled Tools inspect source and Debian packaging files using Python. The Repair Generator makes permitted edits. The Validation/Feedback Adapter requests in-loop build feedback when the supported interface allows it. Failure returns evidence to the loop. The final worktree always goes through the organizer's clean build and artifact checks before a repair can count as successful. History/Metrics records attempts and outcomes, while Budget Controls enforce stopping limits. All components are proposed. We separate the submitted agent from a Linux harness that provides model access and invokes the validator outside the sandbox. Hosted model and feedback interfaces remain unconfirmed. We plan to use AWS for course experiments, with x86_64 and ARM64 environments and pinned configurations. Those environments have not been provisioned or verified. Joonseo will explain how we control the repair loop and measure whether it improves.

Sources: Proposed design in docs/design-proposal.md. Supplied starter kit rc.2 runtime interface and local runner inspection.

## 5. Iteration, context and resource controls (80 seconds)

**Presenter: Joonseo Moon**

Each attempt should test an explicit hypothesis, rather than simply asking for another patch. First identify a failing stage, then inspect the files or build configuration that could explain it. Preserve diagnostic locations so the model can request more context. The largest downloaded log is 2,370,517,311 bytes, about 2.37 decimal gigabytes, which makes unrestricted log ingestion impractical. A history of patch hashes and normalized failure signatures helps prevent repeating the same unsuccessful change. Initial research caps are five repair candidates, thirty minutes total wall time including tools and builds, and thirty thousand model tokens. We allow at most three in-loop builds where supported and stop after two identical failure/patch pairs. A later build stage is only a clue, not proof of progress. Caught exceptions still count as agent errors. These are our design defaults and may be reduced by official limits. Model usage that cannot be observed will remain unknown, not zero.

Sources: docs/evidence/demo-1/dataset-summary.json, summary.largest_logs[0]. docs/design-proposal.md, proposed research limits.

## 6. Evaluation plan (75 seconds)

**Presenter: Joonseo Moon**

The primary metric is the number of cases meeting clean-patch, target-build and artifact requirements divided by every case in a frozen set. We retain crashes, timeouts, invalid patches and unresolved cases in that denominator, and report infrastructure failures separately. Any runnable-case-only rate must be labeled with its own denominator. We will compare the organizer-linked baseline where it can be reproduced, pinning its version, model and configuration. If it is incompatible, we will document that and use an explicitly named one-shot comparator provisionally. Record total wall time, tokens, attempts and tool calls. Run each compared configuration three times on all ten development cases. Report every run and regression, not only repeats on cases that improved. We will group related package names between development and held-out cases to reduce tuning leakage. No public result can establish hidden-case performance.

Sources: Official scoring: https://matrix.cstcloud.cn/build-bench/. Organizer-linked baseline: https://github.com/AIOps-Lab-NKU/BuildBench-Agent-Baseline (link verified on official overview; compatibility unverified). docs/design-proposal.md evaluation plan.

## 7. Milestones and schedule (80 seconds)

**Presenter: Joonseo Moon**

For Demo 2, the prototype must invoke a model, inspect files, produce an allowed edit and completion record, and reach clean validation. We will evaluate ten fixed public cases, targeting five in each available direction, and establish a reproducible baseline. Demo 2 does not promise a particular success rate. For Demo 3, categorize failures and implement one substantive improvement. The target is one additional repair per ten cases on average, with positive net gains in at least two of three full runs. The alternative is twenty percent fewer aggregate model tokens while retaining every previously repaired case in the corresponding runs, with at least one verified repair overall. Keep the same model and budget ceilings and report all attempts. A configuration flag isolates the improvement for an ablation. The final target is forty cases balanced by direction, including thirty package-grouped held-out cases frozen before optimization. Preserve complete results and reproducibility instructions. These evaluation targets define our semester milestones. Resource caps will be calibrated before comparisons. The website lists a November thirteenth competition freeze, before Demo 3. If we enter, qualification and a frozen version must be ready earlier; December is only the course final reporting date.

Sources: Dates: original docs/design-proposal.md course template. Progress rubric: https://ec528.github.io/ec528/fall26/grading/. Competition timeline checked 2026-09-21: https://matrix.cstcloud.cn/build-bench/.

## 8. Current status and next steps (55 seconds)

**Presenter: Joonseo Moon**

We have inspected the 200-case release across 188 packages and reproduced the organizer's hello example on Linux. Our LLM repair agent and public-case evaluation are scheduled for Demo 2. The first priority is a reproducible Debian case: provision the planned AWS environments, pin their configurations and reproduce the original failure. In parallel, confirm the supported model and feedback interfaces and implement the bounded repair loop. AWS provides the planned compute environment, but historical dependencies and hosted compatibility still need verification. This setup lets us evaluate the central question: can better evidence selection and repair history improve verified repairs or reduce model usage? Our hardest challenge is choosing useful evidence and revising diagnoses within a budget.

Sources: docs/evidence/demo-1/dataset-summary.json and docs/evidence/demo-1/README.md. Development priorities: docs/design-proposal.md.
