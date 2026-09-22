# Demo 1 evidence

This directory records the team's September 21, 2026 hello smoke test and the earlier downloaded-dataset inspection. It is an evidence archive, not an agent submission.

## Recorded run

The supplied rc.2 starter kit ran on Ubuntu 24.04.3, x86_64, Docker 29.1.3, 2 CPUs and approximately 3.7 GiB RAM. The final build log ends at 2026-09-21 23:27:24 UTC. Source: `starter-kit/runs/demo` in the team VPS workspace.

| Check | Recorded result |
| --- | --- |
| Initial build | failed |
| Example agent | completed |
| Canonical patch | applied |
| Final build | succeeded, exit 0, no timeout |
| Expected artifacts | binary RPM and source RPM passed verification |
| Final validator build duration | 9 seconds |

JSON and logs are copied unchanged from the run. `repair.diff` shows the supplied example's known marker replacement. Artifact files are retained so their recorded hashes can be independently checked. The two hashes were also verified directly on the VPS. This example uses no LLM and is not evidence of cross-architecture repair ability, general-agent performance, or total workflow latency. The local validator reports privileged mode; it is the team's trusted local smoke environment, not a claim of hosted evaluation isolation.

## Reproduce on the prepared team VPS

From `/root/build-bench-workspace` on the already configured VPS:

```bash
./bb doctor
./bb demo
```

The wrapper sources `environment.sh` and invokes the unmodified starter kit. It uses:

```bash
export BB_AGENT_IMAGE=python:3.11.9-slim-bookworm
export BB_VALIDATOR_IMAGE=ghcr.io/terriyyy/buildbench-validator-runtime@sha256:11151f88f6b12c578b9c9fafd0420d6da27c6969b5909aaa4c51e78dd9acd7c7
export BB_EXAMPLE_ASSETS_IMAGE=ghcr.io/terriyyy/buildbench-example-assets@sha256:98bdf0f445cf0296f67ffc8b4601ab1cb1f82b957f84bd9739276f5659cdde54
export BB_CLEANUP_IMAGE=ubuntu:24.04
```

On another compatible Linux host, use the branch's starter-kit 0.1.0-rc.2 copy or obtain the same official release, install Git and Docker, set the above image overrides, then run `./bb doctor` and `./bb demo` from the kit directory. Image pull access and the existing compatibility configuration are prerequisites. This is a smoke-test recipe, not a complete 200-case evaluation setup. Images referenced by tags are not fully immutable.

Expected result: initial failure followed by `status: succeeded`, `patch_applied: true`, `build_exit_code: 0` and `artifact_validation_passed: true` in `runs/demo/build-result.json`. Build duration can vary; 9 seconds is only the recorded observation. RPM bytes/hashes may vary across rebuilds because timestamps can change.

## Check the archived evidence

From the `design-proposal-draft` folder:

```bash
python3 experiments/check-demo1-evidence.py
```

This verifies the saved statuses, actual artifact hashes/sizes, and dataset count invariants. It does not execute a new build. Full logs and the canonical patch support inspection of the run. `SHA256SUMS` covers the evidence files except this README and the checksum file itself.

## Dataset provenance

`dataset-summary.json` is an extract from `analysis/dataset-analysis.json` in the downloaded-materials archive identified by its SHA-256 in that file. It records 200 cases, 188 package names, the two 100-case directions, and checksum inspection results. Full inputs, the analysis script, and per-case records are available through the repository-root `materials/README.md`; the checksum-verified archive is the source snapshot. Signal categories are overlapping string matches, not ground-truth root causes. The summary predates the successful hello build and says no package builds were executed during that dataset analysis.
