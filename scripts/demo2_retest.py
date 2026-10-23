#!/usr/bin/env python3
"""Sequential live retest: rcran plus the frozen ten cases, both agents, no supplied fixes."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from demo2_demo import RCRAN, capture, source_settings


def make_jobs(cases, repeats):
    # Calibration first, then each development case with both agents.
    return [{'case_id': case, 'agent': agent, 'repeat': repeat,
             'job_id': f'{case}--{agent}--r{repeat}'}
            for repeat in range(1, repeats + 1)
            for case in cases for agent in ('one-shot', 'full-agent')]


def completed(folder):
    # A driver summary is written only after its subprocess has finished.
    # A missing summary is interrupted/incomplete, never a completed failure.
    return (folder / 'summary.json').is_file()


def next_attempt(root):
    root.mkdir(parents=True, exist_ok=True)
    index = 1
    while (root / f'attempt-{index:02d}').exists():
        index += 1
    return root / f'attempt-{index:02d}'


def summarize(out, jobs):
    rows = []
    for job in jobs:
        folder = out / 'jobs' / job['job_id']
        finished = sorted(p for p in folder.glob('attempt-*') if completed(p))
        row = dict(job, status='not_completed')
        if finished:
            attempt = finished[-1]
            entries = json.loads((attempt / 'summary.json').read_text())
            row.update(entries[-1], status='completed', evidence=str(attempt.relative_to(out)))
        rows.append(row)
    (out / 'coverage.json').write_text(json.dumps(rows, indent=2) + '\n')
    lines = ['# Live retest results', '',
             'Every scheduled run is retained; not-completed is not a repair failure.', '',
             '| Case | Agent | Repeat | Status | Repaired | Final reason | Agent stop |',
             '| --- | --- | ---: | --- | --- | --- | --- |']
    for row in rows:
        lines.append('| ' + ' | '.join(str(row.get(k, '—')) for k in
                     ('case_id', 'agent', 'repeat', 'status', 'repaired', 'termination_reason', 'agent_termination')) + ' |')
    (out / 'results.md').write_text('\n'.join(lines) + '\n')
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rcran', type=Path, required=True)
    p.add_argument('--views', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--repeats', type=int, default=1)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(argv)
    if args.repeats < 1:
        p.error('--repeats must be positive')
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((repo / 'docs/evidence/demo2/development-cases.json').read_text())
    cases = [RCRAN] + [item['case_id'] for item in manifest['cases']]
    if len(set(cases)) != 11:
        p.error('Expected rcran plus exactly ten distinct development cases.')
    bundles = {case: args.views.expanduser().resolve() / case for case in cases}
    bundles[RCRAN] = args.rcran.expanduser().resolve()
    expected = {item['case_id']: item['manifest_sha256'] for item in manifest['cases']}
    hashes = {}
    for case, bundle in bundles.items():
        path = bundle / 'case/manifest.json'
        if not path.is_file() or not (bundle / 'run.sh').is_file():
            p.error('Missing case view or rcran bundle: ' + str(bundle))
        data = path.read_bytes()
        if json.loads(data)['case_id'] != case:
            p.error('Wrong case ID in ' + str(path))
        digest = hashlib.sha256(data).hexdigest()
        if case in expected and expected[case] != digest:
            p.error('Case manifest differs from frozen release: ' + str(path))
        hashes[case] = digest
    jobs = make_jobs(cases, args.repeats)
    if args.dry_run:
        print(f'{len(jobs)} live runs: 11 cases × 2 agents × {args.repeats} repeat(s). No calls/builds started.')
        for job in jobs:
            print(job['job_id'])
        return 0
    key = os.environ.get('BB_MODEL_API_KEY', '')
    if not key or not key.isascii() or any(c.isspace() for c in key):
        p.error('Set a valid-format BB_MODEL_API_KEY inside this session; value is never printed.')
    handler = Path('/proc/sys/fs/binfmt_misc/qemu-x86_64')
    if not handler.is_file():
        p.error('Reverse-direction cases require enabled qemu-x86_64 binfmt with F flag.')
    text = handler.read_text()
    if not text.startswith('enabled') or not any(line.startswith('flags:') and 'F' in line for line in text.splitlines()):
        p.error('qemu-x86_64 handler must be enabled with F flag; fix before launching.')
    baseline_path = 'buildbench-starter-kit-0.1.0-rc.2/agents/one-shot'
    check = capture(['git', 'diff', '--exit-code', 'one-shot-v1', '--', baseline_path], repo)
    if check['exit_code']:
        p.error('Frozen baseline verification failed: fetch tag one-shot-v1 and inspect changes; do not relabel modified baseline.')
    fingerprints = {}
    for base in ('harness', 'scripts', 'buildbench-starter-kit-0.1.0-rc.2/agents'):
        for f in sorted((repo / base).rglob('*.py')):
            if not f.is_symlink() and '__pycache__' not in f.parts:
                fingerprints[str(f.relative_to(repo))] = hashlib.sha256(f.read_bytes()).hexdigest()
    identity = {'commit': capture(['git', 'rev-parse', 'HEAD'], repo),
                'python': sys.version, 'sources': fingerprints,
                'case_manifests': hashes, 'jobs': jobs,
                'full_agent_defaults': source_settings(repo),
                'configuration': 'default strategy, history on, review off',
                'endpoint': os.environ.get('BB_MODEL_BASE_URL', 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1')}
    # Endpoint must not embed credentials; key is supplied separately.
    from urllib.parse import urlsplit
    endpoint = urlsplit(identity['endpoint'])
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        p.error('Model base URL must not contain embedded credentials, query, or fragment.')
    out = args.out.expanduser().resolve()
    if out.exists():
        if not args.resume:
            p.error('Output exists; use --resume with unchanged source/settings, or a fresh output.')
        saved = json.loads((out / 'plan.json').read_text())
        if saved['identity'] != identity:
            p.error('Code, Python, endpoint, cases or schedule changed; start a new output directory.')
    else:
        out.mkdir(parents=True)
        (out / 'plan.json').write_text(json.dumps({'created_at': datetime.now(timezone.utc).isoformat(), 'identity': identity}, indent=2) + '\n')
    import fcntl
    lock = (out / '.schedule.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        p.error('Another process is running this schedule; attach to its tmux session.')
    summarize(out, jobs)
    for number, job in enumerate(jobs, 1):
        base = out / 'jobs' / job['job_id']
        if any(completed(f) for f in base.glob('attempt-*')):
            print(f'[{number}/{len(jobs)}] recorded already: {job["job_id"]}', flush=True)
            continue
        target = next_attempt(base)
        mode = 'live-one-shot' if job['agent'] == 'one-shot' else 'live-full'
        command = [sys.executable, str(repo / 'scripts/demo2_demo.py'), mode,
                   '--bundle', str(bundles[job['case_id']]), '--out', str(target)]
        print(f'[{number}/{len(jobs)}] {job["job_id"]}', flush=True)
        proc = subprocess.run(command, cwd=repo)
        summarize(out, jobs)
        if proc.returncode:
            print('Execution/control error: paused schedule. Inspect evidence, then --resume. Completed failures are retained.', file=sys.stderr)
            return 2
    print('Schedule complete. Read results.md and coverage.json; already-building cases are not repairs.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
