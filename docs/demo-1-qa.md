# Demo 1 Q&A preparation

Presenters: **Owen Zhang and Joonseo Moon**. Aim for a direct 20–30 second answer, then offer the supporting detail if asked. These are rehearsal prompts, separate from the confidential course quiz.

## Core explanation

“We propose an LLM agent that uses build logs and source files to diagnose a package failure, makes a permitted repair, and submits the final worktree for an independent clean build. We will measure verified repairs and resource use on fixed public cases. Our general repair agent and public-case evaluation are scheduled for Demo 2.”

## Likely questions

| Question | Lead | Suggested answer |
| --- | --- | --- |
| What have you actually built so far? | Owen | We have inspected the dataset and reproduced the organizer's starter example on Linux. Our general repair agent and public-case evaluation are scheduled for Demo 2; supporting records are in the evidence archive. |
| What is your contribution beyond calling an LLM? | Owen | The proposed contribution is the system around the model: selecting useful evidence from long logs, connecting diagnostics to source and packaging, choosing bounded tools, remembering failed attempts, and enforcing budgets. We will isolate an improvement with an ablation and compare it with the frozen Demo 2 prototype. |
| Why is cross-architecture repair difficult? | Owen | The visible error may come from source assumptions, compiler behavior, dependencies, tests or packaging. A useful agent must identify the causal layer and inspect relevant files rather than treating the final error line as the cause. |
| How will an agent with no network access call a model or rebuild? | Owen | The submitted agent uses ModelClient and a Validation/Feedback Adapter. A separate research harness supplies model access and validation outside the sandbox through a proposed bridge. The hosted interface is still unconfirmed. We will report this compatibility gap rather than claim that our local harness is the hosted platform. |
| What if the hosted platform provides no build feedback during repair? | Owen | The agent makes a bounded candidate using the initial log and available files. Final clean-build validation and artifact checks still determine success. We record whether an experiment had in-loop feedback so comparisons use matching access. |
| Does a passing build prove that the patch is correct? | Joonseo | It proves the benchmark's build-and-artifact outcome. It does not prove unrestricted semantic correctness. We preserve required tests and artifacts and do not use disabling checks as a general repair strategy. |
| Why AWS, and are those environments ready? | Owen | AWS is our planned environment for course-scale x86_64 and ARM64 experiments. We will provision and verify those environments before running comparisons. Before comparisons we will pin the instance type, machine image, Docker version, container images and configuration. AWS does not itself reproduce historical dependencies or resolve the hosted interface. |
| Why evaluate 40 cases when 200 are available? | Joonseo | Forty is our planned course evaluation target: ten development cases and thirty held out from prompt and strategy tuning, balanced by direction and grouped by package. The purpose is a manageable, reproducible comparison. It is not an estimate of hidden leaderboard performance. We disclose any justified scope revision. |
| What is the baseline? | Joonseo | We will inspect and attempt the organizer-linked baseline at a recorded commit. If no compatible general-agent baseline is available, we document that and use a clearly named one-shot LLM comparator. Later versions are also compared with the frozen Demo 2 prototype under the same settings. |
| How will you show a real improvement rather than luck? | Joonseo | Each compared configuration runs three times on all ten development cases. We report every case and regression. The repair target is one additional repair per ten cases on average, with positive net gains in at least two paired runs. The alternative is at least 20% lower aggregate tokens while retaining previously repaired cases and having at least one verified repair overall. |
| Are 30k tokens, 30 minutes and three builds competition limits? | Joonseo | No. They are provisional research caps to calibrate before comparison, alongside five candidates and repeat limits. We follow stricter organizer limits where applicable. We will select the model and set the spending budget before freezing comparison settings. |
| What happens if you miss a milestone? | Joonseo | We show the measured result, explain the failure or infrastructure blocker, and announce a justified revision. We do not remove failed cases from the main denominator or relabel a missed target as success. |
| What is the biggest immediate technical risk? | Owen | Turning one public Debian source package and its historical log into a reproducible validator case, and confirming the permitted model and feedback interfaces. Those are prerequisites to meaningful agent evaluation. |

## Rehearsal plan

1. Owen presents slides 1–4, reaching the handoff at **4:50**. Joonseo presents slides 5–8, finishing at **9:40**.
2. Explain the architecture in component order using the same names as the proposal. Emphasize optional in-loop feedback and mandatory final validation.
3. In the first full run, time every slide. In the second, remove repeated explanations until the total is under ten minutes without rushing.
4. Rehearse questions with the diagram and evidence files closed. If an answer depends on work that is pending, say so directly.
5. Keep the final PDF available as a presentation fallback. During Q&A, Owen leads on architecture and infrastructure; Joonseo leads on evaluation, milestones and result interpretation. Either can add one short clarification.

## Submission checklist

The Demo 1 proposal and slides belong on `demo-1` by noon on September 23. Rehearse the 9:40 presentation and prepare for five minutes of Q&A. The two confidential quiz questions are prepared separately and must be emailed to the instructor by 5:30 pm on demo day. Keep them outside the public repository.
