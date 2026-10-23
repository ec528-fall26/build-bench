# Demo 2: live one-shot evaluation

Experiment: `demo2-oneshot-retest-20261019T212712Z`
Run checkout commit: `001f3b9dd15f89665794609c8b04b73acfae167e`

Frozen one-shot agent; one live run per case; no reference repair supplied.
All ten development cases were processed, plus the separate rcran case.

| Set | Cases processed | Verified repairs | Termination reasons |
| --- | ---: | ---: | --- |
| Development bundle | 10 | 0 | build_failed: 5, case_not_failing: 1, no_edit_applied: 4 |
| Rcran | 1 | 0 | build_failed: 1 |

The bundle result is 0/9 among cases requiring repair.
`html-xml-utils` built unchanged and receives no repair credit.
Rcran failed in this repeat; earlier successes are separate experiments.

This completes single-repeat one-shot evaluation coverage, not a claim
of repair effectiveness or a completed full-agent comparison.

The accompanying summary.json preserves the per-case counts, usage and
wall times emitted by the harness. Full logs, diffs and detailed records
remain on the evaluation host under `/home/ubuntu/buildbench-experiments/demo2-oneshot-retest-20261019T212712Z`.
