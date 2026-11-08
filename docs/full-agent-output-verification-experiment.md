# Full agent: output-verification experiment

**Status:** Experimental; initial campaign complete and three follow-up intbitset trials passed.
**Evidence cutoff:** Three completed reliability trials on 8 November 2026; see the follow-up section.
**Purpose:** Record implementation changes, configurations, validation results and limitations.

## Version and source provenance

- **Base agent version:** `0.5.1`, as previously confirmed in the EC2 checkout's `agent.yaml`.
- **Base repository commit:** `bc7f0f3` (merge of the syntax-guard work).
- **Experiment branch:** `experiment/full-log-context`. The initial optional full-log change was committed and pushed on this branch.
- **Current development copy:** `~/build-bench-full-log` on the experiment EC2. It was transferred as a source archive, rather than checked out there as a Git worktree.
- **Later changes:** Installed incrementally on the experiment instance using checked installer scripts with backups. The pushed branch alone does not represent all the changes described here.
- **Prepared prerelease version:** `0.6.0-rc.1` on the Mac branch. Commit, push, and release publication are separate user-controlled steps. The running EC2 campaign keeps its original `0.5.1` metadata and saved code snapshot.
- **Working description:** “v0.5.1-based output-verification experiment.” This is a descriptive label, not a release identifier.

The campaign script saves a code archive, source hashes, settings, and the script used to launch it. Those artifacts identify the tested implementation more precisely than the unchanged version field. Keep source files unchanged while the campaign runs.

## Problem being addressed

The original agent sometimes eliminated an immediate error without preserving the output that the failing command was intended to produce.

For `python-intbitset`, the original failure involved:

```make
python3 setup.py build_sphinx
```

The package's install manifest requires `docs/_build/html/`. Earlier generated repairs substituted `:`, `dh_auto_build`, or `dh_sphinxdoc` without establishing a command that generates those HTML files. Validation then failed at `dh_install` because the directory was missing.

The model repeatedly assumed that enabling the `sphinxdoc` debhelper sequence would generate the documentation. Source excerpts and a command reference were supplied, but ordinary review still approved proposals based on that unsupported assumption.

## Changes and reasons

Paths in this table are relative to `buildbench-starter-kit-0.1.0-rc.5/agents/full-agent/src/`.

| Change | Main implementation areas | Reason and observations |
| --- | --- | --- |
| Optional full initial build log | `prompts.py` | Tests whether selected diagnostics omit necessary information. Bounded by a configurable byte limit. The tested ectrans comparison did not demonstrate a repair benefit. |
| Optional general repair guidance | `prompts.py` | Asks for cause-directed repairs that preserve required functionality. Guidance alone did not repair ectrans in the tested comparison. |
| Optional CMake source context | `evidence.py`, `source_context.py` | Adds bounded source references around imported-target errors. Intended to improve evidence retrieval without embedding a case-specific patch. No repair benefit was demonstrated on ectrans. |
| Optional packaging source context | `source_context.py`, prompt integration | Supplies relevant Debian install manifests, build configuration, and inferred producer Makefile excerpts without requiring a model tool request. Exposes required files and potential producers. |
| Context-collection refactoring | `source_context.py`, `evidence_support.py` | Separates context collection and shared helpers from the diagnostic parser; reduces mixed responsibilities. |
| Edit-validation cleanup | `loop.py` | Extracts smaller validation and rejection-handling steps while preserving behavior. |
| Build-step no-op guard | `rules_guard.py` | Rejects a recognized build-producing command being replaced with a no-op. This is a narrow static check, not a general proof that all required outputs are preserved. |
| Accurate rejection feedback | `prompts.py` | Explains controller rejection reasons instead of implying that every zero-edit result arose from an incorrect path or text anchor. |
| Command reference and packaging review instructions | `command_evidence.py`, `proposal_review.py` | Distinguishes documentation generation from packaging/post-processing and asks review to connect required output paths to producer commands. |
| Review protocol corrections | `prompts.py`, `model_adapter.py`, `proposal_review.py`, `loop.py` | Makes approval an explicit available action; requires complete replacement batches for revisions; explains malformed responses; detects unchanged resubmissions and limits repetition. |
| Optional fresh review context | `model_adapter.py` | Builds a fresh review request containing the original failure, source evidence, and pending patch without the failed-attempt narrative. Fresh context alone did not solve intbitset. |
| Independent output-verification gate | `output_verification.py`, `model_adapter.py`, `loop.py` | Uses a separate structured model call to assess whether the pending repair preserves affected required outputs. Initial scope is complete batches editing `input/debian/rules`. |
| Persistent verification feedback | `loop.py`, `model_adapter.py` | Retains the latest unresolved assessment across repeated proposals, malformed responses, tool reads, and focused edit corrections. Earlier one-turn feedback disappeared before later decisions. |
| Verification before ordinary review | `loop.py` | Checks a valid proposal before ordinary review can replace it. The current candidate's supported assessment is then shown to the reviewer. Revisions restart verification, and explicit approval is still required. |
| Regression tests and experiment tooling | `tests/full-agent/` and standalone scripts | Test state transitions and preserve exact requests, replies, candidate diffs, settings, and provenance. Separate prompt experiments, autonomous model-only runs, saved-candidate validation, and fresh end-to-end evaluation. |

## Current verification and review flow

For batches within the gate's scope:

1. The agent proposes a patch using original failure evidence and permitted source inspection.
2. Existing preflight checks and enabled static guards run.
3. A separate model call verifies the exact pending batch against packaging output requirements and producer evidence.
4. The controller records one of `supported`, `contradicted`, `insufficient_evidence`, or an invalid-response outcome.
5. Only a supported assessment makes the pending batch eligible for application after ordinary review explicitly approves it. Support alone never applies an edit.
6. Ordinary review sees the current patch and its assessment. A revision passes through checks and verification again.
7. The harness performs final package validation after the agent finishes.

An approval after an unsupported assessment cannot bypass verification. Invalid, truncated, or internally inconsistent verification responses do not authorize application. A changed source snapshot invalidates the previous approval path and requires renewed checking.

Verification consumes normal model-turn, token, and time budgets. The implementation stops after three failed output checks; other existing attempt, repetition, and turn limits can stop it earlier. No budget increases were made to force a success.

The verifier checks a structured evidence chain: required output, declaring manifest, replacement command, working directory, producer evidence, and unsupported assumptions. Schema checks validate response structure and internal consistency; they do **not** establish that every citation or semantic claim is true.

## Build-feedback boundary

The experiment keeps `BB_AGENT_USE_BUILD_FEEDBACK=0`.

- The agent can use the original failing build log and source files.
- Verification assessments are model judgments based on that evidence, not observations from a new build.
- Model-only probes do not invoke Docker or package builds.
- Normal harness runs perform final validation, but its result is not fed back into the same repair loop.
- Existing static guards remain enabled; “no build feedback” does not mean that all static checking is disabled.

## Active campaign settings

```text
BB_AGENT_USE_BUILD_FEEDBACK=0
BB_AGENT_USE_EVIDENCE_MANAGER=1
BB_AGENT_USE_TOOLS=1
BB_AGENT_USE_HISTORY=1
BB_AGENT_USE_NO_BUILD_CONTROL=1
BB_AGENT_USE_RULES_GUARD=1
BB_AGENT_USE_SYNTAX_GUARD=1
BB_REPAIR_STRATEGY=default
BB_FFI_ANALYSIS=0
BB_REVIEW_EDITS=1
BB_EXPERIMENT_LOG_MODE=selected
BB_EXPERIMENT_LOG_MAX_BYTES=200000
BB_EXPERIMENT_PACKAGING_CONTEXT=1
BB_EXPERIMENT_CMAKE_CONTEXT=0
BB_EXPERIMENT_REPAIR_GUIDANCE=0
BB_EXPERIMENT_FRESH_REVIEW=1
BB_EXPERIMENT_OUTPUT_VERIFICATION=1
```

Full-log context, CMake context, and general repair guidance remain available experiments but are not enabled in this campaign. The output gate defaults off outside explicitly configured experiments. It currently requires review and packaging context to be enabled.

## Evidence collected so far

### Unit and integration tests

The complete EC2 full-agent suite passed **664 tests** with fresh review disabled and again with fresh review enabled. Gate-specific tests explicitly enable the gate in their fixtures; the ordinary suite is not globally forced through verifier calls.

Covered behaviors include explicit approval, independent verification, rejection persistence, rechecking revised patches, source-change invalidation, malformed assessments, API failures, and budget exhaustion without applying a pending patch.

Passing these tests demonstrates the tested controller behavior, not general repair accuracy.

### Model-only verification comparison

Three candidate replacements were assessed against the same evidence. In the three-repeat comparison:

| Replacement | Outcomes |
| --- | --- |
| `dh_auto_build` | One `contradicted`; two `insufficient_evidence` |
| `dh_sphinxdoc` | Three `contradicted` |
| `$(MAKE) -C docs html` | Three `supported` |

No bad replacement was marked supported in those nine calls. This was a narrow, case-specific experiment, not an independent multi-case benchmark.

### Failed integrated probes and resulting fixes

- Fresh ordinary review still approved `dh_auto_build` despite receiving the relevant source and command reference.
- The first output-gate probe rejected bad proposals but stalled. Its rejection feedback disappeared after one response; persistence was fixed.
- With persistent feedback, the agent proposed `$(MAKE) -C docs html`, but ordinary review replaced it with a no-op before output verification could assess it.
- This motivated moving output verification ahead of ordinary review rather than increasing retry budgets.

### Successful autonomous selection and exact-patch validation

The model-only run `verify-before-review-20261108T020010Z` independently proposed, verified, approved, and applied:

```diff
-\tpython3 setup.py build_sphinx
+\tcd docs && $(MAKE) html
```

Here `\t` denotes the Makefile recipe's tab indentation.

That run used seven model calls across six model turns, applied one edit, and stopped normally. No package build occurred during selection.

The saved proposal was then matched against the probe's exact final diff and replayed on original source for validation:

- Validation directory: `saved-candidate-20261108T020403Z`
- Result: **REPAIRED (`build_succeeded`)**
- Applied edits: **1/1**
- Duration: **959.9 seconds**
- Additional live model calls during replay: **0**

This demonstrated that the autonomously selected patch built successfully. The replay itself was a separate validation step, not a fresh autonomous repair run.

### Successful fresh end-to-end run

A subsequent normal harness run supplied no saved candidate:

- Campaign directory: `output-gate-e2e-20261108T022136Z`
- Run ID: `intbitset-output-gate-e2e-01`
- Result: **REPAIRED (`build_succeeded`)**
- Applied edits: **1/1**
- Duration: **985.5 seconds**
- Agent termination: **`model_stop`**
- Attempts: **2**
- Model turns: **5**
- Loop builds: **0**

This is a fresh end-to-end success for a case that failed in the original v0.5.1 campaign. It does not isolate which individual change caused the improvement or establish general reliability.

All experiment directories above are under `~/buildbench-experiments/` on the experiment EC2.

## Current all-case rerun

The campaign launched with `run_output_gate_campaign.sh` processes these 11 cases sequentially:

| Case | Original reported outcome | Current rerun |
| --- | --- | --- |
| rcran-01 | Repaired in a separate preliminary run | Pending |
| libbpp-core | Not repaired | Pending |
| ectrans | Not repaired | Pending |
| libmceliece | Not repaired | Pending |
| libbpp-qt | Repaired | Pending |
| python-intbitset | Not repaired | Pending; separate updated end-to-end success recorded above |
| libunwind | Repaired | Pending |
| rust-subversion | Repaired | Pending |
| libevtx | Not repaired | Pending |
| html-xml-utils | Already builds unchanged (`case_not_failing`) | Pending |
| pybigwig | Not repaired | Pending |

Original results total four repaired cases including the separate Rcran run, six unrepaired cases, and one case that already built unchanged. Keep the already-building case separate from repair failures when reporting comparisons.

The new campaign reuses saved original baseline logs where available and generates a baseline otherwise. It always rechecks the previously non-failing `html-xml-utils` baseline. Each case starts from its original bundle; saved repair candidates are not supplied.

The campaign saves:

- `settings.txt`, `campaign-metadata.json`, and source hashes;
- `agent-code.tar.gz` and `campaign.sh`;
- `cases.txt`, `progress.log`, and `campaign-exit-codes.tsv`;
- original log copies where supplied, per-case console logs, and harness artifacts.

The current results path is written to:

```text
~/buildbench-experiments/latest-output-gate-campaign.txt
```

A harness exit code of zero does not by itself mean a repair succeeded. Use the recorded verdict and final validation results. Infrastructure errors and already-building cases should be classified separately.

## What remains before release

1. Finish the campaign and record all case outcomes, failures, regressions, model usage, and runtimes.
2. Inspect repairs for preservation of required functionality; build success alone is not a complete semantic review.
3. Assess whether gains extend beyond intbitset and whether previously repaired cases remain repaired.
4. Review the accumulated source diff, remove development-only backups from the intended commit, and reconcile the EC2 changes with the Git branch.
5. Decide which experiments to retain and which settings should remain opt-in.
6. Assign the final version number and commit the reviewed code, tests, and documentation together.

**Current conclusion:** The new workflow has a confirmed fresh end-to-end intbitset repair with no iterative build feedback. Broader effectiveness and regression results remain pending.

### Mac validation
The Mac checkout ran 664 tests: 662 passed and two existing syntax-guard
compiler tests failed under Apple Clang. Both syntax_guard.py and its tests
are unchanged from HEAD. The failures expose GCC-specific diagnostic handling.
On EC2, all 664 tests passed with fresh review disabled and enabled.

## Reliability follow-up: 8 November 2026 (0.6.0-rc.2)

This section supersedes earlier pending-campaign statements. The initial
`output-gate-all-20261108T024354Z` campaign repaired rcran, libbpp-qt,
libunwind and rust-subversion: the same four successes as the baseline.
Six cases were unrepaired; html-xml-utils already built. Ectrans stopped in
ordinary review, while intbitset and pybigwig stopped in output verification.
There was no aggregate repair gain or loss in that single campaign.

The follow-up separates malformed verifier responses from semantic rejection,
adds one globally bounded format-correction retry, broadens producer evidence
beyond optional packaging manifests, and records paired exact requests and raw
responses when `BB_MODEL_TRACE` is configured. Explicit approval, existing global
budgets and experimental defaults remain unchanged. These changes do not
establish that ectrans's ordinary review stall has been fixed.

### Fresh autonomous intbitset trials

Results directory:
`/home/ubuntu/buildbench-experiments/intbitset-reliability-20261108T043648Z`.

| Trial | Verdict | Applied edits | Attempts | Model turns | Loop builds | Total seconds |
| --- | --- | --- | --- | --- | --- | --- |
| intbitset-reliability-01 | build_succeeded | 1/1 | 2 | 9 | 0 | 1014.9 |
| intbitset-reliability-02 | build_succeeded | 1/1 | 2 | 11 | 0 | 1011.2 |
| intbitset-reliability-03 | build_succeeded | 1/1 | 2 | 13 | 0 | 1036.2 |

Each trial used a fresh source tree and new model calls, with the saved original
failure log and final harness validation. No candidate was replayed and no
iterative build feedback was supplied. The first trial's patch replaced
`python3 setup.py build_sphinx` with `$(MAKE) -C docs html`; the final verdicts
for all three trials were independently reported as repaired. This section does
not claim that all three diffs are identical without inspecting them.

Configuration: review, packaging context, fresh review and output verification
were enabled; build feedback, CMake context, full-log mode, repair guidance and
FFI analysis were disabled. Tools, history, evidence manager, no-build control,
rules guard and syntax guard were enabled. Repair strategy was `default`, log
mode `selected`, and log byte limit 200000. Per-run request trace paths were
configured. Preserve `settings.json`, records, console logs, diffs, build logs
and paired traces with the source snapshot.

All 670 tests passed on EC2 with fresh review off and on. The earlier two Mac
syntax-guard compatibility failures remain a separate known limitation; no new
Mac full-suite pass is claimed. Bootstrap Python dependency warnings also
occurred in earlier successful runs; the harness's dependency recovery allowed
validation to proceed. They were not terminal failures in these trials.

Three successes demonstrate repeatability on this case under these settings,
not a reliable population success rate. Multiple changes were tested together;
these results do not isolate their causal contribution. Next: inspect saved
traces and evaluate pybigwig and other failures before promoting the gate.

The experiments used EC2 source before this Mac metadata update. The rc.2 label
identifies the prepared code/docs revision; it does not relabel historical
records. No stable release or Git tag is implied.
