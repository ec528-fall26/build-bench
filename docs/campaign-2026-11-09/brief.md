# Campaign 1 designer's brief

Source: campaign1-result.json (11 autopsies with 3 votes each, 3 libevtx investigations, 5 reviews, 6 research sweeps, 1 critic). Where a vote refuted a claim, the vote's corrected_claim is used. "(inf)" marks inference. Cases are dev cases; hidden-set frequency is the autopsy's own "common/occasional" call. Agent paths below are relative to buildbench-starter-kit-0.1.0-rc.5/agents/full-agent/src.

## 1. The 11 cases

| Case | Dir | Outcome | True root cause (corrected) | Maintainer-quality fix | Our accepted repair maintainer-quality? | Class | Hidden freq |
|---|---|---|---|---|---|---|---|
| rcran (r-cran-digest, mantic) | amd64>arm64 | Repaired, 10 turns, 1 edit | Vendored crc32c_config.h hard-codes HAVE_MM_PREFETCH 1, which gates the x86-only xmmintrin.h (log 1995) | Direct edit of input/src/crc32c/crc32c_config.h guarding the define with __x86_64__/__i386__, else 0 (validated). The proposed quilt patch was malformed. | Yes | Frozen x86 feature macro in a vendored config header | common |
| libunwind (plucky) | amd64>arm64 | Repaired, 2 turns | GCC 14 aarch64 emits hidden outline-atomics helpers (__aarch64_cas8_acq_rel) from libgcc.a. libunwind.so links -nostdlib, so test programs fail to link (log 2177) | debian/rules, arm64 only: -mno-outline-atomics via DEB_CFLAGS/CXXFLAGS_MAINT_APPEND | Yes. The answer came from a hard-coded recogniser, not model analysis. | Arch-default ABI/ISA feature vs link | occasional |
| rust-subversion (plucky) | amd64>arm64 | Repaired (rc3 36 turns; rc2-main hit the 60-turn budget) | Crate hard-codes *const i8/*mut i8 for C char*. char is signed on amd64, unsigned on arm64, so bindgen c_char is u8 on arm64 (error log 14741) | Replace with std::ffi::c_char in src/{client,delta,dirent,io,repos,string,uri}.rs, not generated.rs. The validated diff is a direct input/src edit (63 hunks, 55 c_char lines) that also added unsafe to 20 extern fns. The "do NOT add unsafe" claim was refuted. | Yes (over-edited) | Platform-dependent C type alias hard-coded | common |
| html-xml-utils (resolute) | amd64>arm64 | Passed, 40 turns, 8 attempts | Parallel-make race: the .c.e rule runs `$(MAKE) cexport` recursively, so under -j8 two sub-makes build cexport at once. One rewrites cexport.o while lto1 reads it (ICE, log 1377). The race is not arm64-specific. | Build cexport once, serially, before the parallel build in debian/rules. This is the autopsy's own proposal and was never built. | No. It strips LTO, -O2 and hardening, and the race is still there. Passed by side effect. | Parallelism race with a misleading symptom | occasional |
| ectrans (noble) | amd64>arm64 | Miss (fiat-only edit), then repaired | The arm64 libfiat-dev 1.2.0-1 exports targets fiat, parkind_dp and parkind_sp whose INTERFACE_INCLUDE_DIRECTORIES name a nonexistent .../fortran/aarch64-linux-gnu-gfortran-13/<name>. Real modules are in /usr/include/<triplet>/fortran/gfortran-mod-15/<name>. CMake's check fails at configure. Amd64 contrast and "build-time artefact" origin unproven. | In-input edit after ecbuild_find_package(fiat) that redirects the three targets to the existing mod dir by glob (validated in rc3b). The upstream-proper fix is in fiat-ecmwf and out of scope. | Partly. The autopsy flag is false. The edit review calls it incomplete because the installed targets file is untouched. | Dependency's exported CMake config defect | occasional |
| libmceliece (noble) | amd64>arm64 | Not repaired (toolchain) | AArch64 BFD ld asserts at elfnn-aarch64.c:9754 (finish_dynamic_symbol, GOT path) while linking libmceliece.so with 62 GNU ifunc symbols. The ifunc dispatch at scripts-build/dispatch:111 is the likeliest trigger. -Bsymbolic-functions is a hypothesis. The "no workaround" claim is not established. | None verified. Untested ladder: strip -Wl,-Bsymbolic-functions, -fuse-ld=gold, ifunc avoidance. Not -fPIC or -lrandombytes. | n/a (no edit) | Toolchain-internal assertion | occasional |
| libbpp-qt (noble) | arm64>amd64 | "Repaired", accidentally | Only debian/libbpp-qt2t64.symbols.amd64 exists, so symbols were enforced only on amd64. Qt template instantiations drifted: 2 missing (QList detach, RefCount deref), 2 new detach_helper | Edit that file, tagging the 2 symbols optional=templinst, with no space after the closing paren | No. The accepted edit has a space the dpkg parser rejects (log 2130-2136). dpkg dropped those lines, which made the error go away. | Stale per-arch symbols file | common |
| python-intbitset (noble) | arm64>amd64 | Miss, then repaired | The amd64 builder runs sbuild -A, which runs the -indep targets. debian/rules:7 runs `python3 setup.py build_sphinx`, which no longer exists (log 1512-1521). arm64 never ran it (inf). | Replace debian/rules line 7 with `python3 -m sphinx -b html docs/ docs/_build/html`. Extras (nodoc guard, -N) unvalidated. | Yes | Removed tool call in an arch:all/-indep step | common |
| libbpp-core (jammy) | arm64>amd64 | 2 misses, then repaired (macro run) | dpkg-gensymbols reports 12 missing weak std:: template instantiations in libbpp-core4.symbols.amd64 (g++-11, log 1776-1918). 14 new symbols are only a warning. | Prefix the 12 missing symbols with (optional), no space, keeping versions | Yes, for the macro run only. rc3b passed by switching the check off. | Stale symbols file | common |
| pybigwig (resolute) | arm64>amd64 | Not repairable in-package. Now stops correctly (still scores unrepaired). | On amd64 libbigwig0t64 0.4.8+dfsg-2 ships no .so, so the libBigWig.so symlink dangles and ld falls back to the non-PIC libBigWig.a (R_X86_64_PC32 vs stderr, log 1365). Why arm64 passes is unverified (the PIE/GOT claim is a hypothesis). | Fix belongs in libbigwig (0.4.8+dfsg-3 un-emptied the package; pybigwig 0.3.23+dfsg-2 rebuilt against it). No verified in-package edit. An objcopy symbol-rewrite hack is untested. | n/a. The -fPIC edit was a miss. | Dependency package contents differ | occasional |
| libevtx (plucky) | arm64>amd64 | Not repaired | See section 2. | See section 2. | n/a | Test-only crash from interposer scaffolding under -O3 | common |

## 2. libevtx (test_tools.sh exit 139, message_handle)

Common facts: info_handle passes, message_handle segfaults, the other six tool tests never ran (test_tools.sh stops at first failure). The cause is real on native amd64 (organizer log 3808-3814), not QEMU. Flags are -O3 -flto=auto -ffat-lto-objects -D_FORTIFY_SOURCE=3.

- Investigation 1 (static, confidence low). Suspect the interposed memset in tests/evtx_test_memory.c (HAVE_EVTX_TEST_MEMORY, enabled on amd64 and aarch64). Proposed patch: undefine the macro in tests/evtx_test_memory.h. Not reproduced.
- Investigation 2 (historical/differential, confidence "high", no backtrace). The cause is -O3 plus the fragile harness (the file warns "might fail if compiled with optimation"). The Ubuntu fix, libevtx 20240504-1ubuntu1 (Klose, 2025-01-06): "Build with -O2 on amd64 and ppc64el to avoid test failures". It adds `ifneq (,$(filter $(DEB_HOST_ARCH), amd64 ppc64el))` with DEB_CFLAGS_MAINT_STRIP=-O3 and DEB_CFLAGS_MAINT_APPEND=-O2 in debian/rules. Launchpad shows 1ubuntu1 built on 6 architectures.
- Investigation 3 (reproduced on the instance under QEMU, confidence high). Backtrace is libfcache_cache_value_free+0x32, called from libfcache_cache_free, message_handle_free and the test's on_error path. The fake memset returns NULL without clearing. GCC treats memset as never-NULL, so the `memory_set(...)==NULL` check in libcdata_array_initialize (libcdata_array.c:162-175) was compiled away, leaving a garbage entries buffer. Zeroing alone only moves the failure to "result (1) != -1". The amd64-vs-arm64 reason is partly verified: amd64 inlines the 184-byte memset as rep stos, so the injected failure lands on a later out-of-line call (arm64 side not run).
- Verified fix (investigation 3): add `&& !defined( __OPTIMIZE__ )` to the HAVE_EVTX_TEST_MEMORY condition in tests/evtx_test_memory.h. make check then showed 0 FAIL (check2.log). Historical fix: the Ubuntu -O2 downgrade above (upstream change, not built here).
- Autopsy proposal ac_cv_lib_dl_dlsym=no is unverified (info_handle passes with the same interposer). Critic and investigation 3 disagree on whether any fix is verified (inf: critic overlooked investigation 3's check2.log).

## 3. Code reviews (27 high-severity findings, effort in days)

**Prompt engineering** (113/417 first replies, 27%, had no action JSON; follow-up turns were 2.67M of 6.36M input tokens)
- Free-text-then-JSON drops the action in 27% of turns: move to native calls, interim "JSON only" prompt plus a short follow-up (0.5; prompts.py:213-226, model_adapter.py:389-413).
- Stateless per-turn rebuild, so the model re-verifies endlessly: keep a real chat history, elide old tool output by age and size (2; model_adapter.py:476-592).
- Native function-calling design replacing action JSON, macro and parser: JSON-schema read tools plus apply_edits and finish (3).
- Symbols macro hidden in prose and ignored for 14 tool turns: expose tag_missing_symbols as a tool (1; patterns.py:260).

**Loop control and budgets**
- No stop gate after an all-fixed verdict (23 wasted turns rust-subversion, 6 rcran): allow K=2 more tool turns (1.5).
- No guard on repeated identical tool requests (up to 11): cache by canonical args unless files changed (0.5).
- syntax_guard rejects valid `(optional) name` (libbpp-qt 13 correction turns, libbpp-core 9): fix the regex, add tests (1).
- No best candidate kept in no-build mode, so bad attempts are never rolled back: static rank plus snapshot (1.5).

**Evidence and verification** (a pattern block fires on 25/200 dev logs, 12.5%, 2+ false positives)
- Pattern coverage 12.5%, with no recogniser for the biggest families (GHC/gold LTO-PIC 11 cases, dh_missing): signature table (4).
- recognise() returns the first block only and fires on non-failures: ranked list, conftest filter (1.5).
- No consequence checker for edits that remove what other files consume (3).
- filter-out mis-evaluated in make conditionals: extend _functions, treat dpkg-architecture as variables (1; predict.py:427).
- arch_model facts never become preprocessor macros (char signedness, endianness, long double): generate them (1.5).
- No scan for C constructs sensitive to char signedness, long double, page size, endianness, time64 (3; arch_slice.py:35-110).
- Rust char recogniser misses other error shapes and over-counts u8: use ffi_analysis obligations (2.5).
- verify._c_diagnostic says "fixed" when the line merely disappears: return unknown outside decidable cases (1; verify.py:50-97).
- Proposal: data-driven signature table plus consequence checker (7).

**Tools and hosted runtime** (python:3.11.9-slim-bookworm, no network, 1 CPU, 1 GB, 1,020 s)
- deb_contents is blind: 3,305 of 3,394 dependency debs use data.tar.zst, requirements.lock is empty. Add zstandard==0.25.0 (0.5).
- search scans only the first 100 files per glob, so "no matches" can be wrong: time-based cap (1; tools.py:20, 270-272).
- New elf_symbols tool on pyelftools (2.5).
- New dependency-consistency audit: dangling .so symlinks, empty runtime packages, cmake export paths (2).
- No hosted-fidelity test: run the kit runner in the exact image against 3 bundles (1).

**Edit application and repair quality**
- libbpp-qt counted repaired though dpkg cannot parse the edit, and the guard is bypassed by an unchanged resend: hard-block plus normaliser (1).
- html-xml-utils repair strips every dpkg-buildflags flag: add an lto macro (optimize=-lto), guard against flag-stripping (1.5).
- No patch-size sanity rule (4,130-line rewrites accepted): per-file edit budget (1).
- Macro framework is one hard-coded macro: registry of recogniser/expander pairs (3).
- Reasoning-only replies burn ~7% of turns and retyped old_text causes not_found loops: SEARCH/REPLACE block format (4).

**Medium findings.** Prompt: stop rule biases to a guessed edit; over-investigation after a verified edit; 12.9k-char system prompt; no few-shot examples. Loop: best-of-N on first edit not implemented; verdicts can be confidently wrong (pybigwig -fPIC); local harness lacks the hosted attempt cap; 31 corrections for one symbol edit; no no-progress detector before first edit (libevtx 15, libmceliece 26 tool turns); prediction gaps for ectrans, libevtx, pybigwig, libmceliece. Evidence: symbols verifier misses over-tagging; non-PIC recogniser too narrow; x86 header recogniser partial; no sub-test mapping for libyal suites; evidence.py drops warning lines; dpkg-buildflags values unchecked; docs over-fit to 11 cases. Tools: non-OSError in a tool aborts the run and discards edits (tools.py:56-61); syntax_guard compiler check inert hosted (use tree-sitter); function-level source map tool; one transient model error ends the run. Edit: arch-flag, Rust c_char and CMake-target macros; speculative evidence-free edits accepted.

## 4. Research

**Verified numbers.** Build-bench (268 OBS packages, 163 forward, 105 reverse): GPT-5 full-file 63.19% forward, 29.52% reverse. Cumulative by iteration 1/2/3: forward 36.81/48.47/63.19, reverse 11.43/16.19/29.52. Single shot 6.13%. Iteration 1 is not a no-feedback bound because Check-Build-Result is available inside an iteration. Taxonomy: Compilation 116, Build Preparation 78, Test 42, Packaging 24, Infra 8.
**GradleFixer (2510.08640v3, Table 5, pass@1 at 30 calls), corrected:** no shell 49.2, shell only 54.3, only run_build 63.4, build+gradle 69.7, GradleFixer 74.0. The 81.4 headline is unbudgeted. The lift comes from build execution, contrary to our strategy doc.
**PhantomRun:** 4,248 first-fatal errors in 4 embedded projects, hardware dependency 61.2%. Same-project history examples give about +4 points (p=0.0366) with 7B models and a build validator.
**R4P (2510.22775):** 72.2% verification accuracy (o3 71.5%). Group-wise beats point-wise. Mini-SE 26.2 to 32.8 on SWE-bench Verified. Idea only, no weights.
**Critics (2501.16655):** isolated per-test 71.4% accuracy vs holistic 35.0, build-status F1 82.1, needs gold tests.
**Agentic Rubrics (2601.04171):** Best@16 40.6/54.2% vs 37.1/50.2 best classifier, ROC-AUC 0.886. Repo grounding matters.
**Meta neuro-symbolic (2507.18755):** ReAct 28.5% SR@1, plus static analysis 34.1%, plus tests 43.9%. Static alone adds about 5.6 points vs about 15.4 for execution.
ComBench's "cascading errors" claim is unverified.

**Hidden-set class estimate (inference, about +/-8 points).** Basis: own n=108 plucky logs, paper Table 1, 11 local cases. Excluding install-deps failures (probably filtered by the dependency lock): test-suite 30-35%; non-arch compile 10-12%; x86 intrinsics/asm/header/binary/flag 12-15%; packaging and hardcoded tuples 8-10%; toolchain/LTO-ICE/BFD 6-8%; symbols 4-10% (3.7% raw, 18% local); dependency content bugs 5-8%; link/non-PIC 4-5%; char signedness 2-5%; timeouts 2-4%; Go/Rust/Haskell 3-6%; Fortran module dir 1-3% (0 observed). Direction about 60-65% amd64>arm64. Caveats: plucky Nov 2024 to Apr 2025 over-represents install-deps and toolchain breakage. The organisers' filter probably raises the mechanical share.

**Wheel verdicts.** Yes: zstandard 0.25.0 (cp311 manylinux wheels, both arches, about 5 MB, the only blocker for deb_contents); pyelftools 0.33 (pure, 196 KB, no .a support, so pair with an ar reader such as arpy 2.4.0 or in-house); tree-sitter 0.26.0 with tree-sitter-c 0.24.2, -cpp 0.23.4, -rust 0.24.2, -python 0.25.0 (both arches; verified only on a Mac). Skip: pcpp, pycparser, python-debian (shells out to unzstd), buildlog-consultant (no aarch64 wheel, GPL). Smoke-test installs on arm64 first.

**gpt-oss protocol.** All 543 replies start with <reasoning>. 91/543 (16.8%) are reasoning-only (median 587 chars, not truncations); 8 of 126 follow-ups too. Bedrock inlines reasoning as tags. Harmony treats tool calls as a first-class channel. gpt-oss calls an undefined apply_patch tool (860 times in 2604.00362), a Codex prior. Native tools are not a free fix (serving-stack Harmony bugs). Context cap 128k (131,072), so the 1.49M figure is cumulative (inf). Effort-level and structured-output evidence is anecdotal.

**Launchpad corpus recipe.** (1) getBuildRecords?source_name=X, filter exactly "Failed to build" / "Successfully built", keep asymmetric amd64/arm64 pairs, download gz logs. (2) getPublishedSources, find the first later version that builds on both. (3) changelogUrl and sourceFileUrls, diff -ru the two debian.tar.xz. (4) Pair log signature with diff and changelog text. Yield is small: 0-3 pairs per package, tried on 4 packages (libevtx, pybigwig, ectrans, fiat). Plan a few hundred pairs for 20-40 signature classes. The arch_tag parameter is not a filter. The public zcyyc/Build-bench repo has 1 case and no corpus. Legitimacy of mining Launchpad data is unconfirmed (inf).

## 5. Critic's gaps

1. No signature-to-fix corpus; recipe run on only 4 packages. Run fixchain.py on about 100 seeds, distil 20-40 rules.
2. libevtx has three conflicting readings. Build all candidate fixes and run all 8 tool tests.
3. libmceliece cause and ladder untested. Capture the real link line and try each rung.
4. pybigwig "no edit possible" never tested. Inspect libBigWig.a, try objcopy/shim.
5. ectrans verified only in glob form. Read fiat-targets.cmake, compare amd64.
6. Edit-quality problems the verdicts miss (libbpp-qt, html-xml-utils, tag-everything pass). Re-grade all 11.
7. Research claims rest on single-agent reading. Hosted-smoke probe, log finish_reason, A/B native tools.
8. Unseen classes unanalysed (test failures, tuples, prebuilt x86, asm, timeouts). Autopsy exemplars from the 108+59 logs.
9. Direction coverage thin (4 of 11 arm64>amd64, all QEMU). Compare on native x86.
10. Known verification and loop defects unranked. Fix top 10 first, add a hosted-fidelity test.
11. Best-of-N and rubric selection untested on builds. Sample 5 first edits per case and test ranking.
12. No held-out estimate; the 11-case tally is over-fitted. Build a frozen held-out set.
13. Autopsy texts contradict validated diffs. Replace with corrected versions, check line citations.
14. Rules open: stop scoring, degenerate-pass scoring, hosted model identity, corpus legitimacy, wheel installs on aarch64.
