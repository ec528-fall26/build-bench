# Part E — Prompts and evaluation

**Owner:** Owen Zhang · **Last updated:** 13 October 2026
**Status:** prompts, reply parsing, ablation switches, the Demo 3 comparison
command and the ground-truth bundle tool are implemented and unit-tested.
**None of it has been run against the model or the validator yet**: the full
agent's loop (Part C) does not exist, so there are no full-agent runs to compare.

Plan: [`full-agent.md`](full-agent.md), Part E. Failure categories:
[`failure-taxonomy.md`](failure-taxonomy.md).

## Files

| File | Responsibility |
| --- | --- |
| `agents/full-agent/src/prompts.py` | Everything said to the model: the instructions, the opening evidence, and the replies to tool, edit and malformed turns |
| `agents/full-agent/src/reply.py` | Reply text → `ModelTurn`. The only code that knows the JSON-in-text format |
| `agents/full-agent/src/config.py` | `AgentConfig` from `BB_AGENT_USE_*` environment variables |
| `experiments/demo3_comparison.py` | One command: run every configuration on every available case and print the Demo 3 table |
| `experiments/validation_bundle.py` | Package recorded runs for Development Validation; count agreements afterwards |
| `tests/full-agent/test_prompts.py`, `test_reply.py`, `test_config.py` | Unit tests for the three agent modules |
| `tests/experiments/` | Unit tests for the two scripts (new CI step) |

(`agents/` is `buildbench-starter-kit-0.1.0-rc.2/agents/`.) Standard library
only. `contracts.py`, `main.py`, `model_client.py` and the frozen baseline are
unchanged.

## Prompts

`system_prompt(config, budget)` is the baseline's instructions plus the lessons of
the official baseline runs ([`part5-findings.md`](part5-findings.md) §9a):

| Lesson | Evidence | What the prompt says |
| --- | --- | --- |
| Any existing file under `input/` may be edited | `v1-rcran-3` declined, believing only the files it was shown exist | Files named in the build output and files never shown may be edited; a direct edit becomes a Debian patch automatically; not being shown a file is never a reason to stop |
| A log path is not a package path | `v1-rcran-2` copied `crc32c/crc32c_prefetch.h`; the file is under `src/` | Paths in build output are relative to where the compiler ran; use the "real file" path listed with an error or confirm with `find_files`; never copy a log path into an edit |
| Prefer direct source edits | All four baseline repairs were build-time commands in `debian/rules` | Fix the file that is wrong rather than adding commands that rewrite files during the build, with the reason |
| State a hypothesis and its evidence | A different fix every run | Every edit reply carries one falsifiable sentence and the evidence seen for it |

Design constraints, each pinned by a test:

- **Model-agnostic.** No model or vendor is named, no reasoning tags or native
  tool calling are asked for. The reply format is the one decided on 7 October.
- **No development-case content.** The plan's worked example is the rcran fix;
  putting it in the prompt would hand the model the one case we can measure. The
  format is shown with placeholders instead.
- **Each capability follows its switch.** Tools, build feedback and history are
  described only when on. With `use_evidence_manager` off the opening message is
  byte-for-byte the baseline's (same log tail, same nine files).
- **Small.** Every turn resends the conversation. Measured on rcran (the
  dataset's `target-failure.log` and unpacked source), all switches on:

  | | Baseline | Full agent |
  | --- | ---: | ---: |
  | Instructions | 835 characters | 5,477 characters |
  | Opening evidence | 37,636 characters | 5,201 characters |

  These are character counts. Token counts need a model call and have not been
  measured; the only measured figure is the baseline's 11,096 input tokens (§9).

With the evidence manager on, the opening message holds: Part A's errors in
order, each with its real path under `input/`; the source around the first
errors whose path resolved to exactly one file (read through Part B's
`read_file`); the log lines around the first error, found by its byte offset;
and `debian/rules` (the baseline's nine files when tools are off). Its limits
are the constants at the top of `prompts.py`. They are research settings to
calibrate on rcran together with the budgets.

## Reply parsing

`parse_turn(text, finish_reason=None, allow_tools=True) -> ModelTurn`. It never
raises; anything unusable is `ModelTurn(action="malformed", reason=...)` with a
reason written for the model, which `prompts.malformed_message` sends back.

| Tolerated (intent unambiguous, nothing lost) | Malformed (a guess could do the wrong thing) |
| --- | --- |
| Prose before or after the JSON; code fences; earlier drafts (the last `action` object wins) | No JSON object with an `action` |
| `<reasoning>`, `<think>` and `<thinking>` blocks | Unknown action or unknown tool |
| A literal newline or tab inside a JSON string | A `tool` reply without requests; `args` that is not an object |
| One request or edit object where a list was asked for | An edit without `path`, `old_text` or `new_text`, or with them empty |
| Missing `args` (becomes `{}`); line numbers sent as `"40"` or `40.0` | An edit reply without a hypothesis |
| The baseline's `{"edits", "rationale"}` format without an `action` | A `tool` request when tools are switched off |
| A complete object in a reply that was cut off afterwards | A reply cut off before its object was complete |

Decisions that go slightly beyond the plan's wording, for the team to confirm:

1. An edit reply with no `evidence` is accepted with an empty list. The
   hypothesis and the edits are enforced; evidence is recorded when given.
2. On a `tool` turn, `ModelTurn.reason` carries the model's `why`, so the run
   record can keep it. The contract's fields are unchanged.
3. An `edit` reply may carry an empty `edits` list (Part C's stop rule counts
   those); it is not malformed.

## Switches

One variable per `AgentConfig` field; unset means on, which is how the
competition runs the agent.

```text
BB_AGENT_USE_EVIDENCE_MANAGER   BB_AGENT_USE_TOOLS
BB_AGENT_USE_BUILD_FEEDBACK     BB_AGENT_USE_HISTORY      1/0, true/false, on/off, yes/no
```

Any other value raises: a typo must not silently run the wrong arm of an
ablation. The prompt lessons themselves are **not** behind a switch, because
`AgentConfig` has no field for them. With all four switches off the full agent
therefore still differs from the baseline in its instructions and reply format
(its evidence is identical). Measuring the lessons alone would need a fifth
switch, added by team agreement.

## Using it from Part C

```python
from src import prompts
from src.config import config_from_env, config_record
from src.reply import parse_turn

config = config_from_env()
messages = prompts.build_messages(context, config, budget, diagnostics=diagnostics,
                                  log_path=log_path, package=case.package)
# ... one model call: reply_text, finish_reason ...
turn = parse_turn(reply_text, finish_reason=finish_reason, allow_tools=config.use_tools)
messages.append({"role": "assistant", "content": reply_text})

if turn.action == "tool":
    results = [run_tool(request, worktree, diff_tracker=tracker) for request in turn.tool_requests]
    reply = prompts.tool_results_message(results)
elif turn.action == "edit":
    # apply through Part 4, request a build through Part D, record the Attempt
    reply = prompts.attempt_result_message(attempt, rejections=warnings, undone=undone,
                                           same_first_error=same_first_error,
                                           note=prompts.budget_note(budget, ...))
elif turn.action == "malformed":
    reply = prompts.malformed_message(turn, config)
messages.append({"role": "user", "content": reply})
```

For a fresh conversation after a failed attempt, call `opening_message` again
with the new build's diagnostics and `history=attempts` (ignored when
`use_history` is off). `history_block` shows the latest attempts with their
edits and older ones as one summary each; pass `kept=` once worse attempts are
undone, so the model knows which edits are still in the files.

## What Part E needs from Parts C and D

The comparison script calls the harness the same way for every configuration:

```python
harness.run_case.run_case(bundle, run_id, runs_dir, model=..., log=..., agent_dir=<agent folder>)
```

For the full agent's rows to be measurable, that call has to drive Part C's loop
with Part D's local build callback, and the run record needs:

| Row field | Used for |
| --- | --- |
| `attempts` (a list, or a count) | Attempts per case |
| `usage` totalled over the run, `null` when unknown | Tokens |
| `agent_config` = `config_record(config)` | Proving which arm a run belongs to |
| `termination_reason` | Why runs ended; failure taxonomy |
| malformed-reply and tool-call counts | Failure taxonomy; the plan's "measured, not guessed" |

Until `agent_config` is recorded, the report notes that the arm is known only
from where the run was launched. A row whose recorded switches contradict its
arm is reported as a problem and the target is not evaluated.

Three things noticed in other parts, not changed here:

- `read_file` requires `start_line` and `end_line`. A model that sends only
  `path` loses a turn; defaults in Part B would avoid that.
- `harness.run_case` writes a row with `termination_reason: null` when an
  unexpected error passes through it (seen locally: `dpkg-source` not installed).
  The comparison counts such rows as infrastructure failures.
- The harness records `mode: "one-shot"` for every agent folder.

## The Demo 3 comparison

```bash
# ARM64 host, repository root
export BB_MODEL_API_KEY=...
python3 experiments/demo3_comparison.py                 # run what is missing, print the table
python3 experiments/demo3_comparison.py --report-only   # print the table again
```

- **Cases:** every `~/buildbench-local-*` bundle, or `--bundle DIR` (repeatable).
  Today that is rcran only.
- **Same starting evidence:** each unrepaired case is built once; all runs of
  all configurations read that one failure log.
- **Configurations:** `one-shot-v1`, `full-history-on`, `full-history-off`; the
  last two differ only in `BB_AGENT_USE_HISTORY`. Each run is its own process.
- **Order:** repeat by repeat, so run *k* of every configuration happens close
  together (the proposal's paired runs).
- **Resumable:** recorded runs are skipped. A run that dies without a record
  gets a `run_crashed` or `run_timeout` row and stays in the denominator.
- **Output:** `<out>/<configuration>/runs.jsonl` in the harness's format (so
  `python3 -m harness.summarize` works on each), `<out>/experiment.log`, and the
  table on stdout (`--output FILE`, `--json FILE`). Default `<out>` is
  `~/buildbench-experiments/demo3-comparison`, outside the repository.

The table has, per configuration: runs, verified repairs, repairs in each
repeat and their mean, median tokens per run, median wall time, mean attempts,
and infrastructure failures. Below it: repairs per case and per migration
direction, termination reasons, fix types of the verified repairs (with the runs
whose diff must be read first), gains and regressions against the baseline, and
history on against off. Unknown values print as `-`, never `0`.

**Guards against a misleading table.** A configuration is not run when its
agent cannot give a meaningful number: the baseline if its fingerprint is not
`one-shot-v1`'s; the full agent while its `main.py` is still the baseline's
single attempt, which is the case today. It then appears as `not run`. The
target line is evaluated only when every configuration has all its runs, one
model, one agent version per arm, and all 10 planned cases; otherwise it reads
`not evaluated` with the reason. Rows from the stand-in model are labelled a
pipeline check.

**Pipeline check without a key or tokens** (the frozen baseline with the
harness's stand-in model):

```bash
python3 experiments/demo3_comparison.py --configurations one-shot-v1 --repeats 1 \
    --replay harness/replays/rcran-known-fix.json --out /tmp/demo3-check
```

**Time.** A run is bounded by the harness's own limits. Using the rcran
manifest's `timeout_seconds: 3600`, the bound is 125 minutes per run; measured
rcran runs took about two to three minutes (§9, harness README).

## Ground truth: spot-checking local verdicts

Development Validation rebuilds a repaired public case in the organizers'
environment. It is a web upload, one task per team at a time, so it checks a
sample; it cannot carry the evaluation.

1. **Choose and package the sample.** One run per case, alternating repaired and
   not repaired, in an order fixed by a seed so it is not hand-picked:

   ```bash
   python3 experiments/validation_bundle.py ~/buildbench-experiments/demo3-comparison/one-shot-v1 \
       ~/buildbench-experiments/demo3-comparison/full-history-on \
       --sample 4 --output development-validation.zip
   ```

   The ZIP follows the documented Worktree Bundle layout: a root
   `manifest.json` (`schema_version`, `suite_id`, `cases`) and
   `cases/<case_id>/worktree/input/` holding that run's rebuilt `.dsc` and
   archives, verified against the `.dsc`'s checksums. It writes
   `development-validation.checks.json` with each local verdict.
2. **Upload** on the website: Upload Worktree Bundle → Create Validation Run →
   select the cases. Needs the team account; check that the page still shows
   suite `development-recommended-v1` (`--suite-id` otherwise).
3. **Record** each official verdict in the check sheet (`official_repaired`,
   `official_status`, `validated_on`), then:

   ```bash
   python3 experiments/validation_bundle.py --compare development-validation.checks.json
   ```
4. **A disagreement** is investigated before any number is reported, and listed
   below either way.

Limits: only runs that reached a rebuilt package can be checked (a run with no
applied edit has nothing to upload); a bundle holds one run per case; the
official environment's tool versions can differ from the original logs'.

| Date | Runs checked | Agree | Disagree | Notes |
| --- | ---: | ---: | ---: | --- |
| — | 0 | — | — | Not yet performed: no full-agent runs exist, and it needs the team account |

## Checks

13 October 2026, macOS, Python 3.13.5 and 3.14.8; no model call, no build, no
network. CI runs the same steps on 3.11 and 3.12, which were not available
locally; the new files were checked for syntax newer than 3.10.

```bash
python3 -m unittest discover -s tests/full-agent -v      # 152 tests: 79 new for Part E
python3 -m unittest discover -s tests/experiments -v     # 44 tests
python3 -m unittest discover -s harness/tests -t . -v    # 58 tests, unchanged, incl. the frozen baseline
```

Also: the six CI steps pass in a copy holding only the folders CI checks out;
24 deliberate one-line breaks of the new code each made at least one test fail;
and the starter kit's submission checker accepts the agent folder with the
three new files (`python3 -m runner.check_agent --agent agents/full-agent`, the
module `./bb check` runs, here with host Python because this machine has no
Docker: 20 files, 107,621 bytes).

The tests cover: the lessons and baseline rules in the instructions; no model or
development-case content; every switch combination; that the tool examples in
the prompt are valid Part B requests; baseline-identical evidence with the
evidence manager off; bounded sizes; path safety of the source excerpts; the
parser's tolerated and malformed inputs; the comparison's arithmetic, guards,
resume and crash handling, including one real child process; and the bundle's
layout and checksum verification. The comparison tests use the harness tests'
`FakeHost` in place of `dpkg-source` and `run.sh`; their numbers are synthetic.

Not done, and what it takes:

| Not done | Needs |
| --- | --- |
| Any live reply to these prompts; the malformed-reply rate | Part C's loop and `BB_MODEL_API_KEY` |
| The comparison with real runs | Parts C and D; the ARM64 host |
| The table on 10 cases | The case environments the mentor agreed to provide |
| A Development Validation spot check | Recorded runs and the team account |
| Calibrating the prompt size limits and budgets | First full-agent runs on rcran |
