# Development Validation of 0.6.0-rc.5: triage of the 58 non-passing cases

Run DV-20261109T183008Z-c9781158 (bundle `devval-v060rc5.zip`, 111 small public cases, one attempt each): 53 passed, 47 failed, 6 timed out, 3 invalid worktree, 2 system errors. The site reports 48.6 % (53 of 109, system errors excluded).

Method: each case's original failure log (`buildbench-development-cases-v0.1`) and its post-repair validation log were run through `evidence.diagnose` of main's rc.7 and compared; decisive lines were then read by hand. The agent's own run records (its diffs and turns) were not available, so 'our edit caused' is asserted only where the validation log shows something absent from the original log. 42 displayed logs are truncated by the site; those rows say so.

## A. Our own edit caused the loss (11)

| package | target | status | what the logs show |
| --- | --- | --- | --- |
| libapache2-mod-auth-openidc | arm64 | Invalid worktree | no change submitted (invalid worktree); original failure is a test failure |
| xmbmon | arm64 | Invalid worktree | questing copy: no change submitted (invalid worktree); x86-only sys/io.h |
| xsimd | arm64 | Invalid worktree | no change submitted (invalid worktree); original failure is a test failure |
| libgnomecanvasmm2.6 | amd64 | System error | agent added Build-Depends graphviz, not in the frozen lock: resolver error before the build (rc.8 guard covers it) |
| libopaque | amd64 | System error | agent added Build-Depends python3-setuptools, not in the frozen lock (rc.8 guard covers it) |
| efibootmgr | arm64 | Failed | agent added linker option --no-gcs, which ld rejects; the option is not in the original log |
| python-pyaudio | amd64 | Failed | agent wrote an invalid dh_linktree action 'remove' |
| rapmap | arm64 | Failed | agent's C edit in ksw2_extz2_sse.c does not parse (original: undefined reference to ksw_extz2_sse2 on arm64) |
| rust-generator | arm64 | Failed | agent's Rust edit leaves unresolved imports (original: build script fails) |
| libmbim | arm64 | Failed | after the edit automake reports GTK_DOC conditionals missing (original: gtkdocize failed) |
| libcrypt-xxhash-perl | arm64 | Failed | the build compiles the pre-patch copy under .pc/debian-changes-*/ that the repack leaves behind; the x86 dispatcher is still built |

## B. Unfixed, the log names the cause (recogniser or recipe candidates) (11)

| package | target | status | what the logs show |
| --- | --- | --- | --- |
| lib1305 | arm64 | Failed | BFD assertion elfnn-aarch64.c:10164 (binutils 2.46); -Wl,-Bsymbolic-functions is still on the link line, so the strip recipe was never applied in rc.5 |
| eegdev | amd64 | Failed | gcc: unrecognized option -fno-cf-protection, present in the original log too; configure dies |
| python-cython-blis | amd64 | Failed | gcc: unrecognized option -mavx512pf (removed from current GCC) |
| bzrtp | amd64 | Failed | -Werror=stringop-overflow (rc.8 wno-error recipe) |
| liboqs | arm64 | Failed | -Werror=array-parameter (rc.8 wno-error recipe) |
| xmbmon | arm64 | Invalid worktree | noble copy: sys/io.h still included on arm64 after the edit |
| libelas | arm64 | Failed | first error fixed (cmake_minimum_required); the next one, emmintrin.h on arm64, was predictable from the source and not addressed |
| r-cran-randomfieldsutils | arm64 | Failed | 'SIMD checks are not available on your system' at load time, unchanged |
| obs-time-source | arm64 | Failed | a CMake generator expression reaches cc literally (-fopenmp-simd), unchanged |
| globalplatform | amd64 | Failed | original: undefined reference to __wrap_RAND_bytes from an ltrans object (LTO with --wrap); validation log truncated |
| dynarmic | amd64 | Failed | original: Zydis API names not declared (dependency version drift); validation log truncated mid-compile |

## C. Test-suite or docs-step failures that need real debugging (skipping tests is refused by our rules guard) (28)

| package | target | status | what the logs show |
| --- | --- | --- | --- |
| alertmanager-irc-relay | amd64 | Failed | go test fails, as originally |
| grub2-signed | amd64 | Failed | make check fails, as originally |
| libotr | amd64 | Failed | regression tests fail, as originally |
| lua-luv | amd64 | Failed | thread test: unsupported table argument, as originally |
| lua-struct | arm64 | Passed | dh-lua test fails, as originally |
| mlmmj | arm64 | Failed | 24 of 196 tests fail, as originally |
| pynfft | amd64 | Failed | Cython compile of nfft.pyx fails in configure, as originally |
| python-pyinstrument | amd64 | Failed | 4 pytest failures, as originally |
| python-pyppmd | amd64 | Failed | original hang (150 min inactivity) became a pytest failure with a segfault |
| sbsigntool | amd64 | Failed | test.pecoff fails, as originally |
| zim-tools | amd64 | Failed | meson test fails, as originally |
| libengine-gost-openssl | amd64 | Failed | OpenSSL decoder tests fail, as originally |
| kissat | amd64 | Failed | original crash in execute_after_dh_auto_build became SIGSEGVs in the test step |
| cowsql | amd64 | Failed | original: integration-test assertion; validation log truncated |
| dqlite | amd64 | Failed | unit and integration tests fail |
| hippomocks | arm64 | Failed | original: make test fails; validation log truncated |
| libcvd | arm64 | Failed | original: tests/convolution.out exit 139; validation log truncated |
| libmaus2 | arm64 | Failed | original: testdnarank/testRank fail; validation log truncated |
| libyuv | arm64 | Failed | LibYUVRotateTest.NV12Rotate270_Invert fails |
| tasmanian | arm64 | Failed | original: test 19 wrong output; validation log truncated |
| ocaml | arm64 | Failed | original: testsuite link errors; validation log truncated |
| opentelemetry-cpp | amd64 | Failed | original: ctest fails; now CMake rejects gRPC::grpc++ include path /include at configure |
| golang-v2ray-core | arm64 | Failed | go tests with network connections fail; log truncated |
| drawterm | arm64 | Failed | original: libmachdep.a fails to build; validation log truncated |
| cxref | amd64 | Failed | docs step (readme) fails, as originally |
| tmpreaper | amd64 | Failed | build-stamp step fails, as originally |
| python-reportlab | amd64 | Failed | docs build cannot import reportlab |
| fmtlib | amd64 | Failed | original: Sphinx/breathe errors in docs; validation log truncated |

## D. Validator environment (emulation) or time limit (8)

| package | target | status | what the logs show |
| --- | --- | --- | --- |
| golang-v2ray-core | amd64 | Failed | Go runtime 'fatal error: lfstack.push', a known symptom of user-mode emulation |
| python-ptrace | amd64 | Failed | ptrace returns 'Function not implemented' under emulation |
| pyfftw | amd64 | Timeout | 4 h limit; estimate was 10-40 min |
| python-pysam | amd64 | Timeout | 4 h limit; estimate was 5-20 min |
| jellyfish | amd64 | Timeout | 4 h limit |
| ocaml | amd64 | Timeout | 4 h limit |
| libcrcutil | amd64 | Timeout | 4 h limit; last visible output is a benchmark loop |
| sphde | arm64 | Timeout | 4 h limit after the tests pass; the original build also died of inactivity |

## Recogniser coverage on the 111 original logs (main, rc.7)

A recipe matches 12 of 111 logs and 11 of those 12 passed (symbols-tag 6 of 6, o2-on-test-segfault 3 of 3, replace-removed-command 1 of 1, strip-bsymbolic-functions 1 of 2). 94 logs get no block at all; the model repaired 39 of those unaided and lost 55.
