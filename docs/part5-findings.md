# Part 5 — Environment and Baseline Findings

**Owner:** Joonseo Moon · **Last updated:** 1 October 2026
**Status:** harness automated and verified — real fix REPAIRED, negative control not repaired

Working notes for the harness and case runner. Numbers here are measured, not
estimated. Related: [`one-shot-agent.md`](one-shot-agent.md),
[`design-proposal.md`](design-proposal.md).

---

## 1. Headline results

| Finding | Value | Why it matters |
| --- | --- | --- |
| **First verified repair** | **`failed` → `succeeded`, binary `.deb` produced** | The full unpack → edit → repack → build loop works end to end on a real case, confirmed by the organizers' validator |
| Baseline build duration | **86 seconds** | Our provisional 30-minute per-case cap is generous, not tight |
| Unrepaired case status | **`failed`** | The case is a valid test — it reproduces its failure |
| Root cause | `crc32c_config.h` hardcodes `HAVE_MM_PREFETCH 1`, pulling in x86-only `xmmintrin.h` | The log points at the header include; the real fault is a vendored config file one level up |
| Frozen dependencies | 361, all checksums verified | Supplied by the organizers; we could not have reconstructed these |
| Local case coverage | **1 of 200** | Everything else needs website Development Validation or an organizer request |
| `dpkg-source` round trip | **verified safe** | Unpack/repack does not alter the outcome — the harness can repackage repairs |
| Repack after an edit | **requires `--auto-commit`** | Plain `dpkg-source -b` aborts on any upstream edit; the round trip could not catch this |

---

## 2. Environment

### Host

| | |
| --- | --- |
| Instance | `m7g.xlarge` (Graviton, 4 vCPU, 16 GiB) |
| Region | us-east-1 |
| AMI | Ubuntu Server 24.04.4 LTS, **arm64** (noble) |
| Storage | 80 GiB gp3 |
| Cost | ~$0.16/hr — **stop the instance when idle** |
| Name | `ec528-buildbench-arm64-joonseo` |

ARM64 is mandatory, not a preference: the supplied runtime image is
`linux/arm64`, and the organizers state the x86_64 + QEMU/binfmt path is
untested end to end.

Security group allows SSH from a single IP. On BU's network that address
changes between campus and home — update the inbound rule rather than assuming
the instance is broken.

### Setup

```bash
sudo apt-get update && sudo apt-get install -y docker.io dpkg-dev
sudo usermod -aG docker ubuntu
# log out and back in for group membership
sudo reboot                      # kernel/libc updated on first apt run
```

Installed: Docker 29.1.3, `dpkg-dev` 1.22.6 (needed for `dpkg-source`).
`docker run --rm hello-world` reports **arm64v8** — confirms native ARM.

### Local validator example

`buildbench-local-rcran-v1.tar`, 388 MB, transferred with `scp`.

```text
buildbench-local-rcran-v1/
  case/manifest.json                    validator schema 1.0
  case/input/                           .dsc + .orig.tar.gz + .debian.tar.xz
  case/config/ubuntu-mantic-proposed-arm64-buildconfig
  case/config/dependency-lock.json
  case/dependencies/ubuntu-mantic-arm64/    ~290 frozen .deb
  case/dependencies/ubuntu-mantic-all/
  runtime-image.tar.gz                  809 MB, linux/arm64
  run.sh
  tools/build-case-docker
  schema/manifest.schema.json
```

`sha256sum -c SHA256SUMS` → every entry OK.

**The runtime image loads untagged, and that is correct.** `run.sh` line 4 pins
it by digest:

```bash
IMAGE='sha256:5d7c27e0ee1e62d90d88e5170f3fa6e6448ad295ef3b87c9f23a15f6393e2967'
```

`docker images` shows it as `<untagged>`. Do not tag it; nothing is wrong.

---

## 3. Reproducing the baseline

```bash
cd ~/buildbench-local-rcran-v1
docker load -i runtime-image.tar.gz
bash run.sh --check
tmux new -s build                      # builds survive a dropped SSH session
time bash run.sh --output "$PWD/results/baseline-1"
```

`--check` validates the manifest and dependency hashes without compiling:

```json
{"lock_declared": true, "passed": true,
 "expected_count": 361, "actual_count": 361,
 "missing_count": 0, "checksum_mismatch_count": 0, "problems": []}
```

`run.sh` prints `build-case: isolated-chroot is privileged and is not safe for
untrusted input`. **Expected** — the organizers document that validation uses a
privileged container, which is why this runs on a disposable instance.

### Result

```json
{
  "status": "failed",
  "build_exit_code": 1,
  "duration_seconds": 86,
  "timed_out": false,
  "artifact_validation_passed": false,
  "target_arch": "aarch64",
  "patch_applied": false,
  "message": "build failed; expected binary artifact pattern
              'r-cran-digest_*_arm64.deb' was not matched"
}
```

Source artifacts (`.dsc`, `.orig.tar.gz`, `.debian.tar.xz`) **were** produced —
`dpkg-source` succeeded and the failure is in the build stage. No binary `.deb`,
which is the failure.

---

## 4. The failure, and what it tells us

Case `launchpad-mantic-amd64-arm64-r-cran-digest-7a42effc961f`, x86_64 → aarch64.

```text
crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: No such file or directory
```

`xmmintrin.h` is the **x86 SSE intrinsics header**. It does not exist on ARM64.
The include is guarded by `HAVE_MM_PREFETCH`, but that flag is hardcoded to 1 in a
vendored config header, so the guard never fires on ARM64 — see §8.

### What this log does and does not show

| Log line | Content | Useful? |
| --- | --- | --- |
| 1872 (last) | `dpkg-buildpackage: error: debian/rules binary subprocess returned exit status 2` | Generic |
| 1871, 1869, 1866 | `make: *** Error 2`, `Error 25`, `compilation failed for package 'digest'` | Downstream noise |
| **1858** | `fatal error: xmmintrin.h: No such file or directory` | **Symptom** — names the failing include |
| 51–1176 | eight matches for `libgpg-error0` | False positives on a keyword grep |

**Correction (1 October).** An earlier version of this section claimed a log-tail
agent "sees only lines 1860–1872 and never reaches the cause." That was wrong.
Line 1858 is 14 lines from the end, and Part 1 passes the model the last 500
lines, so on this case a log-tail agent *does* see the error.

**Second correction (3 October).** A second version of this section argued that
the fix lives in a file the log never mentions, so an agent "working from the
log alone ... cannot locate the cause." The first live run disproved that too.
There are two valid fixes: guard `HAVE_MM_PREFETCH` in `crc32c_config.h` (§8),
or guard the include at the line the log shows. GCC prints that line verbatim
(`18 | #include <xmmintrin.h>`), so the one-shot model, given only the log tail
and `debian/rules`, repaired the case by adding a build-time `sed` to
`debian/rules` (§9). The log is more informative than this section assumed.

Two smaller points still hold: a naive `grep -i error` surfaces a package
*named* `libgpg-error0` eight times before anything relevant, and the final
lines are generic. Whether the tail misses the cause on much larger logs — one
in the dataset is 2.37 GB — is untested; this 1,872-line log cannot show it.

---

## 5. Confirmed for other parts

**Part 4 — `allowed_prefix` and `patch_policy` are correct.** From the real case
manifest:

```json
"patch_policy": {
  "allowed_paths":   ["input/**"],
  "forbidden_paths": ["manifest.json", "config/**", "dependencies/**"]
}
```

The harness reads these from each case manifest and passes them to
`apply_edits`; the `input/` default needed no change.

**Caps (proposal §2).** The 30-minute per-case wall-clock cap is comfortable at
86 s per build — three in-loop builds is roughly five minutes. The manifest
declares `timeout_seconds: 3600` and `jobs: 2`, so 3600 is a ceiling for
pathological cases, not a typical duration. Do not generalise from one small R
package; re-measure as more cases become available.

**Result files for the harness run record:** `build-result.json`, `build-diagnostics.json`,
`build.log`, `artifacts/`. Map `build-result.json` fields directly rather than
inventing our own.

---

## 6. The repair loop

Local validation will not accept edited files in place. `run.sh --input` expects
`input/` to hold a **complete rebuilt Debian source package** — correctly named
`.dsc` plus every referenced archive with updated checksums, built with
`dpkg-source -b`, preserving package name and version. Editing a tarball without
rebuilding the `.dsc` is invalid, and a bare `debian/` tree where a `.dsc` is
expected is rejected.

```text
1. mkdir work && cd work
   dpkg-source -x ../case/input/*.dsc input
2. agent edits work/input/            (paths like input/src/..., matching input/**)
3. cd work/input && dpkg-source --auto-commit -b .
                                      ← the step hosted evaluation does for us
4. cp -a case repaired-case; replace only repaired-case/input/ with
   work/*.dsc and work/*.tar.*
   (manifest.json, config/, dependencies/ stay untouched)
5. bash run.sh --input "$PWD/repaired-case" --output results/<run-id>
```

Two details that matter:

- **Unpack into `work/input/`, run `-x` from inside `work/`.** The agent's paths
  then read `input/src/...`, matching the manifest's `allowed_paths: ["input/**"]`
  and Part 4's default `allowed_prefix`. It is also the `worktree/input/` layout
  website Development Validation expects. Running `-x` from `work/` puts the
  `.orig.tar.gz` where `-b` will look for it.
- **`--auto-commit` is mandatory** — see §8. Use `--auto-commit`, not `--commit`:
  the latter is interactive and would hang a script.

Step 3 exists only in our local harness. The hosted platform "supplies Case
metadata and dependencies, and packages unpacked source before the target
build."

---

## 7. Round-trip verification — PASSED

The harness must rebuild the source package after the agent edits it (§6 step 3).
Before trusting that, we verified that unpack/repack alone changes nothing.

```bash
cd ~ && mkdir -p rt && cd rt
cp -a ~/buildbench-local-rcran-v1/case .
dpkg-source -x case/input/*.dsc extracted/
cd extracted && dpkg-source -b . && cd ~/rt

cd ~/buildbench-local-rcran-v1
cp -a case repaired-case && rm repaired-case/input/*
cp ~/rt/*.dsc ~/rt/*.tar.* repaired-case/input/
bash run.sh --input "$PWD/repaired-case" --output "$PWD/results/roundtrip-1"
```

| | Original case | Rebuilt source package |
| --- | --- | --- |
| `.dsc` in `input/` | 1875 bytes (GPG-signed) | 1032 bytes (unsigned) |
| `status` | `failed` | `failed` |
| `message` | binary artifact not matched | identical |
| `duration_seconds` | 89 | 89 |

**Identical outcome.** `dpkg-source -x` followed by `-b` does not alter how the
case builds, so agent edits applied between those steps can be trusted.

`dpkg-source -x` prints a `gpgv: Can't check signature: No public key` warning.
Harmless — it cannot verify Ubuntu's signature without their keyring, and
extraction proceeds. The rebuilt `.dsc` is unsigned, which the validator accepts.

## 8. First verified repair — PASSED

With the loop proven safe without edits (§7), we applied one real fix by hand.

### The real root cause

The log points at the `#include <xmmintrin.h>` in `crc32c_prefetch.h`, but that
include is guarded. The fault is one level up, in a CMake-generated config header
that was produced on an x86 machine and vendored into the package:

```c
// src/crc32c/crc32c_config.h, line 15
// Define to 1 if targeting X86 and the compiler has the _mm_prefetch intrinsic.
#define HAVE_MM_PREFETCH 1
```

It declares x86 prefetch support unconditionally. `HAVE_BUILTIN_PREFETCH` is
already 1, and `crc32c_prefetch.h` prefers `__builtin_prefetch` over
`_mm_prefetch`, so the x86 path is never actually used — the only effect of the
bad flag is the include that fails on ARM64.

### The fix

```diff
 // Define to 1 if targeting X86 and the compiler has the _mm_prefetch intrinsic.
+#if defined(__x86_64__) || defined(__i386__) || defined(_M_X64) || defined(_M_IX86)
 #define HAVE_MM_PREFETCH 1
+#else
+#define HAVE_MM_PREFETCH 0
+#endif
```

Unchanged on x86; on ARM64 the flag is 0, the x86 header is skipped, and the
already-preferred `__builtin_prefetch` path is used. A genuine portability fix:
no test disabled, no architecture excluded.

As an `Edit` for `apply_edits` — **the known-correct answer for this case, and a
test fixture for Part 3:**

```python
Edit(path="input/src/crc32c/crc32c_config.h",
     old_text="#define HAVE_MM_PREFETCH 1\n",
     new_text=("#if defined(__x86_64__) || defined(__i386__) || defined(_M_X64) || defined(_M_IX86)\n"
               "#define HAVE_MM_PREFETCH 1\n#else\n#define HAVE_MM_PREFETCH 0\n#endif\n"))
```

`old_text` occurs exactly once, as Part 4 requires.

### The repack trap

Plain `dpkg-source -b` refuses an edited tree:

```text
dpkg-source: info: local changes detected, the modified files are:
 input/src/crc32c/crc32c_config.h
dpkg-source: error: aborting due to unexpected upstream changes
```

The package uses source format `3.0 (quilt)`, which will not rebuild with
unrecorded changes to upstream files. `dpkg-source --auto-commit -b .` records
the edit as `debian/patches/debian-changes-0.6.32-1`, appends it to
`debian/patches/series`, and builds. The `.debian.tar.xz` grows from 3236 to
4012 bytes — the patch is inside it.

The generated patch carries a DEP-3 template header full of `TODO`
placeholders. Cosmetic: it does not affect the build.

### Result

```json
{
  "status": "succeeded",
  "build_exit_code": 0,
  "duration_seconds": 87,
  "artifact_validation_passed": true,
  "message": "build completed successfully",
  "timed_out": false,
  "patch_applied": false
}
```

Artifacts now include the binary that never appeared before:
`r-cran-digest_0.6.32-1_arm64.deb` (181,994 bytes), plus the `-dbgsym` `.ddeb`,
`.buildinfo` and `.changes`.

**`patch_applied: false` is expected, and the harness must not read it as
failure.** That field reports the validator's own `--patch` mechanism. Our repair
travels inside the rebuilt source package, so the validator never applies a
patch itself. Success is `status == "succeeded"` and
`artifact_validation_passed == true`.

## 9. Live-model runs (3 October): rcran repaired 3 of 3

`python3 -m harness.run_case --live`, model `openai.gpt-oss-120b-1:0` through
Bedrock's OpenAI-compatible endpoint, all three reusing `fix-auto-1`'s baseline
log and the same agent code (`0.1.0+b7956fa2035c`; `live-1` recorded
`dc00112aabf2` only because a macOS `._edit_applier.py` metadata file polluted the
fingerprint — since fixed to ignore hidden files).

| Run | Result | Tokens in / out | Wall | Fix |
| --- | --- | --- | --- | --- |
| `live-1` | **REPAIRED** | 11,096 / 1,241 | 120 s | `override_dh_auto_configure`: keeps `dh_auto_configure`, then `sed` guards the include **for x86 only** |
| `live-2` | **REPAIRED** | 11,096 / 1,211 | 112 s | `override_dh_auto_build`: comments the include out **on every arch**, and duplicates `dh_auto_install` + the `rm` from the existing install override |
| `live-3` | **REPAIRED** | 11,096 / 1,594 | 111 s | `override_dh_auto_build`: runs `dh_auto_build` **first**, then comments the include out on every arch |

All three are legitimate by the benchmark's rules: the build passes, the expected
artifacts are produced, and none disables tests, excludes the architecture or
replaces configure. Removing the include everywhere is harmless here because the
`_mm_prefetch` path it served is already bypassed (`__builtin_prefetch` takes
priority). `live-3` works only because the R build system compiles during
`dh_auto_install` (the original error came from there), so the edit made after
`dh_auto_build` still lands before compilation; in most build systems it would be
too late.

**Findings.**

- **Not deterministic at temperature 0.** Identical prompts (11,096 tokens each
  time) produced three different fixes. The proposal's three-runs rule is needed.
- **"Repaired" hides fix quality.** The benchmark scores all three the same; a
  reviewer would rank `live-1` well above the other two. A manually reviewed
  quality label alongside the repair rate would capture this.
- **The naive baseline solves rcran.** rcran cannot show the full agent is
  better; that needs cases the baseline fails.
- **Every fix used build-time commands in `debian/rules`.** Part 4's checks see the
  edit to `debian/rules`, not what its commands do to other files, so a
  build-time command could delete tests without tripping them. These were benign.
  **Adopted:** the harness now flags such runs (`harness/diff_scan.py`;
  `fix_type: build_commands`, `needs_review`) and `harness.summarize` reports
  repair rates by fix type, so only flagged diffs need a manual read.

Cost of all three runs: under 1¢.

## 9a. Official baseline on `one-shot-v1` (3 October): rcran repaired 1 of 3

Frozen agent `1.0.0+16e31a424d0d`, same model and prompt as §9; the only change
since `live-1`–`live-3` is recording the server-reported model. Summary from
`python3 -m harness.summarize … --agent-version 1.0.0+16e31a424d0d`:

| Run | Result | Fix type | Tokens in / out | What happened |
| --- | --- | --- | --- | --- |
| `v1-rcran-1` | **REPAIRED** | build_commands | — | Same fix as `live-1`: configure override, include guarded for x86 |
| `v1-rcran-2` | not repaired (`build_failed`) | build_commands | — | `sed -i … crc32c/crc32c_prefetch.h`: the **wrong path**, copied from the log, which reports paths relative to `src/`. Build log: `sed: can't read crc32c/crc32c_prefetch.h`. The override also ran `dh $@` instead of `dh_auto_configure`; never reached |
| `v1-rcran-3` | not repaired (`no_edit_applied`) | none | 11,096 / 1,157 | The model **declined**: it believed only the files it was shown exist and that fixing source would require creating files, so "a supported repair cannot be made" |

`served_model` confirms `openai.gpt-oss-120b-1:0` for the runs that got a reply.

**Official result: 1 of 3.** Pooled with the pre-freeze runs, whose repair behavior
is identical: **4 of 6**. The variance is the finding — one case and three runs
swung between 3/3 and 1/3.

**Failure modes, for Demo 3** (the proposal's failure taxonomy):

1. **Path taken from the log as-is.** Compilers print paths relative to their
   working directory. An agent that checks a path exists before using it, or
   reads the build error after a failed attempt, would catch this.
2. **Declining a feasible repair.** The model didn't know it may edit unseen files
   under `input/`, nor that a direct source edit becomes a Debian patch
   automatically. A clearer prompt fixes this, but the baseline is frozen, so
   it is a full-agent change to measure, not a baseline edit.

## 10. Next

- [x] ~~First end-to-end verified repair~~ — §8
- [x] ~~Result recording~~ — `harness/record.py`. Record `status` and
      `artifact_validation_passed`; **not** `patch_applied` (see §8)
- [x] ~~Rejection capture~~ — `run_case.capture_warnings`, stored per run as `edit_warnings`
- [x] ~~Harness loop~~ — `harness/run_case.py`. Reproduced `fix-1` with no
      manual steps on 1 October (`fix-auto-1`: REPAIRED, 174.5 s), and a
      comment-only negative control was correctly reported as not repaired
      (`control-1`: `build_failed`, 83.2 s). Parts 1, 2 and 4 run for real;
      only the model is replayed from `harness/replays/`
- [ ] **Ask at the 2 October mentor meeting** for local environments for the frozen
      development Case IDs, and asking how teams are expected to measure repair
      rate across multiple cases during development

## 11. Open questions

- Source artifact checksums were not stable across two runs of the *same*
  unmodified case: the first run emitted a 1875-byte `.dsc`, later runs a
  1032-byte one. The case directory is verified pristine
  (`sha256sum -c SHA256SUMS` clean), so the validator appears to regenerate the
  source package rather than copy it. Unexplained; does not affect status or
  root cause, but worth understanding before we make reproducibility claims
  about artifact hashes.
- Only one case has a local environment. Repeated-run evaluation across 10 cases
  depends on the organizers supplying more; website Development Validation is a
  web form running one task at a time and cannot carry it.
- Whether 86 s is representative. One small R package is not a sample.
- The `dpkg-source` round trip is outcome-stable (verified above) but not
  byte-stable — the rebuilt `.dsc` differs from the signed original. That is
  expected and accepted by the validator.

## 12. Operational notes

- `run.sh` refuses to overwrite an existing `--output` directory and exits in
  under a second. Always pass a fresh path; a suspiciously fast run means the
  build never started.
- `tmux` is worth it for long builds only. At ~90 s these builds finish faster
  than the session is worth, and tmux's separate scrollback (`Ctrl+b` then `[`)
  confuses the terminal history. Run them directly.
- Stop the EC2 instance when idle.
