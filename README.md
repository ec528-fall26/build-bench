# EC528 Build-Bench Project

**Team:** Anthony Capraru, Austin Li, Joonseo Moon, Juliette Jacques, Owen Zhang

**Mentor:** Minghua Ma, Microsoft

**Demo 1 presenters:** Owen Zhang and Joonseo Moon

We propose an LLM agent that diagnoses cross-architecture package build failures, edits permitted source or packaging files, and submits its work for a clean target build. Demo 1 presents the design, evaluation plan and verified starter-kit smoke evidence. A general repair agent and public-case evaluation are still pending.

**Review status:** Ready for team review. Approval of the Demo 2, Demo 3 and final milestones is pending; the proposal is not yet frozen.

## Demo 1 materials

- [Design proposal](docs/design-proposal.md)
- [Presentation PDF](slides/demo-1.pdf) and [editable PowerPoint](slides/demo-1.pptx)
- [Architecture diagram](docs/diagrams/architecture.md), [diagram PDF](docs/diagrams/architecture.pdf) and [editable source](docs/diagrams/architecture.mmd)
- [Presenter notes and timing](docs/demo-1-speaker-notes.md): Owen, slides 1–4; Joonseo, slides 5–8; 9:40 total
- [Q&A preparation](docs/demo-1-qa.md)
- [Evidence and Linux reproduction recipe](docs/evidence/demo-1/README.md)
- [Current design document and evidence-check command](docs/design-document.md)

## Check the saved evidence

From the repository root, with Python 3.9 or newer:

```bash
python3 experiments/check-demo1-evidence.py
```

Expected: both `PASS` lines, confirming the saved hello failure-to-success sequence, two RPM artifact hashes and dataset-summary counts. This command checks saved files; it does not run Docker, call a model or rebuild a package.

We plan to run course-scale experiments on AWS with reproducible x86_64 and ARM64 environments. Provisioning and configuration verification are pending. The VPS was used only for the initial Starter Kit smoke test.

The recorded final validator build took 9 seconds. The supplied hello agent uses a known marker replacement and no LLM. It does not establish repair performance on the 200 Debian development cases.

## Repository layout

| Path | Contents |
| --- | --- |
| `docs/` | Proposal, presenter notes and saved evidence |
| `slides/` | Demo 1 PDF and editable PowerPoint |
| `experiments/` | Script to verify the saved evidence |
| `src/` | Reserved for the proposed repair agent |
| `buildbench-starter-kit-0.1.0-rc.2/` | Existing starter-kit copy and previous run files |

The full downloaded dataset is available through the [materials archive](https://github.com/ec528-fall26/build-bench/blob/624f2b6/materials/README.md). Demo 1 evidence is included directly on this branch so it can be checked without downloading the full archive.

The course collects Demo 1 from `demo-1` at noon on September 23, 2026. See the [course submission instructions](https://ec528.github.io/ec528/fall26/setup/).
