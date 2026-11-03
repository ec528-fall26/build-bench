# Demo 2 results (19 October 2026)

The paired one-shot and full-agent evaluation, the one-shot arm's details, and the
Rust budget experiments run alongside it. Merged on 2 November from
`demo2-comparison-diagnostics.md`, `demo2-one-shot-results.md` and
`demo2-handoff-2026-10-19/`; the text is unchanged apart from headings and links.
This is a historical record: the x86 setup failures below were later fixed with
QEMU 10.2.3 (`docs/evidence/qemu-fix-20261102/`), and the submitted agent now runs
without build feedback (`no-build-live-findings.md`).

## Result and scope

Both agents completed all eleven scheduled evaluations: the ten-case development bundle plus the separate rcran case. The frozen one-shot repaired **0/11**; the full agent repaired **1/11**, rcran. Neither repaired a development-bundle case. This is a completed single-repeat evaluation, not a successful ten-case repair demonstration and not evidence of a statistically reliable improvement.

Inspection of the original logs changes the interpretation of the failures: **all five x86-target cases encountered QEMU crashes and failed during build-environment setup before compiling the package.** They remain in the recorded results, but cannot fairly be treated as five ordinary source-repair failures. `html-xml-utils` already built unchanged and receives no repair credit. Four development cases reached genuine package failures on ARM64; neither agent repaired those four.

| Population | One-shot | Full agent | Interpretation |
| --- | ---: | ---: | --- |
| All scheduled cases | 0/11 | 1/11 | Raw verified repair counts; includes blocked and already-building cases |
| Development bundle | 0/10 | 0/10 | All ten processed in both arms |
| Bundle cases reaching source-repair evaluation | 0/4 | 0/4 | ectrans, libmceliece, libunwind, rust-subversion |
| Environment-blocked bundle cases | 5 | 5 | All five aarch64 → x86_64 cases |
| Already-building bundle cases | 1 | 1 | html-xml-utils |
| Separate rcran case | 0/1 | 1/1 | Autonomous full-agent success, no reference patch supplied |

The earlier “0/9 failing cases” wording excludes only the unchanged success. It is a valid raw grouping, but includes five setup failures; it must not be presented as nine successfully reproduced source-level bugs.

## Provenance and settings

The supplied archive is `demo2-comparison-diagnostics.tar.gz`, SHA256 `34e5a27a0ff3e5cdb7b65d78b941a36e6724f4e140886529d9926fcf43f96e05`. This review checked all 22 summary rows against their original `record.json`, including outcomes, edits, usage and wall time. Selected line-numbered log evidence and hashes are in [audit.json](evidence/demo2/paired-retest/audit.json); original records, patches, plans and summaries are retained under [paired-retest](evidence/demo2/paired-retest/).

- One-shot experiment: `demo2-oneshot-retest-20261019T212712Z`.
- Full-agent experiment: `demo2-full-agent-retest-20261019T225339Z`.
- Both plans record commit `001f3b9dd15f89665794609c8b04b73acfae167e` and Python 3.12.3. The live model identifier is `openai.gpt-oss-120b-1:0`.
- Full agent: **default strategy, history on, proposal review off**. Limits were locally raised to 12 attempts, 10 loop builds, 1,000,000 tokens, 7,200 seconds, 120 seconds reserve, 80 turns and 200 tool calls. The source hashes and local settings diff are preserved. These are not committed default limits.
- Each arm ran once per case. Neither received a reference repair. The earlier assisted Rust successes are separate experiments and receive no autonomous credit here.

The total reported model tokens were 237,904 for one-shot and 929,655 for full agent across their respective eleven-case schedules. Summed per-case wall times were 2,076.8 seconds and 4,604.2 seconds. These are sums of recorded run times, not precise launch-to-completion durations or cost estimates. Already-building cases invoked no model. The unequal budgets and single repeats do not support a controlled claim about history or efficiency.

## Per-case findings

| Case | One-shot recorded outcome | Full-agent recorded outcome | Diagnosis |
| --- | --- | --- | --- |
| rcran | build_failed; 1 edit | build_succeeded; 1 edit | Full agent disabled the invalid x86 prefetch feature in the actual configuration header. |
| libbpp-core | no_edit_applied | no_edit_applied; model_stop | QEMU crashes and passwd setup failure in both baselines. |
| ectrans | build_failed; 1 edit | build_failed; repeated_diagnostic | CMake dependency include paths, followed by missing Fortran module; full agent explored eight candidates without completing repair. |
| libmceliece | build_failed; 1 edit | build_failed; repeated_diagnostic | BFD linker assertion persisted after attempts to add PIC flags. |
| libbpp-qt | build_failed; 1 edit | no_edit_applied; model_stop | Baseline environment setup failed. One-shot added an unavailable build dependency; its candidate became unresolvable. |
| python-intbitset | no_edit_applied | build_failed; repeated_diagnostic | Setup crashes were misattributed by the full agent to documentation imports; removing Sphinx work did not solve setup. |
| libunwind | no_edit_applied; 1 proposed | build_failed; repeated_diagnostic | One-shot path lacked input/. Full-agent libgcc-link changes did not resolve the hidden-symbol link failure. |
| rust-subversion | no_edit_applied; 9 proposed | no_edit_applied; edit_correction_budget | One-shot paths lacked input/. Full-agent batch correction exhausted its allowance before any candidate build. |
| libevtx | build_failed; 1 edit | no_edit_applied; model_stop | QEMU/debootstrap setup failure; one-shot dependency change did not repair the environment. |
| html-xml-utils | case_not_failing | case_not_failing | Valid binaries from unchanged source; no model repair needed. |
| pybigwig | build_failed; 1 edit | no_edit_applied; model_stop | QEMU crashes and passwd setup failure, not an evaluated package-source repair. |

### Why rcran succeeded

The full-agent diff changes `HAVE_MM_PREFETCH` from 1 to 0 in `input/src/crc32c/crc32c_config.h`. Its loop build succeeded, and the separate final validation also succeeded with `artifact_validation_passed: true`, producing `r-cran-digest_0.6.32-1_arm64.deb` and its debug-symbol package. This establishes a real autonomous local repair.

The one-shot added a `sed` command after `dh_auto_build` and named `src/crc32c_portable.cpp`. That does not correct the feature configuration before the failing compilation. Its actual final build failed. This comparison illustrates the value of locating the responsible configuration, but a single outcome does not isolate the effect of tools, history, source access or model variability. Disabling the feature globally was not tested for performance or cross-architecture regressions.

### Why the reverse-direction results need qualification

The ten reverse-direction baseline logs contain QEMU crash messages. libbpp-core and pybigwig end in `build_setup` with passwd post-installation errors; libbpp-qt, python-intbitset and libevtx end in `build_infrastructure` with an incomplete debootstrap root and missing commands/mount points. This diagnosis is based on logs, not merely model refusals.

Frozen dependencies and a supplied validator runtime do not guarantee that host-side emulation works. These observations do not establish which QEMU/kernel/runtime combination caused the failures, nor prove that the released environments are unfrozen. An enabled binfmt handler is only a registration check, not an execution smoke test. A native x86 host is a possible isolation experiment, not a confirmed remedy.

The harness's high-level outcomes obscure this distinction: `build_failed` or `no_edit_applied` can result from setup failures. Some underlying validator results explicitly say `infrastructure_error`; others say `failed` while the stage log identifies setup. Preserve the raw verdict and add a separate stage-based diagnostic category. Do not silently relabel or remove runs.

### Why the full agent still failed on native development cases

**ectrans:** changes cleared invalid dependency include directories and advanced to compilation, but subsequent guessed include paths did not make the Fortran module available. Eight attempts show that the loop genuinely uses build feedback; they also show weak diagnosis of dependency/module installation. Generic make failures can hide more useful earlier compiler messages.

**libmceliece and libunwind:** proposed flag/link changes were plausible hypotheses, not validated explanations. Their remaining linker failures require inspecting the actual object generation and link command. Repeatedly moving flags without identifying the responsible object or runtime library did not yield a package.

**Rust:** the full-agent pending batch initially contained 18 edits. Feedback exposed ambiguous snippets such as `CStr::from_ptr(path)` and later invalid replacements. Twelve correction responses did not yield a valid batch. The summary reports zero proposed edits because no attempt was recorded, even though model replies contain a proposal. Therefore aggregate `edits_proposed` is not a complete count of model intent. The separate earlier broad/narrow redundant-replacement failure is another example, not the sole cause of this retest.

None of these facts demonstrates that the pre-build review feature failed: **review was disabled in this run**, and Rust did not reach application. More token allowance alone does not resolve bad paths, ambiguity, correction loops or environment failures.

## What to change next

1. **Make evaluation readiness explicit.** Run each unchanged case through setup first; record setup, configuration, compilation, tests and packaging separately. Investigate QEMU/setup before spending further model calls on the five reverse cases. Rerun those cases after reproducing the intended package failure.
2. **Fix evidence accounting.** Count submitted batches, proposed edits, corrected/discarded edits, applied edits, retained edits and builds separately. Preserve raw stage/status and model-stop explanations. This avoids Rust appearing to have proposed nothing and retained-candidate counts concealing reverted attempts.
3. **Improve edit correction without weakening path safety.** Present exact candidate locations and detect demonstrably redundant edits. Test against saved failed proposals offline before paying for another full benchmark. Do not auto-apply ambiguous edits.
4. **Improve diagnosis at the point of failure.** Preserve the specific Fortran/linker error and relevant build command, then inspect producers and consumers across files. Keep a build-based check; model review alone cannot verify correctness.
5. **Freeze and rerun deliberately.** Record one tested code version, budgets, host/runtime/emulator versions and source fingerprints. Run multiple repeats and a history-off arm before claiming superiority or a history benefit.

## Demo 2 claims, limitations and remaining obligations

Completed: frozen one-shot evaluation across all ten cases plus rcran; separate full-agent coverage of the same eleven; local autonomous rcran repair; traceable negative results, actual patches and build validation. The full-agent rcran result supplies a positive prototype demonstration; the one-shot run remains a valid negative evaluation.

Not completed: successful source-level evaluation of the five reverse cases in a functioning environment; controlled history ablation; reliable multi-case repair; autonomous Rust repair; a repeated-run effectiveness claim; confirmation against the separate hosted Development Validation service; current hosted packaging qualification; final clean-machine execution of the new TA handoff scripts.

The milestone is **coverage completed, effectiveness limited, reverse-direction source evaluation blocked**. Failed repairs do not erase the work performed, and recording all ten cases does not establish that all ten intended bugs were reproduced. These distinctions should appear in the design document and slides.

## One-shot arm

**Experiment:** `demo2-oneshot-retest-20261019T212712Z`
**Started:** 19 October 2026, 21:27:12 UTC
**Agent:** frozen one-shot, live model; one run per case; no reference repair supplied.

### Results

| Evaluation set | Cases processed | Verified repairs | Build failed after edits | No edit applied | Already builds unchanged |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ten-case development bundle | 10 | 0 | 5 | 4 | 1 |
| Additional rcran calibration case | 1 | 0 | 1 | 0 | 0 |
| Total | 11 | 0 | 6 | 4 | 1 |

All ten bundle cases were processed, covering both migration directions (five per direction). The evaluation coverage milestone for the one-shot agent is complete for this single-repeat run; zero repairs does not mean the evaluation was not performed.

`html-xml-utils` built unchanged (`case_not_failing`). It receives no repair credit and is excluded from the repair-required denominator. The raw result is **0/9 among cases not already building**, but log review shows five were blocked in environment setup. Only four development cases reached source-repair evaluation (0/4 repaired); retain all ten records and report these groups separately. Rcran is outside the bundle and failed in this live repeat (**0/1**). Do not overwrite earlier recorded rcran successes or describe this latest run as a success.

`build_failed` means the run reached a candidate build that did not satisfy validation. `no_edit_applied` means no proposed change was applied; it does not establish that every such run had the same rejection cause. For example, the supplied Rust row records nine proposed edits and zero applied. The paired review above assigns detailed categories from the imported logs and patches.

### Interpretation and milestone status

The pipeline processed released cases and recorded negative outcomes rather than counting model confidence or edit application as repair success. These data establish evaluation coverage, not broad repair effectiveness. The proposal promised no minimum Demo 2 repair rate. Prototype/packaging requirements remain distinct from evaluation coverage.

This single-repeat one-shot result does not measure a benefit from repair history, establish full-agent superiority, or fulfill a future three-repeat comparison. The separate full-agent retest completed all eleven cases and repaired rcran; it repaired none of the ten bundle cases. See the paired review for settings and limitations.

### Evidence and provenance

The table has now been checked against the supplied archive: all eleven summary rows match their original run records. Plans, summaries, original records and patches are included under `evidence/demo2/paired-retest/one-shot/`; selected log lines and source hashes are in `paired-retest/audit.json`. The recorded commit is `001f3b9dd15f89665794609c8b04b73acfae167e`. Original host location:

```text
/home/ubuntu/buildbench-experiments/demo2-oneshot-retest-20261019T212712Z/
  plan.json
  summary.json
  runs/runs.jsonl
  runs/<run-id>/record.json
  runs/<run-id>/repair.diff
  runs/<run-id>/baseline/
  runs/<run-id>/result/
```

The launch script checked tracked one-shot files against `one-shot-v1`; take the exact repository commit from `plan.json`, not from an earlier conversation milestone. The per-case rows and usage/runtime values are now imported and described in the paired review. No missing values have been reconstructed or counted as zero.

To recompute the totals on EC2 without any model calls or builds:

```bash
python3 - <<'PYTHON'
import json
from collections import Counter
from pathlib import Path
root = Path.home() / 'buildbench-experiments/demo2-oneshot-retest-20261019T212712Z'
rows = json.loads((root / 'summary.json').read_text())
assert len(rows) == 11
assert len({r['case_id'] for r in rows}) == 11
for name, selected in (
    ('rcran', [r for r in rows if 'r-cran-digest' in r['case_id']]),
    ('bundle', [r for r in rows if 'r-cran-digest' not in r['case_id']]),
):
    print(name, 'cases:', len(selected),
          'repairs:', sum(r['repaired'] is True for r in selected))
    print(dict(Counter(r['termination_reason'] for r in selected)))
PYTHON
```

## Rust budget experiments

These four exploratory runs used the full agent with history enabled,
based on commit `474ad0c`. They were not runs of the frozen one-shot baseline.

Case: `launchpad-plucky-amd64-arm64-rust-subversion-b3a4b8c35bb2`.

Each setting was tested once from a fresh run. None produced a verified repair.
The experiment driver prints 'Demo 3 comparison'; these results are retained
here as supporting development evidence for Demo 2.

| Token limit | Turn limit | Tokens used | Turns used | Attempts | Repair builds | Applied edits | Seconds | Loop stop |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 100,000 | 20 | 100803 | 11 | 1 | 1 | 2 | 398.1 | token_budget |
| 200,000 | 20 | 201597 | 18 | 2 | 2 | 4 | 627.6 | token_budget |
| 300,000 | 20 | 239567 | 20 | 2 | 1 | 23 | 667.1 | turn_budget |
| 300,000 | 30 | 208431 | 22 | 3 | 3 | 5 | 821.1 | build_budget |

Repair builds above count attempt records with a build result, excluding baseline builds.
Applied edits count successful replacement operations, not repaired files or retained final changes.

### Findings

- The 100k and 200k runs stopped at the token limit.
- The 300k/20-turn run stopped at the turn limit; its first attempt applied no edits.
- The 300k/30-turn run reached the three-build limit after 22 turns.
- In the final run, the first reported error moved through lines 81, 175, and 374.
- Three runs reached a remaining error at the `svn_client_checkout3` call.
- Approximately 89–92% of reported usage was input tokens.

The changing first error suggests local progress, but does not prove all earlier
errors were eliminated. Diagnostic counts are parsed summaries, not complete
compiler error counts. The recorded 'test' stage includes compilation invoked
by the package's test command; it does not establish successful compilation.

### Limitations

One run per setting cannot establish a causal or repeatable improvement.
Fresh runs produced different edits. Larger limits did not guarantee more
useful work or a repair. This is neither a hidden-case nor leaderboard result.

The archive includes logs, replies, diffs, run records, and recorded attempt
feedback. It does not establish that complete outgoing prompts and every
tool result were captured. Token-component attribution remains future work.

### Evidence provenance

- Archive: `demo2-budget-experiments-evidence.tar.gz`
- SHA256: `de679487f38d881d61cc2a08bf1592e17f0df0c1b7061de36c2ccbfff37c3ff3`
- The raw archive remains outside Git.
- [rust-budget-experiments.json](evidence/demo2/rust-budget-experiments.json) contains selected fields from the four archived run records.
- Budget settings come from the documented execution session; the archive
  also contains the final temporary budget/turn settings diff.

### Development handoff (19 October)

From the handoff written after these experiments; the commits are on `demo-2`.

#### Completed changes

The following changes were committed and pushed to `demo-2`:

- `935e646`: Rust diagnostic parsing and configuration reporting fixes.
- `cca1763`: skip a model follow-up when reported token usage exhausts the budget.
- `100ebc6`: bound fallback build-log context and mark truncation.
- `044c1b2`: exclude Rust warnings from error diagnostics.
- `1561f69`: exclude quilt backup files from diagnostic source resolution.
- `dc6810e`: clarify edit batches and unique replacement context.
- `7e1f19c`: bounded feedback for rejected full-agent edits.
- `bf39923`: regression coverage for correction after an ambiguous edit rejection.
- `474ad0c`: individual edit outcomes and recorded attempt feedback.

This is a milestone list, not a complete Git log.
The baseline agent was not intentionally changed by this work.

#### What the evidence shows

The agent applies some useful edits, but continues to encounter missing or
ambiguous replacement text, repeated investigation, and remaining type errors.
Token, turn, and build limits each stopped different experiments.
Raising limits alone has not produced a verified repair.

The final experiment applied five replacements across three attempts and
reached the remaining checkout-call error. Another run applied 23 replacements
in one attempt but also failed. Edit count alone is not a success metric.

#### Next work, in order

1. Inspect the full compiler explanation and patch around `svn_client_checkout3`,
   including expected versus actual argument types and the binding declaration.
2. Check whether the model received that detailed explanation or only a headline.
   Improve focused compiler feedback if necessary.
3. Inspect repeated reads, searches, and truncated outputs. Provide precise file
   ranges and better definition locations where the evidence supports it.
4. Add logging for prompt-component sizes and tool activity needed to measure
   investigation costs. Protect credentials and avoid unnecessary source logging.
5. Test targeted changes offline, including partial edit rejection and rollback.
6. Run one fresh Rust experiment with explicit settings and a new output folder.
7. Once a repair succeeds, repeat it and evaluate other eligible cases.

A manual development repair can help establish solvability, but must be labeled
as manual. Autonomous evaluation must start from the original failing source
without being supplied that repair.

#### Evaluation discipline

- Keep original evidence and the frozen baseline unchanged.
- Record commit, local diff, model, configuration, and all resource limits.
- Compare tokens to first edit, rejected edits, turns, builds, and verified repairs.
- Separate exploratory tuning on this public case from broader evaluation.
- Do not infer general improvement from a single run.
- Download and verify evidence before stopping EC2.
