# Demo 2: one-shot and full-agent evaluation diagnostics

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
