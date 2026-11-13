# rc.9's edits against the rc.5 edits the validator passed

13 November 2026, 16:40-17:50 UTC. `devval-v060rc5.zip` is the bundle of rc.5's edits that scored 53 of 111 in Development Validation; `devval-v060rc9.zip` is a fresh run of rc.9 on the same cases. Both bundles were unpacked case by case and the net edit of each was diffed against the unmodified source (`~/cmp-rc5-rc9/` on the EC2 host). A fresh run of plain rc.8 on the 53 arm64-target cases serves as the control (`~/cmp-rc5-rc8/`).

## The model does not write the same edit twice

| | cases | identical edit | different edit |
| --- | --- | --- | --- |
| rc.9 against rc.5, the 53 cases that passed | 53 | 18 | 35 |
| rc.9 against rc.5, arm64 cases that passed | 30 | 7 | 23 |
| rc.8 against rc.5, arm64 cases that passed | 30 | 7 | 23 |

rc.8 differs from rc.5 only in two guards, so the second and third rows say the same thing: a rerun reproduces about a quarter of the passing edits, whichever version runs.

## Do the different edits still build? (arm64, native builds)

Control first: rc.5's own passing edits were rebuilt in the same harness for the 23 arm64 cases whose rc.9 edit differs. 22 of 23 pass (libgovirt fails; its test has a start-up race). So for these cases the harness agrees with the validator.

| arm64 cases | rc.5 on the validator | fresh rc.9 run, native | fresh rc.8 run, native |
| --- | --- | --- | --- |
| the 30 that passed in rc.5 | 30 | 23 | 24 |
| the 25 that did not | 0 | 11 | 6 |
| total of 55 | 30 | 34 | 30 |

- Lost by both fresh runs: node-node-sass, libgovirt, pam-tmpdir, debugedit.
- Lost only by rc.9: harec, gvars3, lua-struct (resolute). Lost only by rc.8: python-msgspec, libpdl-ccs-perl.
- Gained by rc.9: lib1305, efibootmgr, ocaml, libmbim, liboqs, tasmanian, rapmap, lua-struct (oracular), xmbmon (questing), libcrypt-xxhash-perl, r-cran-randomfieldsutils. libmbim and efibootmgr also build unmodified in this harness and were confirmed separately under the log's conditions (round 16 and 17 of `no-build-live-findings.md`).
- Gained by rc.8: libcvd, libmbim, liboqs, tasmanian, rapmap, lua-struct (oracular).
- Not built for rc.8: ocaml (rc.8 has no repair for it). Not built for either: libmaus2, sphde, libyuv. xsimd failed in this rc.9 run (the model's own header edit) and passed in an earlier one.

## amd64 cases

Emulated builds are slow; at 17:50 UTC five of the twelve differing rc.9 edits were built (cysignals, libinsane, reglookup on plucky pass; golang-github-mmcloughlin-avo and python-apt fail) and the rc.5 control builds had not started. The reviews below judge all twelve by reading.

## Reviews by reading

Three reviewers compared each pair of edits with the failure log (`review-amd64.md`, `review-arm64-a.md`, `review-arm64-b.md`). Of the 35 differing edits: 12 equivalent, 8 sound, 2 doubtful, 13 likely to fail. Where a build exists it agrees with the review in every arm64 case but two: gir2pas and libsieve were judged likely to fail, and both build. The two judged doubtful (python-reportlab on mantic, libpdl-ccs-perl) build as well. The losses share a pattern: the edit treats the symptom, not the cause (a second stale object left in pam-tmpdir, an always-true unsigned comparison in lua-struct, a test's expectation rewritten in node-node-sass, make-level lines written inside a recipe in gvars3).

## What it means

A fresh run of either version loses six or seven of the thirty arm64 cases that passed in rc.5 and gains others. The 53 is one draw, not a floor that keeping the old version preserves. On arm64, where both versions could be built like for like, rc.9 ends four cases ahead of rc.8, all from recipes the controller applies; its losses among the known passes are within one case of rc.8's.
