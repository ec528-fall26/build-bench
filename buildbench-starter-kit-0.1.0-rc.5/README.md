# Build-Bench Starter Kit

Build-Bench evaluates software-repair Agents against reproducible package-build cases. This Starter Kit provides the local runner, an example Agent, example cases, submission checks, and packaging commands needed to develop an Agent before uploading it to the competition platform.

Version **0.1.0-rc.5** fixes patch generation for added and deleted text files, including new Debian patch files. Model-access guides, the model hello Agent and the offline SDK self-check remain available. It does not include official credentials or enable local model access. See [中文说明](model-access/README.zh.md) and [release notes](RELEASE-NOTES.md).

## Prerequisites

Install only:

- Git
- Docker Engine 24+ or Docker Desktop with Linux containers enabled

Use Bash on Linux or WSL2 with Docker integration on Windows. Scripts use LF line endings. If your ZIP extractor loses executable permissions, run `chmod +x bb runner/*.sh runner/build-case-docker` from the extracted root.

You do not need to install Python, RPM tooling, `obs-build`, or package dependencies on the host. The Starter Kit runs those components in organizer-provided containers.

## Quick start

### Coding Agent path

Open this directory in your coding assistant and ask it to read `AGENTS.md` first. The assistant can then use the two high-level commands:

```bash
./bb bootstrap my-agent --json
# Let the coding assistant implement agents/my-agent/src/
./bb ready --agent ./agents/my-agent --json
```

`bootstrap` prepares an editable Agent workspace. `ready` snapshots that Agent, runs the released local tests and submission checks, and creates `dist/agent-submission.zip` only when every step succeeds.

This default path uses the no-model template. `ready` includes local `test` and does not inject a hosted model channel. Use the model-based workflow below for model-dependent Agents.

### Human path

```bash
# Download and extract the versioned archive.
cd buildbench-starter-kit-0.1.0-rc.5

./bb doctor
./bb demo
```

`./bb demo` checks Docker, prepares the example Agent and example case, reproduces the initial build failure, runs the Agent, generates a canonical patch, and validates the repaired package.

Successful output points to:

```text
runs/demo/build-result.json
runs/demo/build.log
runs/demo/repair.diff
```

## Create your Agent

```bash
./bb init my-agent
```

This creates:

```text
agents/my-agent/
??? agent.yaml
??? requirements.lock
??? src/
    ??? __init__.py
    ??? main.py
```

Normally you only edit `agents/my-agent/src/`. Update `requirements.lock` when dependencies change, and update `agent.yaml` when the Agent name, version, runtime, or entrypoint changes.

Use this development loop:

```bash
./bb test --agent ./agents/my-agent
./bb check --agent ./agents/my-agent
./bb package --agent ./agents/my-agent
```

The packaged submission is written to:

```text
dist/agent-submission.zip
```

Upload that ZIP on the competition website and run Hosted Smoke Test. Its results do not contribute to the official ranking. Select one qualified final version before the deadline; organizers will arrange official evaluation.

## Model-based Agent workflow

Read [Model access](model-access/guide.en.md) or [模型接入](model-access/guide.zh.md). The hosted runner supplies `BB_MODEL_BASE_URL`, `BB_MODEL_NAME`, and `BB_MODEL_TOKEN_FILE` before the entrypoint. Use the configured Chat Completions API and short-lived task credential, never a personal key or hard-coded provider/model.

```bash
./bb model-check
./bb check --agent ./agents/model-hello
./bb package --agent ./agents/model-hello --output dist/buildbench-model-hello-v0.2.1.zip
```

`model-check` checks the bundled example using synthetic HTTP in a network-disabled container: initialization, SDK serialization, safe edits and failure handling. It makes **no real model requests**, provides **no target-build feedback**, and does **not** test your own Agent or grant hosted qualification. Dependency-image preparation may download pinned packages before the sandbox starts.

The model example repairs only public hello, then exits for independent validation; it is not a general repair baseline. Adapt your own client using `model-access/example.py`, check and package locally, then run Hosted Smoke when organizer model access is enabled. Local `bb test`/`bb ready` retain the original no-network sandbox and **do not provide the hosted model channel**. Merely exporting model variables on the host does not inject them into that sandbox.

The kit does not include official model credentials or a development API allowance. Test your adapter through Hosted Smoke when model access is available. Example settings are not official limits; Smoke and formal evaluation limits will be announced separately.

## Commands

| Command | Purpose |
|---|---|
| `./bb doctor` | Check Git, Docker, runtime images, host architecture, and bundled assets. |
| `./bb demo` | Run the complete official example from initial failure to successful validation. |
| `./bb model-check` | Synthetic offline SDK/example self-check; no provider requests. |
| `./bb bootstrap NAME [--json]` | Check the environment and create a coding-agent-ready workspace. |
| `./bb init NAME` | Create a new managed-Python Agent from the official template. |
| `./bb test --agent PATH` | Run an Agent on the bundled local example case. |
| `./bb check --agent PATH` | Validate the submission structure, manifest, entrypoint, and prohibited content. |
| `./bb package --agent PATH` | Create a deterministic `agent-submission.zip`. |
| `./bb ready --agent PATH [--json]` | Snapshot, check, test, and package one exact Agent version. |
| `./bb version` | Print the Starter Kit version. |

Run `./bb help` or `./bb <command> --help` for command-specific options.

## Submission contents

The managed-Python submission format is:

```text
agent-submission.zip
??? agent.yaml
??? requirements.lock
??? src/
??? README.md            # optional
```

A minimal `agent.yaml` is:

```yaml
schema_version: "0.1"

agent:
  name: "my-agent"
  version: "1.0.0"

runtime:
  type: "managed"
  profile: "python-3.11"

entrypoint:
  - "python"
  - "-m"
  - "src.main"

protocol:
  version: "0.1"
```

Do not include credentials, `.env` files, API keys, hidden-case material, pre-generated repair patches, caches, virtual environments, or prior run outputs.

## Runtime interface

Each Agent instance receives an isolated workspace:

```text
/workspace/
??? input/               # read-only case metadata and initial evidence
??? work/repo/           # writable package worktree
??? output/              # writable structured Agent output
```

The Agent reads `/workspace/input`, edits only `/workspace/work/repo`, and may write `/workspace/output/agent-result.json`. Standard output and standard error are captured by the runner.

The Agent does not receive the Docker socket or start the Validator directly. This kit publishes no in-run target-build client. Official build-feedback arrangements, if enabled, will be specified uniformly; do not assume fixed repair rounds. Time, CPU, memory, model usage and network access remain limited by competition policy.

When the Agent exits, the platform compares the original case with the modified worktree and generates the canonical `repair.diff`. Formal scoring uses only that organizer-generated patch, re-applied to a clean case and validated by the Docker Validator.

## Local outputs

Local commands write disposable data under `runs/` and packaged ZIP files under `dist/`. These directories are not part of a submission.

A validation result includes:

```text
build-result.json    machine-readable status and artifact metadata
build.log            build diagnostics
repair.diff          organizer-compatible canonical patch
artifacts/           RPM/SRPM or other expected package artifacts
```

## Runtime images

The Starter Kit uses public organizer-provided runtime images. `./bb doctor` pulls missing images. This kit retains the immutable multi-architecture digests from the independently versioned runtime-image release `v0.1.0-rc.3`:

```text
ghcr.io/terriyyy/buildbench-validator-runtime@sha256:11151f88f6b12c578b9c9fafd0420d6da27c6969b5909aaa4c51e78dd9acd7c7
ghcr.io/terriyyy/buildbench-example-assets@sha256:98bdf0f445cf0296f67ffc8b4601ab1cb1f82b957f84bd9739276f5659cdde54
```

No registry account is required to pull these public images. If your organization uses a registry mirror, the image references can be overridden:

```bash
export BB_AGENT_IMAGE=<managed-python-image>
export BB_VALIDATOR_IMAGE=<validator-image>
export BB_EXAMPLE_ASSETS_IMAGE=<example-assets-image>
./bb doctor
```

The image versions published with a Starter Kit release define the supported local environment. Formal competition results are always produced by the organizer platform using its pinned evaluator version and hidden validation material.

## Troubleshooting

- If Docker is unavailable, start Docker and rerun `./bb doctor`.
- If an image cannot be pulled, verify registry access or configure the organizer-provided mirror.
- If `./bb check` fails, fix the reported file, manifest, entrypoint, or prohibited-content issue before packaging.
- If the local example build fails, inspect the `build.log` path printed by the command.
- If files under `runs/` are owned by another user, confirm that Docker supports bind mounts for the current workspace.
- For a `model_initialization` failure, check the three platform settings and credential-file readability. Local `bb test` does not supply hosted settings; test model integration through Hosted Smoke rather than adding personal keys or changing sandbox isolation.
- Model HTTP 200 or Agent `completed` does not establish repair success. `agent.log` records Agent output, whereas `build.log` records the independent final package build.
