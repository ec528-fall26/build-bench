# Design Document — Demo 2 one-shot repair pipeline and evaluation

**Team:** Anthony Capraru, Austin Li, Joonseo Moon, Juliette Jacques, Owen Zhang
**Mentor:** Minghua Ma, Microsoft
**Demo 2 evaluation snapshot:** 19 October 2026. **Documentation and readiness update:** 20 October 2026. Due on `demo-2`; update for Demo 3 and the final.


This document describes the implemented local research pipeline through commit `001f3b9`, not only the original proposal. The main Demo 2 deliverable is the frozen one-shot repair pipeline and its public-case evaluation. The iterative full agent is a separate extension. TA entry points are `scripts/demo2_demo.py` for individual demonstrations and `scripts/demo2_retest.py` for the complete retest. The entry point adds reproducibility checks and records; it does not change either repair agent. The handoff files are committed on `demo-2`; record the exact checkout used by the TA. Each run records its actual Git commit, source hashes, settings and outcome. Do not imply that results from earlier versions were produced by the current version.

**What works:** a real package can be unpacked, inspected, edited, repackaged and validated on ARM64. There are recorded live-model rcran repairs and a scripted full-agent failed-build/revision/success demonstration. **What does not yet work reliably:** autonomous repair of the harder development cases, including Rust Subversion. An assisted Rust repair builds successfully, but it is not an autonomous success. **The latest one-shot retest completed all ten development cases:** zero repairs, five failed candidate builds, four runs with no edit applied, and one case that already built unchanged. Rcran was tested separately and also failed in this repeat. See [the completed one-shot results](demo2-one-shot-results.md). The full-agent retest also completed all eleven evaluations and repaired rcran autonomously (1/11 overall, 0/10 bundle). Log review found all five reverse-direction cases blocked during environment setup in both arms. The four source-evaluable development cases remained unrepaired. See [the paired diagnostic review](demo2-comparison-diagnostics.md) for the complete case table, evidence and remaining work.

**Hosted compatibility update (20 October):** The organizer guide now specifies the hosted model channel: `BB_MODEL_BASE_URL`, `BB_MODEL_NAME`, and a short-lived credential read from `BB_MODEL_TOKEN_FILE`. Local `bb test` and `bb ready` do not inject this access. On 20 October our rc.2 one-shot readiness test made no model call because it received no API key, so it did not pass. On 21 October, with a local runner change that forwards the model settings and enables the agent network, both agents passed local `bb ready` on `hello` (§4.5). That is a modified local test, not hosted qualification. The hosted adapter and Hosted Smoke remain unverified. The official platform does not offer agent-requested builds during a run, so the local full-agent build loop is not a demonstrated hosted capability. These findings do not change the October 19 repair counts. See §§1.5 and 4.5–4.6.

## 1. Architecture

### 1.1 Main Demo 2 architecture: the frozen one-shot pipeline

```mermaid
flowchart TD
    A[Released case: original Debian source, manifest and locked dependencies] --> B[Harness runs original target build]
    B -->|already succeeds| X[Record case_not_failing; no repair credit]
    B -->|fails with build log| C[Unpack a fresh work/input tree]
    C --> D[Prompt: last 500 log lines plus fixed package files]
    D --> E[One-shot model: one repair proposal]
    E --> F[Parse edits and rationale]
    F --> G[Validate and apply exact permitted replacements]
    G -->|no edits applied| N[Record no_edit_applied]
    G -->|changes applied| H[Save repair.diff; repack Debian source]
    H --> I[Unchanged local validator: clean target build]
    I --> J[Check status AND expected binary artifacts]
    J --> K[Record repaired or failed, usage, time and evidence]
```

The one-shot pipeline is a complete LLM repair system, not a replay of the assisted Rust fix. It supplies an actual failing case to the model, applies the model's proposed source changes, and determines the outcome through an independent build. A negative result is still a completed evaluation when the run is recorded correctly. There is no minimum repair-rate promise for Demo 2.

#### What the frozen agent sees and does

1. `harness/run_case.py` reads the supplied case manifest and first runs the unmodified case. An already-building case is recorded as `case_not_failing`; it is not presented as a repair. Missing or invalid validator output is an environment error.
2. The harness runs `dpkg-source -x` into a fresh `work/input/`, keeping the upstream archive beside the tree for later repacking. The patch policy comes from the case, rather than from a model-selected path.
3. The one-shot agent receives the **last 500 log lines** and a predetermined source-file list. It does not run the full agent's diagnostic retrieval or tools. The fixed files are `input/debian/rules`, `input/debian/control`, `input/debian/patches/series`, `input/CMakeLists.txt`, `input/configure.ac`, `input/Makefile`, `input/setup.py`, `input/Cargo.toml`, and `input/meson.build`. Each file is bounded at 8 KiB; total file evidence is bounded at 32 KiB. Missing/truncated content is labeled. The log-tail line limit is not an equivalent byte/token cap: the libunwind baseline used over 104,000 input tokens in one call.
4. `agents/one-shot/src/model_client.py` requests one proposal from the configured model. Its output is a JSON object containing `edits` and `rationale`, rather than the full agent's `tool/edit/approve/stop` protocol. HTTP transport retries are not repair iterations: the agent never uses a failed candidate build to generate a second repair.
5. Each proposed edit contains `path`, `old_text`, and `new_text`. The frozen applier requires a unique literal match in an existing permitted UTF-8 file under `input/`. It rejects unsafe paths, missing or ambiguous text and unsupported changes. The later full-agent replace-all/review features do **not** modify this baseline. Rejections are recorded; the harness records proposed and applied counts separately.
6. If no edit applies, the run ends with `no_edit_applied`. Otherwise the harness saves the actual source diff before repacking. Some edits in a multi-edit proposal may be rejected; the final verdict concerns the changes actually applied.
7. The harness repacks with `dpkg-source --auto-commit -b`, assembles a case with original manifest/configuration/dependencies and modified source artifacts, and runs the supplied validator. This is necessary for Debian quilt packages: editing an unpacked file alone does not create the source package the local validator consumes.
8. Only a successful clean build **and** passed expected-artifact validation count as a repair. Every run, including a refusal, rejected proposal, failed build or infrastructure error, is recorded. There is no repair retry in one-shot.

For rcran, the original failure is an x86-only `xmmintrin.h` include enabled on ARM64. Recorded one-shot successes modified `debian/rules` to guard or remove that include before compilation. Those are model-generated build-time changes, distinct from the hand-authored direct-source replay fixture. The harness flags file-modifying build commands for manual review; a passing package alone does not establish ideal patch quality.

#### One-shot modules and boundaries

| Component | Code | Interface / responsibility |
| --- | --- | --- |
| Entry and repair orchestration | `agents/one-shot/src/main.py` | Read context, obtain one proposal, apply permitted edits; no build-feedback loop. |
| Failure evidence | `agents/one-shot/src/log_tail.py` | Extract the bounded-line log tail. |
| Model access and fixed evidence | `agents/one-shot/src/model_client.py` | Construct fixed prompt, call endpoint, parse edit plan and report usage. |
| Edit application | `agents/one-shot/src/edit_applier.py` | Literal replacements, path/file checks and rejected-edit logging. |
| Evaluation harness | `harness/run_case.py` | Original build, fresh extraction, actual diff, repack, clean final build and run record. |
| Outcome interpretation | `harness/record.py` | Require successful status and artifact check; do not use `patch_applied` as a success flag. |
| Repeated evaluation | `scripts/demo2_retest.py` | Fixed case/agent schedule, separate evidence directories, resume without hiding completed failures. |

Agent paths in this document are relative to `buildbench-starter-kit-0.1.0-rc.2/`. The baseline tag is `one-shot-v1`. Before the live retest the driver checks that `agents/one-shot/` has no tracked difference from that tag and records source hashes. It does not insert a known repair or change the baseline prompt. The supplied reference fix is used only by explicitly named replay controls.

### 1.2 Local research extension: iterative full-agent architecture

```mermaid
flowchart TD
    A[Same original case and failure evidence] --> E[Structured diagnostics and source retrieval]
    E --> M[Model action]
    M -->|tool| T[Read-only inspection]
    T --> M
    M -->|edit| P[Snapshot and sequential preflight]
    P -->|invalid| C[Focused correction]
    C --> M
    P -->|optional review| R[Pending diff, counts, coverage hints]
    R --> M
    M -->|approve pending batch| W[Revalidate and apply]
    P -->|review off| W
    W --> B[Copy, repack and request target build]
    B --> H[Attempt history and candidate retention]
    H -->|failure and budget remains| M
    H -->|success or stop| V[Separate final clean validation]
```

This extension adds structured diagnostics, tool use, multiple candidate builds, history and optional pre-build review. These features address failure modes observed in one-shot; they are not necessary to describe the baseline's operation, and their effectiveness must be measured separately. No model weights are trained during either workflow.

### 1.3 Full-agent components and contracts

| Component | Implementation | Responsibility |
| --- | --- | --- |
| Case materialization | `harness/run_case.py`, `harness/case_views.py` | Load a released environment; unpack the Debian source package into a fresh `work/input/`; retain original source and policy. |
| Diagnostics | `agents/full-agent/src/evidence.py` | Stream log evidence; remove validator timing prefixes; preserve Rust expected/found notes and locations; resolve compiler paths against actual source. |
| Tools | `agents/full-agent/src/tools.py` | Bounded read-only listing, finding, searching, file reading and inspection. The model receives labeled results and truncation notices. |
| Model adapter | `model_client.py`, `model_adapter.py`, `reply.py`, `prompts.py` | OpenAI-compatible HTTP requests, structured JSON actions, follow-up for malformed responses, prompt composition, token accounting and raw reply records. |
| Controller | `loop.py`, `contracts.py`, `candidates.py` | Budget checks, attempts, build feedback, history, candidate selection and snapshot restoration. |
| Edit validation | `edit_applier.py`, `edit_preflight.py`, `path_feedback.py` | Existing permitted UTF-8 files only; exact literal edits; sequential simulation; actionable rejection feedback. |
| Optional proposal review | `proposal_review.py` | Present pending diff, measured match counts, retained diagnostics and cross-file search hints before applying an approved batch. |
| Build adapter | `harness/build_feedback.py` | Copy candidate, repack its Debian source, run the organizer's local validator, parse feedback without modifying the agent's retained tree. |
| Results | `harness/record.py`, `harness/summarize.py` | Independent final verdict, append-only run records, timing, usage, failure reasons and diff classification. |

Agent module paths above are relative to `buildbench-starter-kit-0.1.0-rc.2/`. The harness is outside the submitted agent. Its Docker access and local credentials are host-side development facilities; the current organizer contract does not provide an in-run official build interface. This prototype uses a direct local callback, not the originally proposed file bridge. Its repeated candidate builds are a local research facility. Hosted adaptation must not assume that callback or Docker access exists; any official multi-round repair schedule is organizer-controlled.

### 1.4 Full-agent state, edits and correctness

A **model turn** is one controller interaction, sometimes including a same-turn corrective model call. It can request tools, propose edits, approve a pending review, stop, or be malformed. An **attempt** is a repair candidate considered by the loop; it is not interchangeable with a turn. A **build** invokes the validator. Several model turns can occur before any source changes or builds.

An edit names `input/...`, exact `old_text`, `new_text`, and optionally `replace_all: true`. Ordinary replacements must match uniquely. Replace-all is explicit, literal and limited to one named file; it is not a regex or permission to change every similar file. Edits are simulated in order, so later edits see earlier replacements. Paths outside policy, symlinks, ambiguous/missing text and no-op changes are rejected. A compiler path such as `src/client.rs` is evidence, not necessarily a valid edit path: the local worktree requires `input/src/client.rs`.

Invalid batches enter focused correction before writing or building. Review mode additionally requires model approval of the pending diff; approval is bound to the affected source snapshot and exact proposal. Changed source requires renewed review. Partial application in review mode is restored. Model approval establishes neither semantic correctness nor package success.

The loop ranks candidates using build success, stage reached and diagnostic count. These are retention heuristics; fewer diagnostics does not prove a correct repair. Snapshot restoration retains the selected candidate rather than treating every last edit as an improvement. The revised repeated-diagnostic stop rule permits continued work when the leading error is unchanged but other progress is observed.

The final success predicate is:

```text
validator status == "succeeded"
AND artifact_validation_passed == true
```

An applied edit, model claim, `status: completed`, or zero harness exit code does not establish repair. `patch_applied` is not the success predicate: this harness transports changes inside a rebuilt source package rather than the validator's separate patch mechanism.

### 1.5 Hosted contract and required adaptation

This section records requirements from the supplied organizer participation and model-access PDFs, reviewed on 20 October 2026. It describes the migration target, **not an implemented or qualified hosted adapter**. The guide names Starter Kit `0.1.0-rc.5`; the recorded research implementation and readiness attempt used rc.2. Keep the historical baseline and its source hashes intact while developing a separately versioned hosted submission.

| Concern | Recorded local implementation | Hosted contract / required work |
| --- | --- | --- |
| Model configuration | Bedrock endpoint and `BB_MODEL_API_KEY`; recorded supplier model ID | Read injected `BB_MODEL_BASE_URL`, `BB_MODEL_NAME`, and `BB_MODEL_TOKEN_FILE`. Use the alias unchanged and read the file's contents as the credential. Never substitute a personal key for an invalid hosted credential. |
| Connectivity | Host-side HTTPS access to Bedrock | Organizer prepares model access before entrypoint startup while outbound networking remains disabled. Use the supplied interface; do not enable arbitrary external access or hard-code its host, port or credential path. |
| API | Existing OpenAI-compatible client and JSON reply parser | Chat Completions only: `/v1/chat/completions`; Responses is not part of this contract. When using the SDK, pass the injected base URL (already including `/v1`) as `base_url`. |
| Model actions | One-shot edits/rationale; full-agent JSON action protocol | Native function tools are supported, but executed and validated by the agent. They grant no official build or Docker access. Strict JSON Schema final-answer output is not supported; our reply-text JSON approach may remain, subject to hosted testing. |
| Workspace and repair | Local harness materializes `work/input/` and saves a diff | Resolve from `BB_WORKSPACE`; read `/workspace/input`, modify only permitted paths in `/workspace/work/repo`, and write `/workspace/output/agent-result.json`. Audit path mapping; do not confuse read-only hosted input with the local source-tree prefix. |
| Completion and errors | Observed missing-key run exited normally with `status: completed` | If model initialization prevents progress, log the failure stage/type without secrets and exit nonzero. Normal protocol completion is not proof of a successful model call or repair. |
| Build feedback | Harness copies, repacks and builds candidates during the loop | No agent-requested official builds within a run. If repair rounds are arranged, organizers provide updated logs to the same confirmed agent; teams cannot intervene or replace it between rounds. |
| Final repair | Harness transports edited source in a repacked Debian package | Final worktree state defines the repair. Platform derives a canonical diff, reapplies it to a clean case and independently validates. An unapplied patch in output is insufficient. |

**Request handling to audit before submission.** Use the injected model alias and a nonempty Chat messages array with isolated per-case history, `store=false`, and one completion (`n=1`). Use `max_completion_tokens` with a positive value no larger than 32,768; do not also send `max_tokens` or `max_output_tokens`. The approximate 1.05M context window is separate from the output ceiling, which includes reasoning and tool-call tokens. Each request body may be at most 16 MiB. Disable SDK automatic retries. After a timeout or interrupted stream, preserve diagnostics and avoid automatic retries. A 401/403 requires checking the injected credential; 409 forbids concurrent calls; 429 requires stopping new calls and reviewing the error. Length-limited, refused or interrupted output is not a complete success. Do not depend on unlisted sampling or reasoning settings.

The SDK example uses `max_retries=0`, an explicit timeout, `trust_env=False`, and `Accept-Encoding: identity`. Its example package pins (`openai==2.54.0`, `httpx==0.28.1`) are not installed requirements of our historical standard-library client. If adopted, pin and test them in the new submission. Function-tool histories must preserve `tool_call_id`; streaming clients must assemble deltas, check completion and terminal usage. The guide recommends `parallel_tool_calls=false` in its tool row but elsewhere says parallel-tool-call options are outside the interface specification; verify this detail against the current guide before depending on the option. Sequential calls are required by the per-case concurrency rule.

**Hosted Smoke budgets in the supplied guide:** up to 10 minutes per model request, 20 minutes for agent execution including calls/tools, 15 minutes each for initial and final build validation, and 60 minutes for the entire smoke run excluding queue wait. Requests are also bounded by remaining agent time. These are Hosted Smoke limits, not the historical local budgets or announced Full Evaluation case limits. The 128-token and 620-second SDK example settings are examples, not additional allowances.

Hosted Smoke must establish **at least one successful official model call and a successful independent repair validation**. Development Validation checks uploaded repaired worktrees; it does not execute the agent or validate this model channel. A local replay, `bb demo`, unit tests, or local submission validation cannot substitute for Hosted Smoke.

## 2. Design decisions and failed approaches

### 2.1 Decisions defining the Demo 2 baseline

The one-shot baseline deliberately fixes the evidence policy and model interface so later improvements have a stable comparison. A small fixed file set is simpler and cheaper than repository exploration, but can omit the actual root-cause file. A single proposal makes cost and behavior easy to inspect, but a wrong path or repair cannot be corrected from compiler feedback. Literal edits avoid unconstrained shell execution by the agent, but demand exact source text the model may not have seen. Independent Debian repackaging and clean artifact validation make the outcome meaningful even when the proposed patch is plausible but wrong.

We considered a rule-based patch for each package and supplying the known successful Rust diff. Those can validate infrastructure or investigate feasibility, but are not an autonomous baseline evaluation. The live one-shot and full-agent retest receive no reference repair. Build-time commands proposed in `debian/rules` are allowed by the existing pipeline and flagged for review; they are not silently scored as safer than direct source changes.

### 2.2 Full-agent extensions and lessons from unsuccessful trials

| Decision | Alternative and observed tradeoff |
| --- | --- |
| Keep one-shot frozen | Changing its prompt after failures would move the baseline. `one-shot-v1` remains the comparison point; full-agent source is separate. |
| Exact edits and preflight | Blind or fuzzy replacement can touch unintended code. Exact matching exposed missing prefixes, duplicate proposals and ambiguous snippets; the controller now returns precise correction feedback. |
| Preserve compiler detail | Early Rust summaries discarded expected/found argument notes and merged distinct locations. Retaining those notes improves available evidence, but has not yet produced an autonomous Rust success. |
| Include complete diagnosed source when it fits | The original bounded retrieval approach sometimes caused repeated inspection. Current source inclusion allows two files, at most 64,000 bytes each and 100,000 total. An offline check found both entire `client.rs` and `delta.rs` present in a 98,655-character request. This disproved the assumption that every failure was caused by missing those files. Other affected files still require discovery. |
| Iteration with independent builds | A one-shot cannot revise from its own compiler failure. Full-agent attempts can, but investigation and correction often consume the budget before a build. |
| Small-group strategy was tested | Three-edit groups produced valid incremental changes but addressed too little of a package-wide cause. Three builds reduced Rust diagnostics from 103 to 95 without repairing it. It is not the default demonstration strategy. |
| Shared-cause strategy is opt-in | It asks the model to inspect shared producers/consumers across files. It removes the three-edit grouping, but does not automatically discover or fix all affected locations. |
| Explicit replace-all and pre-build diff review | Repeated pointer-type fixes motivated whole-file literal replacement with measured counts and cross-file coverage hints. A valid review can still omit files; coverage is bounded search, not semantic proof. |
| Retain budgets, record exploratory increases | Rust experiments used 100k, 200k and 300k token ceilings and 20/30 turns; a later run allowed 1M tokens, 80 turns and ten loop builds. More allowance alone did not solve erroneous or redundant edits. These are different exploratory configurations, not controlled improvement comparisons. |
| Snapshot/copy before building | Debian `3.0 (quilt)` repacking creates patch metadata. Building a copy keeps these generated changes out of the agent's retained source and repair diff. `dpkg-source --auto-commit -b` avoids an interactive patch-recording step. |
| JSON actions in reply text | Provides an explicit controller protocol without requiring native tool-calling support. Malformed reasoning-only responses remain a measured failure mode. |

A concrete unresolved failure at `001f3b9`: the model first replaces every `*const i8`, then asks to replace `as *const i8` in the same file. The second text no longer exists in the simulated result. Focused correction repeatedly resubmitted the redundant operation, so no batch reached review or build. A separately prepared recovery patch removes only redundancies proven equivalent by whole-file simulation; it is **not assumed installed or live-validated by this document**.

The assisted Rust repair replaced 55 C pointer-type occurrences across seven files with the platform's `std::os::raw::c_char`. This fixed shared wrapper interfaces as well as call sites and produced a package. It demonstrates that editing permitted source is sufficient; it does not show that the autonomous agent found that scope. A model given the worked solution still omitted `string.rs`; assistance supplied its two missing replacements. That second successful build is example-assisted, not independent generalization.

### 2.3 Resource configuration

Committed full-agent defaults at the documented code milestone: five attempts, three loop builds, 100,000 reported model tokens, 1,800 seconds with a 120-second reserve, 20 model turns and 30 tool calls. A baseline build and final validation are additional harness work. Default investigation allows six consecutive tool turns; small-group mode uses two. Token ceilings are checked around calls, so the last request can overshoot; missing usage is unknown, not zero. Build subprocesses have their own manifest timeout and harness margin; the agent clock is not a hard kill of the entire experiment.

The TA wrapper uses the source defaults actually present and records them in `settings.json`. It resets inherited ablation/strategy/review/trace settings explicitly. It does not silently raise limits or edit source. The `--review` option activates shared-cause investigation and proposed-diff review for a live full-agent run only. Default controls and replay use review off, because scripted replies do not contain approval actions.

## 3. Setup from a clean AWS instance for the recorded local experiments

This guide uses only the **rcran-v1** and **development-v1 ten-case validator bundles**, plus this repository. The bundles contain their runtime images, original sources, target configurations and frozen dependencies. No general 200-case development dataset, separate Starter Kit runtime-image release, or assisted repair is needed for this local reproduction route. The separate current-kit hosted qualification route in §4.6 uses its own release-pinned images; do not mix that environment into the historical reproduction checkout.

The recorded evaluation used code commit `001f3b9dd15f89665794609c8b04b73acfae167e`. The helper below restores that source and the saved full-agent limits in a separate directory. It does not promise identical model outputs or an identical host environment. The new TA wrappers have offline tests; this fresh-instance walkthrough still needs a real acceptance run.

### 3.1 Access and instance

You need repository access, permission to download the official bundles, and for live runs an AWS identity permitted to invoke the recorded Bedrock model. Neither bundle nor model credential is stored in Git. Replay controls need no model credential but still perform real local builds.

Use Ubuntu Server **24.04 LTS, ARM64**, on a Graviton instance. The recorded host was an `m7g.xlarge` with 4 vCPUs, 16 GiB RAM and 80 GiB storage; use at least that capacity and monitor free disk space. Use a disposable evaluation host with sudo and Docker access. Allow outbound HTTPS for GitHub, package repositories, bundle downloads and Bedrock. SSH access is needed if using the SSH examples; restrict inbound SSH to your own address. No public application port is required. AWS charges are the evaluator's responsibility; stop the instance when finished and retain results first.

On the new **EC2 Linux terminal**:

```bash
sudo apt-get update &&
sudo apt-get install -y git python3 docker.io dpkg-dev xz-utils gzip tar ca-certificates tmux qemu-user-static binfmt-support &&
sudo systemctl enable --now docker &&
sudo usermod -aG docker "$USER"
```

Log out and reconnect, then:

```bash
uname -m
docker info --format '{{.Architecture}}'
python3 --version
dpkg-source --version
df -h "$HOME"
mkdir -p "$HOME/Downloads"
git clone --branch demo-2 https://github.com/ec528-fall26/build-bench.git "$HOME/build-bench-demo2"
cd "$HOME/build-bench-demo2" &&
git fetch origin --tags &&
git rev-parse HEAD &&
git status --short
```

Expected: `uname -m` prints `aarch64`, Docker prints `aarch64`, Python prints `Python 3.12.x`, `dpkg-source` prints its version banner, and `git status --short` prints nothing.

`uname -m` must report `aarch64`. Python 3.12 on Ubuntu 24.04 is suitable; the harness uses the standard library, so no pip install is required. Record the handoff commit printed above. If the repository is private, authenticate using your approved GitHub access; do not embed tokens in a saved clone URL. Host package versions are not fully pinned by this guide.

### 3.2 Download exactly two official bundles

Open [Build-Bench Data & Downloads](https://matrix.cstcloud.cn/build-bench/data-downloads.html):

| Website row | Version | Select |
| --- | --- | --- |
| Local Validator Example | rcran-v1 | Download local example; also Checksums and Local validation guide |
| Local Validator Bundle: 10 Cases | development-v1 | Download bundle; also SHA-256 and Guide |

Use the official page's links rather than guessed URLs. Website authentication and direct-link availability were not independently verified during preparation. The TA must confirm download access before evaluation. The ten-case archive is approximately 2.29 GB. Verify downloaded archives against the published checksums; if filenames or hashes differ, stop and confirm the release rather than substituting another dataset.

If downloading through your laptop's browser, transfer the archives to EC2. In your **laptop terminal**, replace the key path and IP with those of your own instance:

```bash
scp -i /path/to/your-ec2-key.pem \
  "$HOME/Downloads/buildbench-local-rcran-v1.tar" \
  "$HOME/Downloads/buildbench-local-development-v1.tar" \
  ubuntu@YOUR_EC2_PUBLIC_IP:/home/ubuntu/Downloads/
```

The following commands assume those archive filenames. If the official download uses a different extension, inspect it and follow its accompanying guide; do not merely rename compressed formats. On **EC2**, extract into new directories, verify the internal checksums and load the bundled images:

```bash
test ! -e "$HOME/buildbench-local-rcran-v1" &&
tar -xf "$HOME/Downloads/buildbench-local-rcran-v1.tar" -C "$HOME" &&
cd "$HOME/buildbench-local-rcran-v1" &&
sha256sum -c SHA256SUMS &&
docker load -i runtime-image.tar.gz &&
bash run.sh --check
```

```bash
test ! -e "$HOME/buildbench-local-development-v1" &&
tar -xf "$HOME/Downloads/buildbench-local-development-v1.tar" -C "$HOME" &&
cd "$HOME/buildbench-local-development-v1" &&
sha256sum -c SHA256SUMS &&
docker load -i runtime-image.tar &&
bash run.sh --list
```

These are the recorded bundle layouts. Stop on any missing file, checksum error or failed preflight. `--check` does not prove a real build works. Keep the archives outside Git.

### 3.3 Offline checks, then the recorded source profile

First test the unmodified handoff checkout; tests that assume default limits must run before preparing the historical enlarged-budget profile:

```bash
cd "$HOME/build-bench-demo2" &&
env -u BB_MODEL_TRACE -u BB_REPAIR_STRATEGY -u BB_REVIEW_EDITS \
  -u BB_AGENT_USE_EVIDENCE_MANAGER -u BB_AGENT_USE_TOOLS \
  -u BB_AGENT_USE_BUILD_FEEDBACK -u BB_AGENT_USE_HISTORY \
  python3 -m unittest discover -s tests/full-agent -p 'test_*.py' &&
python3 -m unittest discover -s harness/tests -t . &&
python3 -m unittest discover -s tests/demo2 -p 'test_*.py' &&
python3 scripts/demo2_evidence_report.py
```

Expected output (checked on commit `a0f0044`): the three suites report `Ran 320 tests ... OK`, `Ran 79 tests ... OK` and `Ran 15 tests ... OK`. These counts change if tests are added; the requirement is `OK` with no failures or errors. The evidence report needs no Docker or model key and recomputes the recorded results from the committed evidence. Its first lines must be:

```text
Latest completed one-shot run: demo2-oneshot-retest-20261019T212712Z
Provenance: archive summaries verified against original records.
rcran : 1 cases; 0 repairs; {'build_failed': 1}
development_bundle : 10 cases; 0 repairs; {'build_failed': 5, 'case_not_failing': 1, 'no_edit_applied': 4}

Latest paired retest (one repeat; raw verdicts retained):
one-shot 0 / 11 repairs; 5 setup-blocked; 1 already building
full-agent 1 / 11 repairs; 5 setup-blocked; 1 already building
```

It then prints the older four-case archive (0/12 for each arm) and source-archive SHA-256 values. This step checks the numbers in §5.3 against the saved records. It does not rerun the experiments.

Prepare the separate checkout. Do not point `--out` at an existing directory:

```bash
python3 scripts/demo2_prepare_recorded.py --out "$HOME/build-bench-demo2-recorded" &&
cd "$HOME/build-bench-demo2-recorded" &&
cat ta-recorded-profile.json &&
python3 -m harness.case_views "$HOME/buildbench-local-development-v1" "$HOME/bb-cases"
```

The helper checks out the historical commit, applies only the saved budget diff, verifies every recorded full-agent Python source hash and checks the one-shot tag. The settings are 12 attempts, 10 loop builds, 1M reported tokens, 7,200 seconds, 120 seconds reserve, 80 turns and 200 tool calls. The runner sets default strategy, history on and review off; no reference repair is supplied in live mode. Baseline and final validation builds are additional harness work. A model call can overshoot the token threshold.

Do not commit the prepared checkout's intentional local budget modifications. Record its `ta-recorded-profile.json` with the results. Source verification does not pin host kernel, Docker, QEMU or model-serving changes.

## 4. Running and verifying the experiments

### 4.0 Reproduction index: each claimed result, its command and the expected output

Complete §3 first. Every row below is a claim made in this document. Live-model rows are nondeterministic: the acceptance criterion is a valid record and, for any claimed repair, a validator `succeeded` status with passed artifact validation. Matching our counts exactly is not required.

| Claimed result | Command (section) | Needs | Expected output |
| --- | --- | --- | --- |
| Offline tests and recorded counts (§5.3) | Offline checks (§3.3) | Nothing beyond the clone | All three suites `OK`; evidence report prints one-shot 0/11 and full-agent 1/11 |
| Positive and negative replay controls | `demo2_demo.py controls` (§4.1) | rcran bundle, Docker | Two summaries with `check_error: null`; positive `repaired: true`, negative `repaired: false` and `termination_reason: build_failed` |
| Full-agent failed build → revision → successful build | `demo2_demo.py revise-replay` (§4.1) | rcran bundle, Docker | One summary with `repaired: true`, `check_error: null` |
| Live rcran repair by a model | `demo2_demo.py live-one-shot` / `live-full` (§4.3) | Bedrock key | One summary per run; `repaired` may be true or false |
| Paired retest: one-shot 0/11, full agent 1/11 | `demo2_retest.py` (§4.3) | Both bundles, Bedrock key, several hours | `results.md` and `coverage.json` with 22 completed jobs |
| Local `bb ready` passes for both agents (21 Oct) | `./bb ready` with the local model override (§4.5) | Bedrock key, Docker | `✓ Final validation succeeded`, readiness exit code 0, ZIP written |
| Assisted Rust build (`rust-assisted-cchar-v1`) | No TA command; evidence only (§2.2, §5.2) | — | Not reproducible from this handoff; it receives no autonomous credit |
| Hosted Smoke qualification | Not yet executed (§4.6) | — | No result is claimed |

### 4.1 Prove the rcran pipeline without a model key

Start a persistent session on EC2:

```bash
tmux new -s demo2-ta
```

Inside that session:

```bash
cd "$HOME/build-bench-demo2-recorded" &&
python3 scripts/demo2_demo.py controls \
  --bundle "$HOME/buildbench-local-rcran-v1" \
  --out "$HOME/buildbench-experiments/ta-rcran-controls-01"
```

Expected: the positive known-fix control produces a verified repair; the negative comment-only control applies a change and fails its build. Both results must be recorded. These are scripted infrastructure controls, not autonomous successes. The console prints `Starting <name>; evidence: <out>` before each control, then a JSON summary for each one. Each summary must contain `"check_error": null`. The positive summary contains `"repaired": true`; the negative contains `"repaired": false` and `"termination_reason": "build_failed"`. The last line is `Pipeline completed. Live not-repaired results are valid outcomes, not claimed successes.` and the exit code is 0. A failed check prints its reason in `check_error` and exits with code 2. Inspect the new output directory's `settings.json`, `summary.json`, `*.console.log` and `runs/*/record.json`. Stop and diagnose if either control fails its expected check. Reconnect with `tmux attach -t demo2-ta`.

Then run the scripted full-agent revision demonstration (still no model key):

```bash
python3 scripts/demo2_demo.py revise-replay \
  --bundle "$HOME/buildbench-local-rcran-v1" \
  --out "$HOME/buildbench-experiments/ta-rcran-revise-01"
```

Expected: one summary with `"repaired": true` and `"check_error": null`, then the `Pipeline completed.` line. The driver checks that the record contains at least one failed loop build **and** a later successful one. If either is missing, it reports `Replay did not demonstrate both a failed and a successful loop build.` and exits with code 2. The replies are scripted, so this shows how the loop handles build feedback. It is not evidence of model reasoning.

Add `--dry-run` to any `demo2_demo.py` command to print its planned harness commands without building.

### 4.2 Short-term Bedrock API key for local live runs

This section is for the team's local research harness only. Hosted submissions use the organizer-injected token file (§1.5), not a Bedrock key. Local `bb ready` does not inject official model access.

Use a **short-term Amazon Bedrock API key generated in us-east-1**. In the AWS Bedrock console choose the region, then **API keys → Short-term API keys → Generate short-term API keys**. The generating identity needs permission to use the model. AWS documents that the key inherits that identity's permissions, is region-specific, and expires at the earlier of the session expiry or 12 hours. See [AWS key-generation instructions](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-generate.html).

Generate the key shortly before starting the live evaluation. A long schedule may exceed its lifetime. Do not put it in Git, a script, a URL, a screenshot or an evidence archive. Paste it with hidden input **inside the same EC2 tmux shell** that launches the run:

```bash
set +x
read -r -s -p 'Paste short-term Bedrock API key for us-east-1: ' BB_MODEL_API_KEY
export BB_MODEL_API_KEY
printf '\n'
export BB_MODEL_BASE_URL='https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1'
```

The historical agent fixes the model ID to `openai.gpt-oss-120b-1:0`; the wrappers do not substitute a newer model. This uses the Bedrock OpenAI-compatible endpoint, not an OpenAI account key. A correctly formatted key is not proof of authorization. A 401/403, expired key, unavailable model or endpoint error is an execution problem, not a source-repair result.

### 4.3 Live evaluation: rcran plus the ten cases

The full schedule contains 22 jobs, one repeat per agent per case, with rcran first. The new scheduler interleaves agents; the archived evaluations ran the one-shot arm and full-agent arm separately. Source, limits and case identities are reproduced; scheduling and model outcomes are not asserted identical.

Enable the reverse-direction handler:

```bash
sudo update-binfmts --enable qemu-x86_64 &&
cat /proc/sys/fs/binfmt_misc/qemu-x86_64
```

It must be enabled and include `F` in flags. **This check alone is insufficient:** in the recorded run all five x86-target cases encountered QEMU/setup crashes before package compilation. The pipeline retains these outcomes. Do not report them as five established source-repair failures or promise that the TA's host fixes them.

Inspect the schedule without model calls or builds:

```bash
cd "$HOME/build-bench-demo2-recorded" &&
python3 scripts/demo2_retest.py \
  --rcran "$HOME/buildbench-local-rcran-v1" \
  --views "$HOME/bb-cases" \
  --out "$HOME/buildbench-experiments/ta-paired-recorded-01" \
  --dry-run
```

Then launch all 22 jobs:

```bash
python3 scripts/demo2_retest.py \
  --rcran "$HOME/buildbench-local-rcran-v1" \
  --views "$HOME/bb-cases" \
  --out "$HOME/buildbench-experiments/ta-paired-recorded-01"
```

Expected from the dry run: `22 live runs: 11 cases × 2 agents × 1 repeat(s). No calls/builds started.` followed by the 22 job IDs. Expected from the real run: progress lines `[n/22] <job_id>`, ending with `Schedule complete. Read results.md and coverage.json; already-building cases are not repairs.` The output directory contains `coverage.json`, `results.md`, `plan.json`, and per-job evidence under `jobs/`. Every row in `coverage.json` must have `"status": "completed"`. `results.md` has one table row per case and agent, with columns for repaired, final reason and agent stop. Compare it with the table in §5.3; the counts may differ because model output varies. An unchanged successful build is not a repair. A valid live failed repair is retained, not retried until success. The complete schedule can take hours; individual manifest build timeouts differ and two allow four hours. Quiet output during compilation is normal.

After interruption, refresh an expired key in the same shell and add `--resume` to the same command, without changing code, endpoint, Python or case files. Resume skips jobs with completed summaries, including failures. **It does not automatically retry a completed authentication-error job.** Keep that evidence and run any deliberate retry into a fresh output directory, clearly labeled, rather than deleting records or combining retries into the original score.

For a shorter live demonstration instead of the full schedule, run these separately with fresh output directories:

```bash
python3 scripts/demo2_demo.py live-one-shot \
  --bundle "$HOME/buildbench-local-rcran-v1" \
  --out "$HOME/buildbench-experiments/ta-rcran-one-shot-01"
python3 scripts/demo2_demo.py live-full \
  --bundle "$HOME/buildbench-local-rcran-v1" \
  --out "$HOME/buildbench-experiments/ta-rcran-full-01"
```

Expected: each command prints one JSON summary and the `Pipeline completed.` line. `repaired` may be `true` or `false`; a not-repaired live run is a valid outcome. `check_error` must be `null`. A key without permission, or an expired key, appears as a model error in the record, not as a repair failure.

The recorded single repeat was one-shot 0/11, full agent 1/11 (rcran), with five setup-blocked and one already-building case per arm. Model behavior is nondeterministic; an identical score is not the acceptance criterion. Require valid records and actual clean-build/artifact verification for every claimed repair.

### 4.4 Preserve evidence and assess readiness

Retain the handoff commit, prepared profile, plan, settings, records, patches, baseline/candidate/final logs and summaries. Check that a claimed success has both validator success and passed expected-artifact validation. Review setup-stage failures separately. Stop the AWS instance after exporting the evidence.

Handoff acceptance remains pending until a fresh host passes bundle verification, offline tests, both real rcran controls and at least one recorded live invocation. The code/document update itself did not provision AWS, test website account access, call Bedrock or build packages. The five reverse cases and hosted competition qualification remain separately unresolved.
### 4.5 Local readiness checks (20 and 21 October 2026)

**21 October: both agents pass local `bb ready` with a local model override.** Evidence: `docs/evidence/demo2/local-readiness-20261021/`. Both one-shot and full agent passed the Starter Kit `hello` example: 1/1 case, readiness exit code 0, final validation succeeded, and the submission ZIP was written. To get there, the rc.2 runner (`runner/run-agent-case.sh`, commit `a0f0044`) now forwards `BB_MODEL_API_KEY`, `BB_MODEL_BASE_URL` and `BB_MODEL_NAME` into the agent container. It also takes the container network from `BB_LOCAL_AGENT_NETWORK`, which still defaults to `none`. No agent repair code or validator success criteria changed. **This is a modified local test, not Hosted Smoke qualification**, because the official local runner gives the agent no network or model access.

Reproduce on the EC2 host in the `demo-2` checkout (not the prepared recorded checkout), in the shell that holds the Bedrock key from §4.2:

```bash
cd "$HOME/build-bench-demo2/buildbench-starter-kit-0.1.0-rc.2" &&
export BB_VALIDATOR_IMAGE='ghcr.io/terriyyy/buildbench-validator-runtime@sha256:11151f88f6b12c578b9c9fafd0420d6da27c6969b5909aaa4c51e78dd9acd7c7' &&
export BB_EXAMPLE_ASSETS_IMAGE='ghcr.io/terriyyy/buildbench-example-assets@sha256:98bdf0f445cf0296f67ffc8b4601ab1cb1f82b957f84bd9739276f5659cdde54' &&
docker pull "$BB_VALIDATOR_IMAGE" && docker pull "$BB_EXAMPLE_ASSETS_IMAGE" &&
export BB_LOCAL_AGENT_NETWORK=bridge &&
./bb ready --agent ./agents/one-shot --output runs/readiness/ta-one-shot/one-shot.zip --json
```

```bash
./bb ready --agent ./agents/full-agent --output runs/readiness/ta-full-agent/full-agent.zip --json
```

Expected progress for each agent (also saved to `runs/tests/<agent>/<run>/progress.log`):

```text
[1/1] Testing hello
✓ Agent submission is valid
✓ Example Case prepared
✓ Initial build failure reproduced
✓ Agent completed
✓ Canonical patch generated
✓ Final validation succeeded
[1/1] hello succeeded
```

Then each command prints a JSON result with `"status": "succeeded"`, `"exit_code": 0` and `"summary": "Agent passed local readiness checks and was packaged."`, plus an `agent_submission` artifact with its SHA-256. Compare with `one-shot-ready-result.json` and `full-agent-ready-result.json` in the evidence folder. The model's repair is nondeterministic, so the `repair.diff` and ZIP hash may differ from ours. Our one-shot run replaced the injected `exit 1` with `true`. Our full-agent run removed the failing lines but also added unneeded `%global` macro overrides; that patch passed the build but needs review. Without `BB_LOCAL_AGENT_NETWORK=bridge` and a key, the agent cannot reach the model and readiness fails, as on 20 October below.

**20 October: earlier failed attempt (kept for the record).** **Purpose:** distinguish package validation from model integration and repair success. This was an additional check of the committed one-shot package, not a rerun of the 22-case research evaluation.

| Item | Observed evidence |
| --- | --- |
| Checkout | `8724cf7` on `demo-2` (short commit printed by Git; full SHA saved in the host evidence directory) |
| Agent / kit | `agents/one-shot`, version `1.0.0`, entrypoint `python -m src.main`; Starter Kit rc.2 |
| Image configuration | Published rc.3 validator and example-assets images, supplied through environment overrides; mixed-version compatibility check, not an rc.5 qualification |
| Submission check | Passed; 9 files, 40,077 bytes |
| Local case | `hello` prepared and original build failure reproduced |
| Agent result | Zero proposed and applied edits; message: `No model API key configured (set BB_MODEL_API_KEY).`; `status: completed` |
| Runner result | Patch generation reported `Agent made no allowed textual change`; case `runner_error`, 0/1 passed, `bb ready` exit code 5 |
| Interpretation | No model call and no candidate repair were tested. This is not evidence that the model failed to solve hello, and it is not a readiness pass. |

The inspected rc.2 agent launch uses `--network none` and forwards neither `BB_MODEL_API_KEY` nor an organizer model credential. Refreshing a key in the host shell cannot fix that unchanged launch. The participation guide explicitly says local `bb test` and `bb ready` do not inject official model access, and directs model-dependent agents to local check/package followed by Hosted Smoke. We do not hard-code a hello repair. The 21 October pass relies on the opt-in `BB_LOCAL_AGENT_NETWORK` override described above, so it is not presented as qualification under the official local runner or as Hosted Smoke.

Earlier attempts stopped before agent execution: an output ZIP outside the kit was rejected (exit 2), then unavailable default validator/example-assets images blocked preparation (exit 3). Published rc.3 overrides allowed the test to reach agent execution. The final image references were:

```text
ghcr.io/terriyyy/buildbench-validator-runtime@sha256:11151f88f6b12c578b9c9fafd0420d6da27c6969b5909aaa4c51e78dd9acd7c7
ghcr.io/terriyyy/buildbench-example-assets@sha256:98bdf0f445cf0296f67ffc8b4601ab1cb1f82b957f84bd9739276f5659cdde54
```

Evidence remains on the team's EC2 host; this documentation update does not claim it has been imported into Git:

```text
/home/ubuntu/buildbench-experiments/bb-ready-oneshot-20261020T175811Z/
  commit.txt, runtime-images.txt, ready.log, exit-code.txt
/home/ubuntu/build-bench-demo2/buildbench-starter-kit-0.1.0-rc.2/runs/tests/one-shot/20261020T175827Z-2260/
  summary.json, progress.log
  cases/hello/agent-result.json, agent.log, initial-build-result.json, initial-build.log
```

Archive the compact records and image references without credentials. Do not report this as a completed `bb package` run or as evidence of a valid final ZIP: readiness stopped at the example test.

### 4.6 Separate hosted qualification plan (not yet executed)

This is a future submission workflow, not a prerequisite for reproducing §§3–4.3. Use a separate directory and the current release documented by the organizers (rc.5 in the supplied PDF), with its own pinned runtime images. Do not upgrade the historical research checkout in place or relabel past results as rc.5 runs.

1. Download and verify the current versioned Starter Kit from [Data & Downloads](https://matrix.cstcloud.cn/build-bench/data-downloads.html), or follow the official [baseline repository](https://github.com/AIOps-Lab-NKU/BuildBench-Agent-Baseline) instructions and record its exact commit. Read that kit's contract before adapting an agent. The PDF specifies Linux/WSL2 and Docker Engine 24+ or Docker Desktop with Linux containers.
2. Run `./bb doctor` and `./bb demo` in that kit. Demo uses the official example agent, not ours; its success qualifies the example environment only.
3. Create a separately versioned hosted agent, implement the injected model configuration and error handling in §1.5, audit permitted worktree paths and request parameters, and pin all third-party dependencies. Preserve the frozen local baseline unchanged. This document update has not implemented these changes.
4. In the current kit, run the following **after** the adapted agent exists at `agents/demo2-hosted`. These are documented target commands, not completed checks:

```bash
./bb check --agent ./agents/demo2-hosted &&
./bb package --agent ./agents/demo2-hosted
```

5. Record the exact source version, kit release, ZIP path, size and SHA-256. The package root must contain `agent.yaml`, `src/`, `requirements.lock`, and `README.md`; exclude credentials, generated evidence and case-specific answer patches. Upload the kit-produced ZIP through My Submissions, then run Hosted Smoke. An adapter change requires a new immutable version.
6. Retain the submission/run IDs and redacted logs establishing both an official model call and independent repair validation. Only then mark hosted integration qualified. Confirming a final version is a separate team action; do not select or finalize a version as part of this documentation update.

The supplied guide retains a general local-example checklist alongside its explicit exception for model-dependent agents. We record both the 20 October failure and the 21 October pass with a modified runner, and follow the guide's check/package/Hosted Smoke steps. Neither local result is described as hosted qualification. According to the supplied guide, final confirmation is due by the end of 13 November 2026, Beijing time (UTC+8), is irreversible, and requires current Hosted Smoke revalidation. Verify the current organizer notice before acting; no final confirmation or Full Evaluation was performed here.

## 5. Claims, milestones and limitations

### 5.1 Milestone accountability

| Original commitment | Current evidence/status | Remaining work |
| --- | --- | --- |
| Demo 2: compatible end-to-end LLM prototype and materialized Debian trace | **Local prototype demonstrated.** rcran live repairs documented; model/tool/edit/build records implemented. Commands above expose both controls and live paths. | No additional implementation required for this local prototype milestone. |
| Demo 2: runnable packaged agent; starter compatibility documented | **Local readiness passed with a modified runner; hosted qualification open.** On 21 October both agents passed `bb ready` on hello (rc.2 kit, rc.3 images, model settings forwarded and agent network enabled). The 20 October unmodified attempt failed without a model credential (§4.5). | Implement the hosted configuration/error adapter in a separate version; check/package using the current kit and pass Hosted Smoke. Preserve the local failure evidence. |
| Demo 2: freeze/evaluate ten public cases, aiming five per direction | **Coverage complete for both agents, one repeat per case.** One-shot bundle: five `build_failed`, four `no_edit_applied`, one `case_not_failing`; zero repairs. Full agent: zero bundle repairs, plus a verified rcran repair. Original records and plans are imported. | Five reverse cases failed during environment setup in both arms; rerun their intended source failures after fixing setup. Keep the unchanged case separate. This is not the future controlled repeated comparison. |
| Demo 3: history-based revision and ablation | Loop/history implemented early; scripted revision works and live Rust attempts show sequential changes. | Controlled history-on/off comparison with common settings on the frozen set; no measured history benefit claimed yet. |
| Demo 3: measurable improvement | **Not demonstrated.** Each arm repaired 0/4 source-evaluable development cases in the latest single-repeat retest. | Freeze version/budgets, run planned paired repeats, report gains and regressions. |
| Final: 40-case evaluation and second targeted improvement | Future work, not completed by this prototype. | Preserve package-group holdout and avoid tuning on held-out cases. |

The proposal explicitly promised **no minimum Demo 2 repair rate**, but did promise evaluation coverage and a runnable prototype. Successful rcran demonstrates a real capability. Ten-case evaluation completion depends on recorded coverage, not repair count; packaging verification is a separate obligation. Announce any milestone revision rather than silently changing the denominator.

### 5.2 Claims supported and not supported

**Fresh-instance reproducibility:** The evaluations ran on the team's configured EC2 instance. The consolidated fresh-instance setup instructions have passed offline checks but have not yet been validated end to end on a new instance. This is a handoff-verification limitation; it does not undo the completed local prototype demonstration or recorded evaluations.


Supported: real end-to-end local repair/build machinery; positive and negative replay controls previously verified; documented live rcran successes; detailed negative evidence on harder cases; an assisted Rust build demonstrating source-level feasibility.

Not supported: general superiority over one-shot, a causal benefit from repair history, autonomous Rust success, RISC-V/hidden-case performance, current hosted qualification, or reliable success on every live rerun. Passing builds establish benchmark build/artifact criteria, not exhaustive functional correctness or unchanged behavior on every architecture.

The latest full-agent rcran success is verified from the newly imported paired archive, including its final artifact check. The older raw rcran live records were not included in the supplied 24-run archive. The rcran historical claims above cite `part5-findings.md` §§9a/9c and should be strengthened by checking in the corresponding compact raw records. The four full-agent rcran runs vary in settings, and temperature 0 did not remove variation in one-shot runs. Do not advertise 4/4 as a controlled superiority comparison against 1/3.

The `rust-assisted-cchar-v1` experiment succeeded at packaging with zero diagnostics in 163.0 seconds. The example-assisted completion succeeded in 168.9 seconds. Neither receives autonomous credit. Ground-truth agreement with the separate website Development Validation process has not been demonstrated here.

Known limitations include repeated prompt cost, limited cross-file reasoning, stale/duplicate model edits, limited output budgets, incomplete tool/model-protocol compliance, unknown usage on some calls, and coarse high-level failure classification. A previous Unicode line separator in an API key caused a request-encoding failure mislabeled at the summary level as no edit applied. The new TA wrapper rejects that key format and surfaces model errors, without claiming to repair every upstream classification issue.

### 5.3 Findings from the completed paired retest

The full diagnostic review is [Demo 2 comparison diagnostics](demo2-comparison-diagnostics.md). Both plans record commit `001f3b9dd15f89665794609c8b04b73acfae167e`. The full agent used default strategy, history on and **proposal review off**, with local limits of 1M tokens, 80 turns, 10 loop builds, 12 attempts, 200 tool calls and 7,200 seconds. The `demo2_prepare_recorded.py` helper in §3.3 explicitly restores these elevated limits and verifies recorded source hashes in a separate checkout. Without that preparation, wrappers use the source defaults present; they do not silently raise limits. Optional review and shared-cause features were not evaluated by this paired run.

| Latest evidence | One-shot | Full agent |
| --- | ---: | ---: |
| Scheduled cases processed | 11 | 11 |
| Verified repairs across all cases | 0 | 1 (rcran) |
| Development bundle repairs | 0/10 | 0/10 |
| Source-evaluable development cases repaired | 0/4 | 0/4 |
| Reverse-direction cases blocked in setup | 5 | 5 |
| Already-building cases | 1 | 1 |

The full agent changed `HAVE_MM_PREFETCH` from 1 to 0 in the rcran configuration header; separate final validation produced the expected ARM64 `.deb`. One-shot's failed build-time edit remains in the results. This is one positive local demonstration, not a reliability guarantee or causal proof about history.

Frozen dependencies and a validator runtime did not prevent host/emulation setup crashes. All five x86-target baseline logs contain QEMU crashes and finish in setup/infrastructure failure. Their underlying intended source defects were not evaluated successfully. The high-level harness categories can mislabel such runs as ordinary build failures or no edit; future reporting must retain raw verdicts while exposing stage-based categories.

Immediate work is to qualify the reverse-direction environment, improve stage classification and proposal accounting, and test edit-correction changes against saved failures. Later work is controlled repeats/history ablation and broader cases. Hosted compatibility requires the separately versioned adapter and qualification workflow in §§1.5 and 4.6; an in-run official build callback is unavailable under the supplied contract. The Rust full-agent run exhausted twelve correction responses before applying a batch; its zero proposed-edit summary does not mean the model never proposed edits. Increasing token limits alone has not established a solution.

### 5.4 Handoff acceptance checklist

Before the team calls the handoff ready:

- Commit this document, driver, driver tests and compact evidence to `demo-2`; record the full commit used by the TA.
- Provide repository and validator-archive access; verify bundle checksums on the TA host.
- Run both replay commands on that commit and preserve their real build results. Run at least one live rcran example and preserve its actual outcome, even if it fails.
- Preserve both readiness records (§4.5): the 20 October failure and the 21 October modified-runner pass. Complete current-kit check/package and Hosted Smoke only after the hosted adapter is implemented; do not describe these as already passed.
- Preserve the imported 22 run records and the paired diagnostic table. Keep setup-blocked cases, the unchanged successful case, genuine source-repair failures and the rcran repair distinct. Re-run the five reverse cases after environment qualification; keep assisted successes separate.
- Keep credentials, unpacked dependency bundles and build trees out of Git and the submitted agent ZIP.

The accompanying drivers were tested offline during document preparation. This documentation revision performed no new EC2 build or paid model experiment; §4.5 records the separate readiness tests supplied by the team. A passing clean-machine walkthrough is the remaining acceptance gate, not an assumed result.

### Source map

- `design-proposal.md`, milestones §5: original commitments and no-minimum-rate scope.
- `part5-findings.md` §§9a–9d: frozen baseline, full-agent rcran and initial development-case observations (historical notes contain some stale planning text).
- `harness/README.md`, `harness/run_case.py`, `harness/build_feedback.py`, `harness/record.py`: executable build/result contract.
- `reviewed-repairs.md`: optional replace-all and proposed-review semantics.
- `evidence/demo2/comparison-records.json`: older compact 24-run archive evidence; not the new ten-case run.
- `demo2-one-shot-results.md` and `evidence/demo2/one-shot-retest-summary.json`: completed eleven-case one-shot aggregate verified against imported original records. The paired-retest evidence contains both plans, summaries, all 22 records, actual patches and line-numbered log excerpts.
- Organizer bundle READMEs and manifests: pinned local runtime/dependencies and platform requirements.
- Supplied `Participate | Build-Bench Challenge.pdf` and `Model Access.pdf`, reviewed 20 October 2026: hosted contract, model configuration, local/hosted distinction and qualification requirements. The Participate PDF includes expanded request/error/adaptation sections absent from the collapsed view in the earlier PDF. Source links extracted from the supplied PDF: [participation and model access](https://matrix.cstcloud.cn/build-bench/submission.html?lang=en#model-access), [versioned model-access guide](https://matrix.cstcloud.cn/build-bench/downloads/model-access-v0.1-20261016/model-access.en.md), and [guide checksums](https://matrix.cstcloud.cn/build-bench/downloads/model-access-v0.1-20261016/MODEL_ACCESS_SHA256SUMS). Policy statements here describe those supplied snapshots, not an independent verification of later website changes.
- [Runtime images rc.3 release](https://github.com/terriyyy/BuildBench-Runtime-Images/releases/tag/v0.1.0-rc.3): image digests used in the mixed-version readiness attempt. These are not asserted to be rc.5's pins.
- Team-pasted EC2 output from 20 October 2026 and the host evidence paths in §4.5: actual readiness outcome, missing-key agent result and runner isolation flags. Full host artifacts still need to be retained in repository evidence; do not infer a hosted pass from these logs.
