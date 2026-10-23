#!/usr/bin/env python3
"""Report archived outcomes only: no model call and no build."""
from collections import defaultdict
import json
from pathlib import Path


def main():
    source = Path(__file__).resolve().parents[1] / 'docs/evidence/demo2/comparison-records.json'
    data = json.loads(source.read_text())
    latest = json.loads((source.parent / 'one-shot-retest-summary.json').read_text())
    print('Latest completed one-shot run:', latest['experiment_id'])
    print('Provenance: archive summaries verified against original records.')
    for group in ('rcran', 'development_bundle'):
        item = latest[group]
        print(group, ':', item['cases_recorded'], 'cases;', item['verified_repairs'], 'repairs;', item['termination_reasons'])
    paired = json.loads((source.parent / 'paired-retest/audit.json').read_text())
    print('\nLatest paired retest (one repeat; raw verdicts retained):')
    for agent in ('one-shot', 'full-agent'):
        rows = [r for r in paired['runs'] if r['agent'] == agent]
        print(agent, sum(r['repaired'] is True for r in rows), '/', len(rows), 'repairs;',
              sum(r['classification'] == 'environment_blocked' for r in rows), 'setup-blocked;',
              sum(r['classification'] == 'already_building' for r in rows), 'already building')
    print('Source archive SHA256:', paired['source_archive_sha256'])
    print('\nOlder four-case archive (separate experiment):')
    groups = defaultdict(list)
    for row in data['records']:
        groups[row['configuration']].append(row)
    for config, rows in sorted(groups.items()):
        repaired = sum(row['repaired'] is True for row in rows)
        cases = {row['case_id'] for row in rows}
        print(f'{config}: {repaired}/{len(rows)} repaired across {len(cases)} cases')
        for case in sorted(cases):
            selected = [r for r in rows if r['case_id'] == case]
            print(f'  {case}: {sum(r["repaired"] is True for r in selected)}/{len(selected)}')
    print('Full-agent configuration label comes from archive folder; switches absent in original rows.')
    print('Source archive SHA256:', data['source_archive_sha256'])

if __name__ == '__main__':
    main()
