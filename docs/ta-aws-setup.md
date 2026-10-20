# TA setup: Demo 2 on a fresh AWS instance

This guide uses only the **rcran-v1** and **development-v1 ten-case validator bundles**, plus this repository. The bundles contain their runtime images, original sources, target configurations and frozen dependencies. Do not download the general 200-case development dataset, a separate runtime image release, or an assisted repair.

The recorded evaluation used code commit `001f3b9dd15f89665794609c8b04b73acfae167e`. The helper below restores that source and the saved full-agent limits in a separate directory. It does not promise identical model outputs or an identical host environment. The new TA wrappers have offline tests; this fresh-instance walkthrough still needs a real acceptance run.

## 1. Access and instance

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

`uname -m` must report `aarch64`. Python 3.12 on Ubuntu 24.04 is suitable; the harness uses the standard library, so no pip install is required. Record the handoff commit printed above. If the repository is private, authenticate using your approved GitHub access; do not embed tokens in a saved clone URL. Host package versions are not fully pinned by this guide.

## 2. Download exactly two official bundles

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

## 3. Offline checks, then the recorded source profile

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

Prepare the separate checkout. Do not point `--out` at an existing directory:

```bash
python3 scripts/demo2_prepare_recorded.py --out "$HOME/build-bench-demo2-recorded" &&
cd "$HOME/build-bench-demo2-recorded" &&
cat ta-recorded-profile.json &&
python3 -m harness.case_views "$HOME/buildbench-local-development-v1" "$HOME/bb-cases"
```

The helper checks out the historical commit, applies only the saved budget diff, verifies every recorded full-agent Python source hash and checks the one-shot tag. The settings are 12 attempts, 10 loop builds, 1M reported tokens, 7,200 seconds, 120 seconds reserve, 80 turns and 200 tool calls. The runner sets default strategy, history on and review off; no reference repair is supplied in live mode. Baseline and final validation builds are additional harness work. A model call can overshoot the token threshold.

Do not commit the prepared checkout's intentional local budget modifications. Record its `ta-recorded-profile.json` with the results. Source verification does not pin host kernel, Docker, QEMU or model-serving changes.

## 4. Prove the rcran pipeline without a model key

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

Expected: the positive known-fix control produces a verified repair; the negative comment-only control applies a change and fails its build. Both results must be recorded. These are scripted infrastructure controls, not autonomous successes. Inspect the new output directory's `summary.json`, console logs and `runs/*/record.json`. Stop and diagnose if either control fails its expected check. Reconnect with `tmux attach -t demo2-ta`.

## 5. Short-term Bedrock API key for live runs

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

## 6. Live evaluation: rcran plus the ten cases

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

Expect progress lines and `coverage.json`, `results.md`, `plan.json`, and per-job evidence under `jobs/`. An unchanged successful build is not a repair. A valid live failed repair is retained, not retried until success. The complete schedule can take hours; individual manifest build timeouts differ and two allow four hours. Quiet output during compilation is normal.

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

The recorded single repeat was one-shot 0/11, full agent 1/11 (rcran), with five setup-blocked and one already-building case per arm. Model behavior is nondeterministic; an identical score is not the acceptance criterion. Require valid records and actual clean-build/artifact verification for every claimed repair.

## 7. Preserve evidence and assess readiness

Retain the handoff commit, prepared profile, plan, settings, records, patches, baseline/candidate/final logs and summaries. Check that a claimed success has both validator success and passed expected-artifact validation. Review setup-stage failures separately. Stop the AWS instance after exporting the evidence.

Handoff acceptance remains pending until a fresh host passes bundle verification, offline tests, both real rcran controls and at least one recorded live invocation. The code/document update itself did not provision AWS, test website account access, call Bedrock or build packages. The five reverse cases and hosted competition qualification remain separately unresolved.
