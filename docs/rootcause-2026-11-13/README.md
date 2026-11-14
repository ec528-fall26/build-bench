# Root causes of the 58 non-passing cases of the rc.5 Development Validation run

13 November 2026. Run DV-20261109T183008Z-c9781158: 111 small public cases, 53 passed. `docs/devval-rc5-triage.md` sorted the other 58 by reading logs. This directory goes one step further: six investigators reproduced the failures with real package builds wherever the EC2 host could build them, found the cause in the source, and built a fix. Their reports are `g1.md` to `g6.md`; each case there has the evidence lines, the diff and the build result.

How to read "verified": a real `dpkg-buildpackage` in a container of the case's Ubuntu release. arm64 targets were built natively. amd64 targets run under QEMU emulation on this host, so only small ones were built. Builds use today's archive unless a line says the log's package versions were restored from snapshot.ubuntu.com. None of this is the organizer's validator, which has the frozen dependency set.

## Three facts about the validator that change what a repair has to do

1. **amd64 targets are built under QEMU user emulation on arm64 hosts.** Evidence in the rc.5 validation logs: `qemu: uncaught target signal 11`, ptrace returning ENOSYS, Go's `lfstack.push` crash on guest addresses above 2^47, and build times 10 to 20 times the originals. The original Launchpad logs come from real amd64 hardware. So an amd64 case can fail in validation for reasons no edit touches: the 4 hour limit (pyfftw, python-pysam, jellyfish, ocaml, libcrcutil), missing ptrace (python-ptrace), missing Linux AIO (dqlite), the Go runtime (golang-v2ray-core), wall-clock tests (python-pyinstrument).
2. **The validator runs the full build (`debian/rules binary`) on both architectures, with `-j2`.** Launchpad built arm64 with `dpkg-buildpackage -B` (arch-only) and amd64 with `-b`. Two consequences. An arm64 failure that exists only because the arch-only build did not install Build-Depends-Indep (libmbim: `gtkdocize`) does not exist in the validator, and the job is to change as little as possible. And an amd64 failure may sit in a step the arm64 build never ran (documentation, arch-all packages), in which case it has nothing to do with the architecture (python-reportlab, fmtlib, pynfft, tmpreaper, python-pysam, python-pyaudio).
3. **A change to an upstream file reaches the build as a quilt patch, and the unpatched copy stays under `.pc/`.** A build that compiles every source file below the package root compiles that copy too (libcrypt-xxhash-perl).

## The cases

`fix` is the investigators' fix, `built` says whether it was built. `agent` is what 0.6.0-rc.9 does about the cause: a recipe the controller applies, a check or hint, or nothing.

### arm64 targets (native builds)

| case | cause | fix | built | agent |
| --- | --- | --- | --- | --- |
| lib1305 | ld BFD assertion with `-Bsymbolic-functions` (binutils) | strip the flag | pass, unmodified fails | recipe `strip-bsymbolic-functions`, controller |
| efibootmgr | ld GCS warning about a dependency is fatal with `--fatal-warnings`; the maintainer's fix line is not exported | add `export` | pass with the log's binutils | recipe `gcs-report-dynamic-none`, controller |
| liboqs | new `-Werror=array-parameter` | `-Wno-error=` | pass | recipe `wno-error` (rc.8), model |
| libmbim | `gtkdocize` is in Build-Depends-Indep, the arch-only build did not install it | list it in Build-Depends | pass under `-B`, unmodified fails | recipe `promote-indep-build-dep`, controller |
| tasmanian | FMA contraction on arm64 moves a seeded MCMC test past its tolerance | `-ffp-contract=off` | pass, 26/26 | recipe `arm64-x86-semantics`, added by the controller after the model |
| xsimd | strict-aliasing violation in the NEON bool load/store, exploited by GCC 15 | memcpy, or `-fno-strict-aliasing` | both pass, 343/343 | same baseline |
| mlmmj | `char ch = fgetc(f); ch == EOF` never true with unsigned char | `int ch`, or `-fsigned-char` | 195/196 with the flag; one unrelated test fails either way | same baseline |
| xmbmon (2 copies) | x86 port I/O header: `sys/io.h`, then six inline-asm functions | x86 conditional with stubs of the same names, `SET_IOPL()` returning -1 | pass, both releases | playbook entry; verdict "x86 assembly still compiled"; syntax check on `((void)0)` |
| rapmap | calls to `ksw_extz2_sse41/_sse2` that only exist on x86, in branches a run-time CPU check never takes on arm64 | put those branches under an x86 conditional | pass | playbook entry; syntax check on a misplaced body |
| libcrypt-xxhash-perl | `Build.PL` compiles every `.c` below `.`, including the x86 dispatcher and, after an edit, its `.pc/` copy | change `c_source` in Build.PL, or remove the file in debian/rules | pass, with the quilt round trip | playbook entry; verdict "this edit cannot take effect" |
| r-cran-randomfieldsutils | load-time `Available()` raises where cpuid is missing, then an x86-only SSE baseline check | return false, and guard the baseline check | pass | none (the model gets the first half) |
| libelas | hand-written SSE2 in four files, no fallback, no simde in the dependencies | a 57-line NEON stand-in header on the include path | pass | none: needs a new file, which the agent cannot create |
| obs-time-source | CMake generator expressions leaked into the installed `libobs.pc`; meson passes them to cc | filter the `.pc` in debian/rules | pass | none |
| rust-generator | the crate has no aarch64 context switch | 75-line port (asm and Rust) | pass, 30 tests | none: a port |
| drawterm | no `posix-aarch64` directory; the 32-bit ARM one does not assemble | new directory with an atomic `tas` | pass | none: needs new files |
| hippomocks | runtime code patching writes 32-bit ARM opcodes on AArch64 | AArch64 branch (`ldr x17; br x17`) | pass | none |
| libcvd | out-of-bounds in the non-SSE Gaussian convolution for tiny images | bounds-safe small-image path | pass | none |
| sphde | memory barriers are compiler-only outside x86 and powerpc | real barriers, one more read barrier | `make check` 20/20 | none |
| libmaus2 | cache line size read as 0 when sysfs lacks it; aligned `new` with alignment 0 | fall back to sysconf, then 64 | mechanism reproduced, package not built | none |
| golang-v2ray-core | `go:linkname` to `net/http.readRequest` with one argument too many (Go 1.17 stack ABI) | drop the argument | test passes on Go 1.17 and 1.18 | none |
| ocaml | 41 tests expect empty compiler output and get `-Wlto-type-mismatch` warnings | `optimize=-lto` | pass, 3546 tests, 0 failed (53 min) | recipe `disable-lto`, controller |
| libapache2-mod-auth-openidc | libcjose rejects the RSA1_5 test vector; not architecture-specific | none within the rules | n/a | none |
| libyuv | one NEON rotate test leaves bytes unwritten | not found | not reproduced | none |
| lua-struct | listed in the triage, passed | | | |

### amd64 targets (emulated in the validator)

| case | cause | fix | built | agent |
| --- | --- | --- | --- | --- |
| eegdev | dpkg-buildflags 1.22.5 emits `-fno-cf-protection`, which gcc rejects | strip it | pass with the log's dpkg, unmodified fails | recipe `strip-buildflag`, controller |
| globalplatform | tests linked with `--wrap` in an LTO build | `optimize=-lto` | pass, 5/5 (emulated); unmodified fails | recipe `disable-lto`, controller |
| bzrtp | new `-Werror=stringop-overflow` | `-Wno-error=` | not built | recipe `wno-error` (rc.8), model |
| python-reportlab | `sys.version[:3]` is "3.1" on Python 3.10; docs step only | take the version from `sys.version_info` | pass | recipe `python-version-slice`, added by the controller after the model |
| tmpreaper | `build-stamp` runs the program before anything builds it; only `-b` uses that target | `build-stamp: build-arch` | pass (emulated) | none |
| cxref | per-architecture defines file missing for amd64 (`_Float128` in glibc headers) | add the file | pass (emulated) | none: needs a new file |
| fmtlib | Sphinx 7 dropped the `style` template variable; then `dh_sphinxdoc` wants `data-content_root` | two lines in the theme | indep step passes | none |
| pynfft | Cython 3: relative cimports, `noexcept`; then `build_sphinx` removed | three edits | pass (built on arm64, arch-independent) | `replace-removed-command` covers the third |
| python-pysam | a `.docs` file lists a file the source does not ship (arch-all package) | drop the line | not built; 4 h limit under emulation anyway | none |
| python-pyaudio | `dh_linktree` on a stub jquery package (archive state) | drop the two lines | unmodified passes today | none |
| python-cython-blis | GCC 15 removed Xeon Phi: `-mavx512pf`, `-march=knl` and friends, 37 lines in each of 5 files | rewrite both flag shapes | the 37 commands compile | block lists the files and lines |
| dynarmic | code uses the Zydis 4 API, the frozen set has 3.2.1 | port back | not built | none |
| kissat | the LTO-built binary crashes at start (`optimize=+lto` in debian/rules) | `optimize=-lto` | pass (emulated), test program runs | recipe `disable-lto`, controller |
| libotr | a unit test calls `otrl_auth_clear` on an uninitialised struct | initialise it | that test passes | none |
| python-pyinstrument | a test loop uses `and` where `or` is meant; wall-clock tests then fail under emulation | fix the loop | not built | none; emulation |
| python-ptrace | test expects `link(` where Python 3.14 uses `linkat(`; then no ptrace under emulation | relax the pattern | our rc.5 edit already did | emulation |
| alertmanager-irc-relay | test installs its JOIN handler after the event | install it first | not built | none |
| sbsigntool | `objcopy --target=efi-app-x86_64` no longer auto-detects the ELF input (binutils 2.46, inference) | `-O` | not built | none |
| zim-tools | `std::regex` recursion depth on a PNG (inference) | not established | not built | none |
| libengine-gost-openssl, python-pyppmd, jellyfish, lua-luv, cowsql, opentelemetry-cpp | crashes or flakes not pinned down | none | | none |
| grub2-signed | compares against a signed binary the archive had not published yet | none within the rules | | |
| dqlite, golang-v2ray-core, pyfftw, libcrcutil, ocaml | emulation or the 4 hour limit | none | | |
| libgnomecanvasmm2.6, libopaque | rc.5 added a Build-Depends outside the frozen set | do not | | rc.8 guard |

## What this says about the agent

Counting the 58 by what would have been needed:

- **A flag or a line that a recipe can write, and a signature that identifies it** (14): lib1305, efibootmgr, eegdev, liboqs, bzrtp, libmbim, globalplatform, kissat, ocaml (both copies), tasmanian, xsimd, mlmmj, python-reportlab. All have a recipe as of rc.9, each confirmed by a real build except bzrtp; twelve are applied by the controller without asking the model.
- **A small, local source edit that the log and one file point to** (about 9): xmbmon (2), rapmap, libcrypt-xxhash-perl, r-cran-randomfieldsutils, tmpreaper, python-pysam, libotr, python-pyaudio. The model gets these right some of the time; the rc.9 playbook entries, verdicts and syntax checks are aimed here.
- **A real port or several coordinated edits** (about 14): libelas, rust-generator, drawterm, hippomocks, libcvd, sphde, libmaus2, golang-v2ray-core (arm64), obs-time-source, fmtlib, pynfft, python-cython-blis, dynarmic, cxref. Out of reach for a model that cannot build, and three need new files, which the agent's edit format does not allow.
- **Nothing an edit can do** (about 14): the emulation and time-limit cases, grub2-signed, libapache2-mod-auth-openidc, the unpinned crashes.
- **Our own mistakes in rc.5** (the rest): the two Build-Depends additions, the invalid worktrees.
