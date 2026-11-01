# Part A — what the agent could not see on ectrans, libunwind and libmceliece

**Owner:** Anthony Capraru · **Date:** 1 November 2026 · **Sprint task:** Part A, task 4
of the next-sprint plan (`docs/next-sprint-plan.md`, branch `next-sprint-plan`).

**Sources:** the 19 October paired retest records
(`docs/evidence/demo2/paired-retest/full-agent/*/record.json`) and the audit's
log excerpts (`docs/evidence/demo2/paired-retest/audit.json`). The full build
logs stayed on the EC2 host, so line numbers below are the audit's. Statements
about root causes are labelled as unverified where no repair has confirmed them.

## Summary

| Case | Stage (new classifier) | What the model was shown | What it lacked | Changed in Part A |
| --- | --- | --- | --- | --- |
| ectrans | configure, then compile after the first edit | After its first edit: 8 generic `make`/`dh_auto_build` errors and nothing else | The gfortran error itself; that the missing module comes from a dependency | gfortran errors parsed with file and line; missing-module check |
| libunwind | link | The real `/bin/ld` error | The link command; which output and library were involved | Last compile/link step attached to linker errors |
| libmceliece | link in the full log (the audit excerpt lacks the `ld` line, so on the excerpt alone: compile, inferred from `dh_auto_build`) | The real `/bin/ld` BFD assertion | The link command and which output it was building | Same |

None of these changes is a repair. They give the model the evidence that the
earlier runs show it was missing; whether that changes the outcome has to be
measured in the sprint pilot (Part E).

## ectrans (launchpad-noble-amd64-arm64-ectrans-50e45e72102d)

**Original failure: configuration.** CMake rejects imported targets from the
`fiat` dependency because they name include directories that do not exist on
arm64, for example `/usr/lib/aarch64-linux-gnu/fortran/fiat` and
`.../fortran/aarch64-linux-gnu-gfortran-13/parkind_dp` (record attempts 1, 4
and 6).

**What happened.** The agent's first edit cleared those include directories.
Configuration then passed and the build failed while compiling
`src/trans/algor/bluestein_mod.F90`:

```text
   20 | USE PARKIND1, ONLY : JPIM, JPRB
Fatal Error: Cannot open module file 'parkind1.mod' for reading at (1): No such file or directory
```

(audit, build-02 lines 1797 and 1799; the same in builds 3, 5, 7 and 8.)

**What the model was shown.** For every one of those builds, the record's
diagnostics are eight wrapper messages: `make[3]: *** [...bluestein_mod.F90.o]
Error 1`, `make[2]: *** ... Error 2`, `dh_auto_build: error: ... returned exit
code 2`, `dpkg-buildpackage: error: ...`. The gfortran error was not among them.
gfortran prints the location on one line and `Fatal Error:` four lines later,
and Part A recognized neither. Over eight attempts the agent alternated between
clearing fiat's include directories (the build then reaches compilation and
fails as above) and pointing them at other guessed paths (configuration fails
again).

**What it lacked.**

1. The error itself, with its file and line.
2. That `PARKIND1` is not part of ectrans. None of the 247 files the materials manifest
   lists for ectrans (154 of them Fortran) is named `parkind1*`. The CMake errors name it as part
   of the imported `fiat` targets `parkind_dp` and `parkind_sp`. So the module
   files are expected from the installed `fiat` package. The useful question is
   where that package installs its `.mod` files on arm64, not which include
   directory to clear.
3. The directories that actually exist in the build root. They are not in the
   log. *Not addressed here:* this needs a way to list an installed build
   dependency's files (a tool, Part B, or the dependency snapshot, Part D).

**Root cause:** not established. The evidence points at a mismatch between the
module directory that fiat's CMake configuration names on arm64 and where its
`.mod` files are installed, but no repair has confirmed this.

**General changes (not specific to ectrans).**

- gfortran's two-line errors are parsed: message, file, line, quoted source,
  and the real file under `input/` (absolute `/usr/src/packages/BUILD/...`
  paths now resolve).
- For "Cannot open module file 'X.mod'", Part A checks whether any Fortran
  source under `input/` defines `module X`. If none does, it says the module
  must come from a build dependency. The same check applies to missing headers
  (`fatal error: X.h: No such file or directory`).
- The gfortran error is printed before the `make` and `dh_auto_build` errors.
  Once it is recognized, it is the first diagnostic, which the prompt's log
  window and the loop's repeated-error check use.

On a reconstruction of build-02 (`tests/full-agent/test_evidence_context.py`),
the first diagnostic is now:

```text
Fatal Error: Cannot open module file 'parkind1.mod' for reading at (1): ...
  real file: input/src/trans/algor/bluestein_mod.F90 (line 20)
  Last compile or link step ...: [ 40%] Building Fortran object src/trans/.../bluestein_mod.F90.o
  None of the N Fortran files under input/ defines module 'parkind1': it is
  expected from a build dependency (an installed .mod file) or generated during
  the build.
```

## libunwind (launchpad-plucky-amd64-arm64-libunwind-f18b55e03a91)

**Failure: linking test programs.**

```text
/bin/ld: .libs/Lperf-simple: hidden symbol `__aarch64_cas8_acq_rel' in /usr/lib/gcc/aarch64-linux-gnu/14/libgcc.a(cas_8_4.o) is referenced by DSO
```

(audit lines 2259 and 2265, for `Lperf-simple` and `Gperf-trace` under `tests/`.)

**What happened.** Both attempts added `-lgcc_s`, first to `LDADD` and then to
`LDFLAGS`. The error did not change, and the loop stopped on a repeated
diagnostic.

**What it lacked.** The command that linked `.libs/Lperf-simple`. That command
shows which libraries and options were used and in what order. Without it the
model could only guess at flags. Context, not a verified cause:
`__aarch64_cas8_acq_rel` is one of libgcc's out-of-line atomic helpers on
aarch64. The error shows it as a hidden symbol in the static `libgcc.a`,
referenced by a shared object (DSO) involved in that link.

**General change.** Linker errors (`ld:` messages, `undefined reference`,
`collect2`) now carry the last compile or link step printed before them, for
example `libtool: link: gcc ... -o .libs/Lperf-simple ...`. With parallel make
this can belong to another job, and the detail says so.

## libmceliece (launchpad-noble-amd64-arm64-libmceliece-5020df9683fc)

**Failure: linking.** `/bin/ld: BFD (GNU Binutils for Ubuntu) 2.41.50.20231214
assertion fail ../../bfd/elfnn-aarch64.c:9754`. This is an internal assertion
in the linker, so it names no symbol or file.

**What happened.** Both attempts added `-fPIC`. The error did not change.

**What it lacked.** Which output was being linked and with what command. The
assertion line alone gives no file to investigate. The change above attaches
the last link step to this error as well.

## Setup failures (the five x86_64-target cases)

These are classified as `setup` with `repairable_in_package: false`, using the
validator's own stage events (`build_setup`, `build_infrastructure`). The
classifier records what the log shows, for example "Segmentation fault (core
dumped) (seen 14 times)" or the failed `passwd` post-install script. It does not
conclude that QEMU caused them. On the audit's excerpts, the classifier agrees
with the team's manual review on all 11 cases
(`tests/full-agent/test_failure_stage.py`). For the setup cases this mostly
reflects the validator's stage event, which the review also used.

## Not done here

- Listing an installed dependency's files (ectrans point 3): Part B or Part D.
- Using `failure_stage` to skip model repair on setup failures: Part C.
- Showing package format and failure stage in the prompts: Part E.
- A4 of the submission plan (an inventory of all 200 public cases) needs the
  full logs from the Git LFS materials archive. It is not part of this sprint's
  Part A tasks.
