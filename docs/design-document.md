# Design Document

Demo 1 status, September 22, 2026. The general repair agent is proposed and not implemented. The [design proposal](design-proposal.md) describes its architecture and milestones. This document will expand with the Demo 2 implementation.

## Current artifact

The completed evidence covers dataset inspection and one supplied hello smoke run on the Linux VPS. It includes the initial failure, example-agent completion, canonical patch, successful final validator result and both RPM artifacts. The example performs a known marker replacement and uses no LLM.

## Verify the archived evidence

| Item | Value |
| --- | --- |
| Supports | Slide 8: successful hello smoke run, two artifacts, 9-second final build; proposal: 200 cases, 188 packages and 100 cases per direction |
| Setup | Python 3.9 or newer; no external Python packages |
| Command, from repository root | `python3 experiments/check-demo1-evidence.py` |
| Expected runtime | A few seconds on a laptop |
| Expected output | Two `PASS` lines and the scope statement; exit code 0 |
| What it checks | Saved statuses, patch/artifact flags, artifact sizes and SHA-256 hashes, archived-file checksums, dataset-summary invariants |
| What it does not check | New Docker builds, the full dataset source checksums, model behavior, or public-case repairs |

For a new Linux hello run, follow the [recorded environment and reproduction recipe](evidence/demo-1/README.md). Network/image access and a compatible Docker host are prerequisites. Runtime and artifact bytes may differ across rebuilds.

## Implementation plan

We plan to use AWS for course-scale x86_64 and ARM64 experiments. Demo 2 work will provision and verify those environments, establish model access, reconstruct Debian cases and historical dependencies, and implement the repair loop and baseline experiments. Hosted model and build-feedback compatibility must be confirmed with the organizers. No public-case repair rate or competition qualification is claimed.
