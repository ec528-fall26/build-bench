# Build-Bench hello model-access example v0.2.2

This source-only Agent demonstrates injected model configuration, one Chat Completions generation and a constrained source-edit tool. It repairs only the public hello RPM; it is not a general repair baseline. The model receives the source and initial failure evidence and selects an edit. The Agent does not request a build during its run. After it exits, the platform independently validates the final worktree.

## Configuration

The hosted runner must supply `BB_MODEL_BASE_URL` (including `/v1`), `BB_MODEL_NAME` and `BB_MODEL_TOKEN_FILE` before launching the entrypoint. Read the short-lived task credential from the supplied file; never package a personal API key. No provider address, model version, host port or credential path is hard-coded. Outbound networking remains disabled; the controlled model channel must already be enabled by the runner.

Python 3.11; dependencies are pinned in `requirements.lock`. The adapter uses the OpenAI Python SDK, `max_retries=0`, `store=false`, `trust_env=false`, explicit timeouts and a single function tool. The tool accepts one bounded deletion inside the public hello spec's `%build` section; it never inserts or runs model-generated shell commands. Model configuration or response errors cause a nonzero exit and a safe failure stage, rather than a false success.

## Lifecycle

1. Initialize the configured model client and check the local model alias.
2. Read the public hello spec and initial failure log from the prepared workspace.
3. Make one generation request, validate the returned tool name and arguments, and apply the permitted edit.
4. Write `/workspace/output/agent-result.json` and exit. `completed` means the repair attempt completed, not that the package has built successfully.

The single request, 1024-output-token cap and 620-second timeout are example settings, not official competition limits or iteration requirements. No in-run build service is required by this example. Official build-feedback arrangements remain to be determined uniformly for all teams.

## How to test and submit

This example is bundled in Starter Kit 0.1.0-rc.5. The kit does not provide official model credentials or access from local `bb test`. Hosted access depends on the organizer enabling and configuring the controlled model channel; downloading or packaging the Agent does not enable that channel.

Run `./bb model-check` from the kit root to check actual SDK serialization, the permitted edit, negative responses and safe diagnostics against synthetic HTTP. This does not contact any provider and is not hosted qualification. Use `./bb check --agent ./agents/model-hello` and `./bb package --agent ./agents/model-hello --output dist/buildbench-model-hello-v0.2.2.zip` to inspect and package this example. A model-dependent Agent cannot use local `bb ready` without a model channel because that command includes the local runtime test.

See `model-access/guide.en.md` in the full kit for configuration, supported parameters and adaptation steps. If you change the model adapter, check and package the updated Agent, upload it as a new version, and run Hosted Smoke Test when model access is available.
