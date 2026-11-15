# Demo 3 Agent

## Why this version

Our demo milestones were completed earlier to meet the competition
deadline of November 14, 2026. Demo 3 preserves full-agent 0.3.2 for
that historical demonstration rather than substituting newer development.

Source commit: `feaa5714d9a4c9738dee46c9190e4d81d05c43af`.
Evaluation findings: `7774267f14b2eaafecbf11332660e4d223fa1b75`.

This is not a claim that 0.3.2 is the latest or best-performing agent.

## Separate agent versions

- `agents/full-agent/` retains the branch's current agent.
- `agents/full-agent-v032/` contains the historical 0.3.2 agent.
- The current agent's tests remain in `tests/full-agent`.

These agent paths are under `buildbench-starter-kit-0.1.0-rc.5/`.

Select the historical agent using `--agent full-agent-v032`.
The existing `--agent full-agent` option continues selecting the current
agent. Run different versions in separate processes.

The harness treats an absent `use_no_build_control` setting as false
for compatibility with 0.3.2.

## Historical evaluation

The recorded result was **4 of 11 frozen local cases repaired**, with
**four x86 cases undetermined under QEMU**. This is historical evidence,
not a new Demo 3 evaluation.

## Demo configuration

Use `python3 -m harness.run_case` with
`--agent full-agent-v032` and `--live`.

Export these environment settings before running:

    export BB_AGENT_USE_EVIDENCE_MANAGER=1
    export BB_AGENT_USE_TOOLS=1
    export BB_AGENT_USE_BUILD_FEEDBACK=0
    export BB_AGENT_USE_HISTORY=1
    export BB_REPAIR_STRATEGY=default
    export BB_REVIEW_EDITS=0

Use a fresh run ID and results directory. The agent receives no iterative
build feedback; the harness still performs final build validation.

Do not use `scripts/demo2_demo.py` for this configuration: it selects
the current agent and explicitly enables build feedback.

## Validation so far

Before relocation, the restored historical agent passed 490 discovered
tests on the Mac. The historical commit reports 524 tests; that count
difference remains unexplained.

After the harness compatibility fix and its regression test, 80 harness
tests passed. These results do not establish a new live repair result.
