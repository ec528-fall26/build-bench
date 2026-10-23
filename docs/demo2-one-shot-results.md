# Demo 2 — completed one-shot evaluation

**Experiment:** `demo2-oneshot-retest-20261019T212712Z`
**Started:** 19 October 2026, 21:27:12 UTC
**Agent:** frozen one-shot, live model; one run per case; no reference repair supplied.

## Results

| Evaluation set | Cases processed | Verified repairs | Build failed after edits | No edit applied | Already builds unchanged |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ten-case development bundle | 10 | 0 | 5 | 4 | 1 |
| Additional rcran calibration case | 1 | 0 | 1 | 0 | 0 |
| Total | 11 | 0 | 6 | 4 | 1 |

All ten bundle cases were processed, covering both migration directions (five per direction). The evaluation coverage milestone for the one-shot agent is complete for this single-repeat run; zero repairs does not mean the evaluation was not performed.

`html-xml-utils` built unchanged (`case_not_failing`). It receives no repair credit and is excluded from the repair-required denominator. The raw result is **0/9 among cases not already building**, but log review shows five were blocked in environment setup. Only four development cases reached source-repair evaluation (0/4 repaired); retain all ten records and report these groups separately. Rcran is outside the bundle and failed in this live repeat (**0/1**). Do not overwrite earlier recorded rcran successes or describe this latest run as a success.

`build_failed` means the run reached a candidate build that did not satisfy validation. `no_edit_applied` means no proposed change was applied; it does not establish that every such run had the same rejection cause. For example, the supplied Rust row records nine proposed edits and zero applied. The [paired diagnostic review](demo2-comparison-diagnostics.md) assigns detailed categories from the imported logs and patches.

## Interpretation and milestone status

The pipeline processed released cases and recorded negative outcomes rather than counting model confidence or edit application as repair success. These data establish evaluation coverage, not broad repair effectiveness. The proposal promised no minimum Demo 2 repair rate. Prototype/packaging requirements remain distinct from evaluation coverage.

This single-repeat one-shot result does not measure a benefit from repair history, establish full-agent superiority, or fulfill a future three-repeat comparison. The separate full-agent retest completed all eleven cases and repaired rcran; it repaired none of the ten bundle cases. See the paired review for settings and limitations.

## Evidence and provenance

The table has now been checked against the supplied archive: all eleven summary rows match their original run records. Plans, summaries, original records and patches are included under `evidence/demo2/paired-retest/one-shot/`; selected log lines and source hashes are in `paired-retest/audit.json`. The recorded commit is `001f3b9dd15f89665794609c8b04b73acfae167e`. Original host location:

```text
/home/ubuntu/buildbench-experiments/demo2-oneshot-retest-20261019T212712Z/
  plan.json
  summary.json
  runs/runs.jsonl
  runs/<run-id>/record.json
  runs/<run-id>/repair.diff
  runs/<run-id>/baseline/
  runs/<run-id>/result/
```

The launch script checked tracked one-shot files against `one-shot-v1`; take the exact repository commit from `plan.json`, not from an earlier conversation milestone. The per-case rows and usage/runtime values are now imported and described in the paired review. No missing values have been reconstructed or counted as zero.

To recompute the totals on EC2 without any model calls or builds:

```bash
python3 - <<'PYTHON'
import json
from collections import Counter
from pathlib import Path
root = Path.home() / 'buildbench-experiments/demo2-oneshot-retest-20261019T212712Z'
rows = json.loads((root / 'summary.json').read_text())
assert len(rows) == 11
assert len({r['case_id'] for r in rows}) == 11
for name, selected in (
    ('rcran', [r for r in rows if 'r-cran-digest' in r['case_id']]),
    ('bundle', [r for r in rows if 'r-cran-digest' not in r['case_id']]),
):
    print(name, 'cases:', len(selected),
          'repairs:', sum(r['repaired'] is True for r in selected))
    print(dict(Counter(r['termination_reason'] for r in selected)))
PYTHON
```
