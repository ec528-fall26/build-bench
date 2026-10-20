#!/usr/bin/env python3
"""Prepare an isolated checkout matching recorded agent source and full-agent limits."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

COMMIT = '001f3b9dd15f89665794609c8b04b73acfae167e'

def verify_sources(repo, expected):
    failures = []
    for name, digest in expected.items():
        path = repo / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            failures.append(name)
    if failures:
        raise ValueError('Source does not match recorded experiment: ' + ', '.join(failures))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True, help='new isolated checkout; never overwritten')
    args = p.parse_args()
    source = Path(__file__).resolve().parents[1]
    out = args.out.expanduser().resolve()
    if out.exists():
        p.error('Destination exists; choose a new path.')
    evidence = source / 'docs/evidence/demo2/paired-retest/full-agent'
    plan = json.loads((evidence / 'plan.json').read_text())
    if plan['commit'] != COMMIT:
        p.error('Unexpected recorded commit.')
    subprocess.run(['git', 'cat-file', '-e', COMMIT + '^{commit}'], cwd=source, check=True)
    # An independent local clone preserves all history and does not modify the handoff checkout.
    subprocess.run(['git', 'clone', '--no-hardlinks', str(source), str(out)], check=True)
    subprocess.run(['git', 'checkout', '--detach', COMMIT], cwd=out, check=True)
    patch = evidence / 'local-settings.diff'
    subprocess.run(['git', 'apply', '--check', str(patch)], cwd=out, check=True)
    subprocess.run(['git', 'apply', str(patch)], cwd=out, check=True)
    verify_sources(out, plan['source_sha256'])
    # Copy only the TA wrappers and their evidence; never overwrite historical agent/harness code.
    for name in ('demo2_demo.py', 'demo2_retest.py', 'demo2_evidence_report.py'):
        (out / 'scripts').mkdir(exist_ok=True)
        shutil.copy2(source / 'scripts' / name, out / 'scripts' / name)
    shutil.copytree(source / 'docs/evidence/demo2', out / 'docs/evidence/demo2', dirs_exist_ok=True)
    tag = subprocess.run(['git', 'diff', '--exit-code', 'one-shot-v1', '--',
        'buildbench-starter-kit-0.1.0-rc.2/agents/one-shot'], cwd=out)
    if tag.returncode:
        raise SystemExit('Frozen baseline differs; do not run this checkout.')
    (out / 'ta-recorded-profile.json').write_text(json.dumps({
        'historical_commit': COMMIT,
        'handoff_commit': subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip(),
        'limits': plan['limits'], 'strategy': 'default', 'history': True,
        'proposal_review': False, 'reference_repair_supplied': False,
        'source_hashes_verified': True,
    }, indent=2) + '\n')
    print('Prepared:', out)
    print('Recorded source hashes verified; raised limits applied only in the isolated checkout.')
    print('No model calls or builds started. Host/emulator behavior and model outcomes can differ.')

if __name__ == '__main__':
    main()
