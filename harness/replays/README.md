# Replay files

A replay file is the stand-in model's answer for one case: `{"case_id", "rationale", "edits": [{"path", "old_text", "new_text"}]}`.
`python3 -m harness.run_case --bundle <bundle> --replay <file> --run-id <id> --log <organizer log>` applies the edits and
runs the organizers' validator on the result, with no language model involved. That makes a replay the cheapest way to ask
"would the validator accept this fix?".

| file | case | fix | validator (9 Nov 2026) |
| --- | --- | --- | --- |
| `rcran-known-fix.json` | r-cran-digest | arch guard on HAVE_MM_PREFETCH | passed (3 Nov) |
| `rcran-comment-only.json` | r-cran-digest | negative control | failed, as intended |
| `libmceliece-verified-strip-bsymbolic-functions.json` | libmceliece (arm64) | `export DEB_LDFLAGS_MAINT_STRIP = -Wl,-Bsymbolic-functions` in debian/rules | passed, 298 s |
| `libmceliece-verified-gold-arm64.json` | libmceliece (arm64) | `-fuse-ld=gold` on arm64 | passed, 296 s (fallback) |
| `html-xml-utils-verified-serial-cexport.json` | html-xml-utils (arm64) | override_dh_auto_build: `dh_auto_build -- cexport` then `dh_auto_build` | passed, 276 s, LTO and hardening kept |
| `html-xml-utils-verified-no-parallel.json` | html-xml-utils (arm64) | `dh_auto_build --no-parallel` | passed, 285 s |
| `libbpp-qt-verified-optional-templinst.json` | libbpp-qt (amd64) | ` (optional=templinst)` glued to the two missing symbols | passed, 1357 s, zero parse warnings |
| `libevtx-verified-o2-on-amd64.json` | libevtx (amd64) | Ubuntu's own fix: `-O3` stripped, `-O2` appended on amd64/ppc64el | passed, 2846 s, test_tools.sh PASS |
| `libevtx-verified-optimize-guard.json` | libevtx (amd64) | `&& !defined( __OPTIMIZE__ )` on the HAVE_EVTX_TEST_MEMORY guard | passed, 3463 s, FAIL 0 |
| `ectrans-verified-cmake-redirect.json` | ectrans (arm64) | CMake loop after find_package(fiat) redirecting missing module dirs via /usr/include glob only | passed, 441 s |
| `pybigwig-verified-static-shim.json` | pybigwig (amd64) | setup.py unpacks libBigWig.a, adds a hidden `stderr` shim, links with `-Wl,-Bsymbolic` | passed, 692 s |

The nine verified files came out of the 9 November experiment campaign (`docs/campaign-2026-11-09/experiments.md`). With
the agent's own repairs of rcran, libunwind, rust-subversion, python-intbitset and libbpp-core, every one of the eleven
local cases now has a validator-accepted fix. Failed variants (bindnow, LTO off, ectrans symlink-to-FMODDIR) are
described in the experiments document and were not kept.
