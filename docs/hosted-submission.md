# Hosted submission

How the full agent runs on the organizers' hosted platform, how to package and
submit it, and where the submission stands. Current as of 3 November 2026.
The source of truth is the competition website
([submission page](https://matrix.cstcloud.cn/build-bench/submission.html)) and
Starter Kit v0.1.0-rc.5 (`buildbench-starter-kit-0.1.0-rc.5/model-access/guide.en.md`).
Replaces `hosted-submission-notes.md` (21 October), which described the adapter
as not yet built.

## Status

| Version | ZIP SHA-256 | Commit | Hosted Smoke | Final |
| --- | --- | --- | --- | --- |
| full-agent 0.2.0 | `cd9a754ee0941a44084d69097ad5a4f33589bb95e0af870a69b76899d76ef084` | `d19cf19` | **passed** 3 Nov (`hosted-20261103T025621Z-628b117b`, case `hello`) | not confirmed |
| full-agent 0.3.0 (`agent-improvements`) | — | — | not run | — |

Smoke passed all three checks: model access, agent execution, and independent
repair validation. Confirming a final version is due by the **end of 13
November 2026, UTC+8**. It is irreversible and needs a current Hosted Smoke
pass for that version.

## The contract, and how the agent meets it

| Requirement (website / rc.5 guide) | Full agent |
| --- | --- |
| Model settings are injected: `BB_MODEL_BASE_URL` (ends in `/v1`), `BB_MODEL_NAME` (an alias), `BB_MODEL_TOKEN_FILE`. No personal key in the ZIP. | `model_client.hosted_settings()` reads them. It checks the URL (scheme, host, no credentials, path ends `/v1`) and reads the token file (≤ 4096 bytes). Without them it falls back to `BB_MODEL_API_KEY` for local Bedrock runs. |
| Chat Completions only. Send `model`, `messages`, `store=false`, `n=1`, and `max_completion_tokens` ≤ 32,768. | Hosted requests send only those fields, with `max_completion_tokens` 16,384 and `Accept-Encoding: identity`; no `temperature`. |
| No retries, no concurrent requests. 401/403: check the credential. 409: concurrent call. 429: stop. | One attempt per request (`max_attempts=1`), one request at a time, and proxy variables are ignored. Any error ends the loop as `model_error`. |
| If model initialization fails, log the stage and error type without secrets and exit nonzero. | `main._failed` writes `failure_stage` `model_initialization`, or `model_repair` when no call succeeded, plus `error_type`, and exits 1. The token is never printed. |
| Agents cannot request official builds during a run. | Prompts say edits are not test-built; `stop` submits them. The loop has no build adapter in a hosted run. |
| The final worktree is the repair: the platform derives the diff and validates it on a clean case. | The agent edits `/workspace/work/repo/input/` in place and writes `/workspace/output/agent-result.json`. |
| Smoke limits: 10 min per request, 20 min of agent execution, 60 min in total. | Hosted runs stop at 1,020 s (`main.HOSTED_SECONDS`). Each request's timeout is capped by the time left, and no request starts with under 30 s remaining. |
| Context window about 1.05 M tokens. Smoke has no request-count limit. Official evaluation settings will be announced separately. | Our own cap is 3,000,000 summed tokens (`Budget.max_tokens`), 60 turns and 8 edit attempts. These are research settings, not competition limits. |
| Native function tools (`tools`/`tool_choice`) are supported. | Not used: the agent keeps its own JSON action format. Revisit if the hosted model's replies often fail to parse. |

Repair rounds: the website says multiple repair rounds with updated logs *may*
be arranged, to the same confirmed agent. Nothing has been announced. Every
failure in `no-build-live-findings.md` is something one more build would have
shown, so watch for this.

## Package and submit

Package on the EC2 host from a clean checkout, not on a Mac whose Desktop syncs
to iCloud: iCloud has created `* 2.py` duplicates inside `full-agent/src/`,
which `bb package` would include.

```bash
# EC2 (key ~/.ssh/build-bench-ta.pem, user ubuntu, repo ~/build-bench-demo2)
cd ~/build-bench-demo2 && git checkout <branch> && git pull
env | grep BB_MODEL            # must print nothing
cd buildbench-starter-kit-0.1.0-rc.5
./bb doctor
./bb check --agent ./agents/full-agent
./bb package --agent ./agents/full-agent       # prints Output, Bytes, SHA256
git rev-parse HEAD
python3 -m zipfile -l dist/agent-submission.zip   # agent.yaml, README.md, requirements.lock, src/ only

# Mac
scp -i ~/.ssh/build-bench-ta.pem \
  ubuntu@<ip>:~/build-bench-demo2/buildbench-starter-kit-0.1.0-rc.5/dist/agent-submission.zip \
  ~/Downloads/full-agent-<version>.zip
shasum -a 256 ~/Downloads/full-agent-<version>.zip   # must match the SHA256 above
```

Then upload the ZIP in My Submissions, wait for the static checks, and run
Hosted Smoke. Record the version, SHA-256, commit and Smoke run id in the
status table above. **Do not press "Confirm final version"** until the team
has decided.

Skip `./bb test` and `./bb ready`: locally nothing injects model access, so a
model-dependent agent cannot pass them. The kit says to use check, package and
Hosted Smoke instead.

## Local runs that match the hosted setting

```bash
read -s BB_MODEL_API_KEY       # on its own line; Bedrock short-term keys last 12 h
export BB_MODEL_API_KEY BB_AGENT_USE_BUILD_FEEDBACK=0
python3 -m harness.run_case --bundle ~/bb-cases/<case-id> --agent full-agent --live \
    --run-id <id> --log ~/organizer-logs/<case-id>.log
```

The model is gpt-oss-120b on Bedrock, not the organizers' model, so these runs
show behaviour, not the hosted score. The organizer logs come from
`materials/downloaded-materials.tar.zst`; the EC2 validator bundles do not
include them.
