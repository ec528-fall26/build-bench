# Full Build-Bench Agent

## Purpose
The project's full agent: it reads the failure, investigates the package with
read-only tools, proposes a repair with a stated hypothesis, tests it when build
feedback is available, and revises. Plan: `docs/full-agent.md`.

Experimental Rust FFI coverage: set `BB_FFI_ANALYSIS=1` and
`BB_AGENT_USE_BUILD_FEEDBACK=0` to trace C-char parameters and simple pointer
returns, resolve explicit aliases/imports, recognize `CStr::from_ptr`, review
simulated repairs, and record coverage of actual edits (`ffi-contracts-v2`). This automatically
enables edit review and replaces broad Rust signedness pattern advice. It is a
bounded source analysis, not a compiler or proof of build success. Default is off.
Implementation scope and evaluation instructions: `docs/ffi-contract-analysis.md`
in the project repository. The environment flag must reach the agent process;
local exports do not change a hosted submission's defaults.
For an offline JSON coverage report without edits, models or builds, run
`python3 -m src.ffi_analysis --worktree /path/to/work/repo` from this agent directory.
The worktree must contain the unpacked source under `input/`. Macros, generics,
method dispatch and missing generated declarations remain unresolved.

## Status: wired; repaired rcran in 4 of 4 live runs (14 October)
All parts are connected (14 October): `main.py` diagnoses the failure log (Part A),
runs Part C's loop with Part E's prompts and parser, and writes
`agent-result.json` with every attempt, model turn and token count. Submitted,
it has no build feedback, so edits are investigated and revised but submitted
untested. Locally, `harness/run_case.py --agent full-agent` runs the same loop
with real builds (Part D). Results: `docs/part5-findings.md` §9c.

Version 0.5.1 (7 November), syntax guard (`src/syntax_guard.py`):
- In the 0.3.3 Development Validation run (111 small cases, 36 passed), about
  29 of the 69 failed cases ended on an error other than the original, several
  introduced by the agent's own untested edit (xmbmon "void value not
  ignored", sphde "expected primary-expression", pam-tmpdir, libmbim), and
  seven failed at an earlier stage than the original log.
- Before a batch is written, each edited file is checked as it was and as the
  batch leaves it; only problems the batch adds count: Python compile, sh -n,
  JSON, brace/parenthesis and #if/#endif balance for C, C++, Rust and Go,
  ifeq/endif balance and space-indented recipe lines in Makefiles and
  debian/rules, block balance in CMake, and `-fsyntax-only` parser errors on
  edited lines when a compiler is on PATH (missing headers stubbed; only files
  that parse cleanly before the edit). Compiler time is capped at 45 s per
  batch and 120 s per run.
- A finding sends that item back as a focused correction (`Reason:
  syntax_check`); an unchanged resend is applied, so a false alarm costs one
  correction turn and never blocks a repair. Switch: `BB_AGENT_USE_SYNTAX_GUARD`.
- Measure it on a recorded run without a model or a build:
  `experiments/syntax_guard_audit.py <run_public out> --cases-root <cases>`.

Version 0.4.0 (6 November), test-suite failures (`src/test_failures.py`):
- 59 of the 200 public cases fail in the package's own tests, the largest
  stage, and their diagnostics were wrapper lines (`dh_auto_test: error: ...
  returned exit code 13`). When Part A classifies the failure as `test`, a block
  after the errors now names each failing test and its message (Python
  unittest and pytest, automake and Autotest, CTest and GoogleTest, go test,
  cargo, TAP, Perl, meson, LLVM lit, Haskell), keeps crash, assertion and hang
  lines, the end of the test output, and shows the test's source from the
  worktree. Measured on the 200 public logs: 58 of 59 test-stage cases get a
  block (33 with named failing tests), none of the other 141.
- The block repeats the rules: no disabling, skipping or deleting a test, or
  ignoring its failure (rules.html section 6); it lists the usual architecture
  assumptions a test exposes.

Version 0.3.3 (4 November), from the Demo 2 feedback; no change in behaviour:
- `loop.py`: `run_loop` (634 lines, 68 `if`s) is now a thin wrapper around
  `_Loop`, one method per stage of a turn (budgets, request, model call, tool
  turn, review, and the six steps of an edit batch: merge a correction,
  validate and repair, review, apply, build, feedback). The longest method is
  70 lines; the stage order is documented in the class docstring;
- `tools.py`: one function per tool and a dispatch table (`_WORKTREE_TOOLS`)
  replace the 338-line `if` chain;
- `model_client.py`: `BB_MODEL_BASE_URL` (another Bedrock region) and
  `BB_MODEL_ID` (another model) are read for development runs; the hosted
  settings are unchanged. The one-shot baseline keeps its own copy of this
  file on purpose: `agents/one-shot/` is frozen (tag `one-shot-v1`) and a
  submission ZIP must be self-contained, so the two copies have diverged and
  cannot share a module.

Version 0.3.0 (2 November), for submitted runs with no build feedback:
- the model can read and search the whole failure log (`read_log`, `search_log`);
  the log excerpt it is shown carries line numbers;
- an edit correction shows the whole file (up to 24,000 characters) and may use
  tools before correcting;
- the system prompt lists common cross-architecture causes, and with no build
  adapter it says edits are not test-built and that `stop` submits them;
- `Budget.max_tokens` is 3,000,000 (was 100,000, which ended runs after about
  six turns; the website sets no token limit), `max_completion_tokens` is
  16,384, and the loop allows 60 turns and 120 tool calls (30 turns ran out
  after 3 to 6 minutes of the 17 in the first live no-build runs);
- an edit that an earlier edit in its batch already made (a broad replace_all
  followed by a narrower one) is dropped instead of sent back for correction;
- 8 edit attempts (was 5), and the playbook says where a Debian package's
  flags take effect (debian/rules, DEB_CFLAGS_MAINT_APPEND) and to change Rust
  C-string types to c_char in every file at once.

Version 0.3.1 (3 November), from the no-build live findings
(`docs/no-build-live-findings.md`), all in `src/edit_repair.py` and the loop:
- a batch is never lost to one item: when an item's correction budget is
  spent, or a correction repeats an already rejected old_text, that item alone
  is dropped and the rest of the batch is applied (rust-subversion: 26 edits,
  none written);
- an old_text that matches one place in the file apart from indentation is
  corrected to the file's whitespace when that is unambiguous (two tabs for
  three, consistently: libunwind sent that ten times); otherwise the
  correction shows the exact lines with tabs and spaces made visible;
- no-op edits and exact duplicates are dropped before any correction is asked;
- a `DEB_*_MAINT_*` line added at make level in debian/rules gets `export`,
  which it needs to reach any compile; a line inserted into a backslash-
  continued Makefile variable is rejected with the fix; a GNU make conditional
  in Makefile.am gets a warning;
- the prompts say the build output predates every edit and never changes, so
  the model stops once its edit addresses the cause instead of re-checking.

Version 0.3.2 (3 November, evening), from the live comparison of 0.3.1 with
0.3.0 (`docs/no-build-live-findings.md`, round 5):
- `src/patterns.py`: a recognised cross-architecture failure (Rust C-string
  signedness, an x86 intrinsics header on ARM, ARM outline-atomics helpers, a
  dpkg-gensymbols mismatch) is named right after the errors, with the files
  and lines gathered from the worktree and the usual repair; advice only;
- an edit whose old_text lives in exactly one other file of the same type is
  moved there instead of costing correction turns (16 wrong paths on
  rust-subversion);
- an edit that only removes `export` from a `DEB_*_MAINT_*` line is dropped
  with the reason instead of being re-exported into a no-op (37 turns on
  libunwind).
- an edit whose old_text is gone from the file and whose new_text is already
  there is dropped as already made, instead of being sent back for correction
  (rust-subversion: 58 turns after the repair was complete).

Live, without build feedback (round 6 in the findings): 0.3.2 repaired
rust-subversion, libunwind and rcran, one run each; rust-subversion for the
first time.

| File | Part | Status |
| --- | --- | --- |
| `contracts.py` | shared | Agreed 7 October. Change only by team agreement |
| `evidence.py`, `case.py` | A | Streaming diagnostics, source-path resolution, and read-only case metadata loading. See `docs/part-a-evidence.md` |
| `tools.py`, `path_checks.py`, `diff_tracker.py`, `tool_timeout.py` | B | Eight read-only tools, including `read_log` and `search_log` over the original failure log; called by the loop, which caps tool calls per case (`max_tool_calls`) |
| `build_feedback.py` | D | `NoBuildFeedback`: returns `None` until the organizers publish the protocol. The local build callback lives in the harness, never here |
| `loop.py`, `candidates.py` | C (Juliette Jacques) | The repair loop and candidate retention. See `docs/part-c-loop.md` |
| `model_adapter.py`, `main.py` | C (wiring) | The loop's `next_turn`: rebuilds each request from the loop's snapshot with Part E's text, one model call per turn. `main.py` is the entry point; `run_agent()` is shared with the harness |
| `prompts.py`, `reply.py`, `config.py` | E (Owen Zhang) | Instructions and per-turn messages, reply text → `ModelTurn`, and the ablation switches. Used every turn through `model_adapter.py`. See `docs/part-e-prompts-evaluation.md` |
| `model_client.py`, `log_tail.py`, `edit_applier.py`, `types.py` | baseline Parts 2–4 | Copied, not shared, so the baseline stays frozen |

Tests: `tests/full-agent/` at the repository root.

Part B arguments, limits, and Part C integration:
[Read-only tools guide](../../../docs/part-b-tools.md).

Part E prompts, reply parsing, switches and the Demo 3 comparison:
[Prompts and evaluation guide](../../../docs/part-e-prompts-evaluation.md).

## Model access
Configured through environment variables, never through files in this folder.

**Hosted** (competition platform; Starter Kit rc.5 model-access guide). The
platform injects all three; the agent uses them unchanged:

- `BB_MODEL_BASE_URL` — the organizers' endpoint, ending in `/v1`.
- `BB_MODEL_NAME` — the model alias, sent as `model`.
- `BB_MODEL_TOKEN_FILE` — a file holding the task credential.

Hosted requests send only `model`, `messages`, `max_completion_tokens`,
`store: false` and `n: 1`, with no retries and no proxy settings from the
environment. The run stops within `HOSTED_SECONDS` in `main.py` (17 minutes;
Hosted Smoke allows 20). Invalid settings, or no successful model call, end
with `status: "no_fix"`, the failing stage and exit code 1.

**Development** (local runs; used when `BB_MODEL_NAME` and
`BB_MODEL_TOKEN_FILE` are unset):

- `BB_MODEL_API_KEY` — Amazon Bedrock API key (required for a repair).
- `BB_MODEL_BASE_URL` — endpoint base URL (default: Amazon Bedrock's
  OpenAI-compatible endpoint in `us-east-1`).

The development model is fixed in code (`MODEL_ID` in `model_client.py`).
Without a key the agent still completes, makes no request and proposes no edits.

Check and package from the Starter Kit root (`buildbench-starter-kit-0.1.0-rc.5/`):

```bash
./bb check --agent ./agents/full-agent
./bb package --agent ./agents/full-agent
```

## Ablation switches
`src/config.py` reads one variable per `AgentConfig` field. Unset means on, which
is how the competition runs the agent; experiments set them to `0` to switch one
capability off. They take effect once Part C's loop calls `config_from_env()`.

- `BB_AGENT_USE_EVIDENCE_MANAGER`, `BB_AGENT_USE_TOOLS`,
  `BB_AGENT_USE_BUILD_FEEDBACK`, `BB_AGENT_USE_HISTORY`

## Runtime
Python 3.11. The current implementation uses only the Python standard library.

## Workspace
The workspace defaults to `/workspace`. For local testing, its location
can be overridden using the `BB_WORKSPACE` environment variable.

Required inputs:
- `input/task.json`
- `input/initial-build.log`
- `work/repo/`

Output:
- `output/agent-result.json`

A status of `completed` indicates that the agent finished running.
Repair success requires independent build and artifact validation.

## Entry point
Run `python -m src.main` from the agent directory.
