# Build-Bench Model Access

Version: v0.1 · 15 October 2026

This guide explains how your Agent can call the organizer-provided model in the hosted runtime. The platform supplies the model base URL, model alias and task credential before the Agent starts. Use these settings; no personal API key is needed in your submission.

The local Starter Kit supports development, checks and packaging; it does not provide official model credentials or a development API allowance. Test hosted model access through Hosted Smoke Test when available to participants.

This guide describes Chat Completions integration and examples. **For model specifications, currently supported parameters and features, feature availability and limits for each stage, follow the latest [Model Access specification](https://matrix.cstcloud.cn/build-bench/submission.html?lang=en#model-access) on the competition website.** Check that specification before using optional model features; bundled examples are not an exhaustive list of supported capabilities.

## 1. Read the platform configuration

Your Agent runs in an isolated sandbox without direct outbound network access. Use the OpenAI-compatible model address supplied by the platform. Configuration is ready before the entrypoint starts, so you can initialize the model client in your entrypoint.

| Environment variable | Meaning |
| --- | --- |
| `BB_MODEL_BASE_URL` | API base URL including `/v1`; pass to `OpenAI(base_url=...)`, without appending `/chat/completions` |
| `BB_MODEL_NAME` | Organizer model alias; pass unchanged as `model`, without hard-coding a supplier model |
| `BB_MODEL_TOKEN_FILE` | Read-only, short-lived credential file for this Run/Case; read its contents into the SDK's `api_key` |

Read all three settings directly rather than hard-coding a host, port, model name or credential path. All teams use the organizer-configured model. Task credentials are supplied by the platform; you do not need to apply for or submit a supplier API key.

Task credentials are valid only for the corresponding run. Do not include them in ZIPs, logs or error messages. `GET /v1/models` can check the credential and model alias; confirming model generation also requires a completed Chat Completions request.

## 2. Request format and current supported scope

Generation endpoint: `POST /v1/chat/completions`. Python SDK: `client.chat.completions.create(...)`. Use the Chat Completions message and parameter format.

### Check the latest website specification

The website's [Model Access specification](https://matrix.cstcloud.cn/build-bench/submission.html?lang=en#model-access) maintains the current information on:

- Model specifications, per-request output limits and calling limits for each evaluation stage.
- Availability and parameter formats for optional features such as tool calling, streaming and strict JSON Schema.
- Supported reasoning-effort, sampling and parallel-tool-call options.

Adapt your Agent to the currently published scope. A parameter's presence in an SDK does not establish that it is supported in the hosted environment. Function-argument schemas and strict final-answer schemas are distinct features; check the corresponding guidance for each.

### Basic request conventions

| Field | Usage |
| --- | --- |
| `model` | Use the injected `BB_MODEL_NAME` alias |
| `messages` | Nonempty Chat message array; maintain history per Case, not across Cases |
| `store` | Explicitly send `false`; no shared server-side conversation state |
| `n` | Omit or set to `1`; multiple candidate completions are excluded |
| `max_completion_tokens` | Per-request output limit; see the latest website specification for allowed values and behavior when omitted. Example values are not competition limits. Do not also send other output-limit fields |

The legacy `max_tokens` field is not recommended for new examples; never combine it with `max_completion_tokens`. `max_output_tokens` belongs to Responses and is not used for Chat.

Use the platform-provided model address and task credential rather than connecting to personal model endpoints or external services. Sandbox permissions and allowed resources are governed by the competition rules and the latest website specification.

## 3. Minimal Python example

The example uses Python 3.11, `openai==2.54.0` and `httpx==0.28.1`. Pin dependencies in `requirements.lock`. If using another version or framework, check that its request format follows this guide.

```python
import os
from pathlib import Path
import httpx
from openai import OpenAI

with OpenAI(
    base_url=os.environ["BB_MODEL_BASE_URL"],
    api_key=Path(os.environ["BB_MODEL_TOKEN_FILE"]).read_text().strip(),
    max_retries=0,
    timeout=httpx.Timeout(620.0, connect=3.0),
    http_client=httpx.Client(
        trust_env=False, headers={"Accept-Encoding": "identity"}
    ),
) as client:
    result = client.chat.completions.create(
        model=os.environ["BB_MODEL_NAME"],
        messages=[{"role": "user", "content": "Reply with OK."}],
        max_completion_tokens=128,
        store=False,
    )
    if result.choices[0].finish_reason != "stop":
        raise RuntimeError("Incomplete model response")
    print(result.choices[0].message.content)
```

**128 tokens and 620 seconds are illustrative, not competition limits.** Run this example in a hosted environment supplying the settings above. It demonstrates model-client initialization and one request; a complete Agent must also repair the worktree and write `agent-result.json` according to the submission protocol. The downloadable `model-access-example.py` includes credential and response checks for use as an adapter reference.

## 4. Function tools and streaming

When using function tools or streaming supported by the current website specification, follow the message-handling practices below. Check the website for available parameters and combinations.

1. Preserve the original assistant message containing `tool_calls`.
2. The Agent validates tool names, arguments, allowed paths and permissions, then executes allowed operations.
3. Return each result as `role: tool` with its original `tool_call_id`, and continue with the complete `messages` history.

Your Agent executes tools; the model only requests calls. Validate model-generated arguments and follow allowed paths and runtime permissions before executing a tool. Do not execute unchecked shell commands. The sandbox does not provide a Docker socket or direct access to the final Validator.

For SSE, assemble text and tool-argument deltas, then check `finish_reason` (`stop` or `tool_calls`, depending on the stage) and terminal usage after the stream ends. The final usage chunk may have empty `choices`. Treat length-limited, refused or interrupted responses as incomplete; HTTP 200 or partial text alone is not sufficient.

## 5. Errors, retries and caching

Disable SDK automatic retries with `max_retries=0`. If model initialization or a request fails, log the failure stage, exception type and HTTP status without credentials or sensitive request content. If the error prevents your Agent from continuing, exit nonzero rather than reporting the failure as `status: completed`.

| Status | Action |
| --- | --- |
| 400 / 413 | Check parameters, message shape and request/output size; correct the request using the error information and the currently published website limits |
| 401 / 403 | Check platform settings, credential-file readability and credential validity; do not substitute a personal API key |
| 409 | A model request is already running for this Case; do not submit another concurrently |
| 429 | The task has reached a limit, or calls are paused because usage is uncertain; stop new requests |
| 502 / 504 | A model-service request failed or timed out; record the failure stage and error information |

After a timeout or interrupted stream, retain the error information. Do not automatically retry, create another session or replace credentials to bypass task limits. For persistent access problems, contact the organizers with the submission ID, run ID, failure stage and redacted error information.

Follow the latest website specification for caching settings; do not send extra warm-up requests. Place stable instructions and tool definitions before the current Case input and subsequent tool results; do not add unrelated content just for caching. Cache hits are not guaranteed and do not increase the number of available requests.

## 6. Adaptation and testing steps

`agents/model-hello/` v0.2.2 shows how to read platform settings, call the model and edit the public hello Case. It is a reference for integration, not a general repair solution. Its request count and limits are example settings, not competition requirements.

Prepare your Agent in the following order:

1. **Adapt model configuration.** Read `BB_MODEL_BASE_URL`, `BB_MODEL_NAME` and `BB_MODEL_TOKEN_FILE` in your adapter. If you previously used custom variables such as `BB_LLM_ENDPOINT` or `OPENAI_API_BASE_URL`, map the platform settings yourself; the platform supplies only the three settings listed here. Distinguish the SDK base URL from a full request endpoint.
2. **Check and package locally.** In the Starter Kit root, run `./bb check --agent <Agent-directory>` and `./bb package --agent <Agent-directory>`. Local `bb test` and `bb ready` do not provide the official model channel; model-dependent Agents need the hosted environment to test integration.
3. **Upload and run Hosted Smoke Test.** Upload the packaged ZIP and, after static checks pass, run Hosted Smoke to check model initialization, Agent execution and final repair validation. Hosted Smoke does not contribute to the official ranking.

Run `./bb model-check` in the Starter Kit root to check the bundled model example's SDK calls, file edits and error handling using simulated responses. This makes no real model requests and does not test your own Agent. Your submitted version must still pass Hosted Smoke Test.

Dependencies may be downloaded when preparing the runtime image. Agent execution remains isolated, with model requests using the platform-provided channel.

After your Agent exits, the platform replays its final worktree changes on a clean Case, independently builds the package and checks artifacts. A successful model call does not imply a successful repair; use the final validation result. For build-feedback availability and limits, consult the latest website specification and competition announcements.

Development Validation checks uploaded repaired worktrees; it is not a test of your Agent's model integration.

**Existing submissions:** If your Agent already reads the platform settings, rerun Hosted Smoke on that version when model access is enabled; no duplicate upload is needed. If you change your adapter, package and upload a new version. A previous Smoke pass does not mean that version has been tested for model access.

Request-count, output, runtime and other resource limits for each stage follow the latest website specification and competition announcements, with the same limits applied to all teams in that stage. Do not treat example settings as those limits.

## References

These documents explain Chat message formats, tool calling and streaming. When integrating with Build-Bench, use the platform configuration in this guide and follow the latest website specification.

- [Build-Bench Model Access specification (current capabilities and limits)](https://matrix.cstcloud.cn/build-bench/submission.html?lang=en#model-access)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)
- [OpenAI Chat streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)

