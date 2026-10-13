# Failure taxonomy

**Owner:** Owen Zhang (Part E) · **Last updated:** 13 October 2026
**Status:** two categories observed, both from the frozen baseline on one case.
The remaining categories are definitions waiting for full-agent runs.

The proposal's Demo 3 milestone asks for "a failure taxonomy with concrete
traces". This file is that record. A category counts as **observed** only with a
run id and the evidence that puts the run in it; everything else is listed as
defined, with no count.

## Evidence so far

Six runs of the one-shot agent on one case, rcran
(`launchpad-mantic-amd64-arm64-r-cran-digest-7a42effc961f`), same model and
prompt ([`part5-findings.md`](part5-findings.md) §9 and §9a):

| Runs | Agent | Repaired | Not repaired |
| --- | --- | ---: | ---: |
| `live-1`, `live-2`, `live-3` | pre-freeze `0.1.0+b7956fa2035c` | 3 | 0 |
| `v1-rcran-1`, `v1-rcran-2`, `v1-rcran-3` | frozen `1.0.0+16e31a424d0d` | 1 | 2 |

One case cannot say how common a category is. It can only show that it exists.

## Observed failures

### F1. Path copied from the build output

The model used a path exactly as the compiler printed it. Compilers print paths
relative to their working directory, so the path did not exist from where the
edit ran.

| | |
| --- | --- |
| Run | `v1-rcran-2`, not repaired, `termination_reason: build_failed`, fix type `build_commands` |
| Log line the model read | `crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: No such file or directory` |
| What it did | Added `sed -i … crc32c/crc32c_prefetch.h` to an override in `debian/rules` |
| Real location | `input/src/crc32c/crc32c_prefetch.h`: the compiler ran in `src/` |
| Build log of the attempt | `sed: can't read crc32c/crc32c_prefetch.h` |
| Recognise it by | An edit or build command naming a path that is not a file under `input/`, while a file with that suffix exists deeper in the tree |
| Addressed by | Part A resolves each printed path to the real file (`Diagnostic.resolved`); the prompt gives that path and forbids copying a log path; `find_files` confirms a path (Part B); the build's new error reaches the model (Parts C, D) |

### F2. Declining a feasible repair

The model returned no edits although a repair within the rules existed.

| | |
| --- | --- |
| Run | `v1-rcran-3`, not repaired, `termination_reason: no_edit_applied`, fix type `none`, 11,096 / 1,157 tokens in / out |
| Its reason | It believed only the files it was shown exist, and that fixing source would need a new file, so "a supported repair cannot be made" |
| Why that was wrong | Any existing file under `input/` may be edited, and a direct source edit is recorded as a Debian patch automatically. The verified fix adds four lines to an existing header (§8) |
| Recognise it by | No edit proposed, with a stop reason about permissions, unseen files or having to create files, on a case that another run or a person repaired |
| Addressed by | The prompt states both facts and that not being shown a file is never a reason to stop; tools let the model read the file (Parts B, E) |

A stop is not always this failure. A case whose build needs a dependency that
does not exist for the target architecture cannot be repaired by an edit, and
stopping is then correct. Label F2 only when a feasible repair is known.

## Observed in runs that were repaired

These did not fail. The benchmark scores them like any other repair; a reviewer
would not. They are recorded because the full agent's prompt asks for direct
source edits, and Demo 3 reports fix types next to the repair counts.

| ID | Observation | Runs | Evidence |
| --- | --- | --- | --- |
| Q1 | Build-time rewrite instead of a source edit | `live-1`, `live-2`, `live-3`, `v1-rcran-1` | Every repair added commands to `debian/rules` that change a source file during the build; all four are `fix_type: build_commands`, `needs_review` |
| Q2 | Repair depends on the order of build steps | `live-3` | The edit runs after `dh_auto_build`. It works only because the R build system compiles during `dh_auto_install`; "in most build systems it would be too late" (§9) |
| Q3 | Change broader than the fault | `live-2`, `live-3` | The x86 include is commented out on every architecture, not guarded for the target. Harmless here because that code path is already bypassed (§9) |
| Q4 | Second defect hidden behind the first failure | `v1-rcran-2` | The override also ran `dh $@` instead of `dh_auto_configure`; the build stopped at the `sed` error first, so it was never reached (§9a) |

## Defined, not yet observed

These follow from how the full agent works. None has a recorded trace; the count
for each is unknown, not zero. Field names assume the run record described in
[`part-e-prompts-evaluation.md`](part-e-prompts-evaluation.md).

| ID | Category | Recognise it by |
| --- | --- | --- |
| F3 | Malformed reply | `parse_turn` returned `malformed`: no action object, unknown tool, missing fields, reply cut off. Six of six baseline-format replies on rcran parsed (plan); the new format has not been tried |
| F4 | Edit rejected | Proposed edits not applied: `old_text` not found exactly once, a path outside `input/`, or Part 4's policy (disabled tests, excluded architecture) |
| F5 | Wrong hypothesis | The edit applied and the build's first error is unchanged |
| F6 | Partial repair | The first error changed or the build reached a later stage, and it still failed when the run ended |
| F7 | Regression | An attempt did worse than the best so far and was undone |
| F8 | Repeated attempt | The same hypothesis or the same edit proposed again after it failed |
| F9 | Budget exhausted | The run ended on attempts, builds, tokens or time with a repair still in progress |
| F10 | Unproductive investigation | Tool calls that return nothing usable: invalid arguments, the same request repeated, or the budget spent before any edit |
| F11 | Not repairable by an edit | A correct stop: the failure needs something outside `input/` |
| I | Infrastructure failure | `unpack_failed`, `repack_failed`, `build_error`, `baseline_error`, `run_crashed`, `run_timeout`. Counted separately and kept in the denominator; says nothing about the agent |

## Labelling

1. Every run that is not repaired gets one primary category, assigned by a
   person who has read the run record, the diff and the build output. The
   termination reason narrows the choice; it does not decide it
   (`build_failed` can be F1, F5 or F6).
2. A label needs a pointer: a log line, a `file:line`, or the attempt number in
   the run record. No pointer, no label; the run is left as "unlabelled".
3. Q-observations are recorded for repaired runs whose diff was read, which is
   every run flagged `needs_review`.
4. Counts are reported per configuration with the number of runs behind them,
   and unlabelled runs are shown, not dropped.

| Run | Configuration | Category | Pointer | Source |
| --- | --- | --- | --- | --- |
| `v1-rcran-2` | `one-shot-v1` | F1 | Build log of the attempt: `sed: can't read crc32c/crc32c_prefetch.h` | `part5-findings.md` §9a |
| `v1-rcran-3` | `one-shot-v1` | F2 | The run's rationale | `part5-findings.md` §9a |
