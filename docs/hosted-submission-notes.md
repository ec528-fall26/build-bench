# Hosted submission notes

Moved out of the Demo 2 design document on 21 October 2026, unchanged except for headings. These notes cover the organizers' hosted platform, which is **not** needed to reproduce the Demo 2 results. Section references such as "§4.5" point to the [design document](design-document.md) as it was before the move; the local readiness check is now its Experiment 5.

## 1. Hosted contract and required adaptation

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

## 2. Hosted qualification plan (not yet executed)

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
