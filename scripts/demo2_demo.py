#!/usr/bin/env python3
"""Reproducible Demo 2 entry point; uses the existing harness, never patches it."""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

RCRAN = 'launchpad-mantic-amd64-arm64-r-cran-digest-7a42effc961f'
MODES = ('controls', 'revise-replay', 'live-one-shot', 'live-full')

def plan(mode, bundle, out, python=sys.executable):
    jobs = {
        'controls': [('positive', 'one-shot', 'rcran-known-fix.json', True),
                     ('negative', 'one-shot', 'rcran-comment-only.json', False)],
        'revise-replay': [('revise', 'full-agent', 'rcran-full-revise.json', True)],
        'live-one-shot': [('live-one-shot', 'one-shot', None, None)],
        'live-full': [('live-full', 'full-agent', None, None)],
    }[mode]
    result = []
    for name, agent, replay, expected in jobs:
        cmd = [python, '-m', 'harness.run_case', '--bundle', str(bundle),
               '--agent', agent, '--run-id', name, '--runs-dir', str(out / 'runs')]
        cmd += ['--replay', 'harness/replays/' + replay] if replay else ['--live']
        result.append((name, cmd, expected))
    return result

def controlled_env(source, review=False):
    env = dict(source)
    for key in list(env):
        if key.startswith('BB_AGENT_') or key in {'BB_REPAIR_STRATEGY', 'BB_REVIEW_EDITS', 'BB_MODEL_TRACE'}:
            env.pop(key)
    for key in ('EVIDENCE_MANAGER', 'TOOLS', 'BUILD_FEEDBACK', 'HISTORY'):
        env['BB_AGENT_USE_' + key] = '1'
    env['BB_REPAIR_STRATEGY'] = 'shared-cause' if review else 'default'
    env['BB_REVIEW_EDITS'] = '1' if review else '0'
    return env

def verified(row):
    result = row.get('result') or {}
    return (row.get('repaired') is True and result.get('status') == 'succeeded'
            and result.get('artifact_validation_passed') is True)

def check_row(row, expected):
    if row.get('repaired') is True and not verified(row):
        return 'Inconsistent repair flag: final success and artifacts were not both verified.'
    if row.get('termination_reason') in {'baseline_error', 'unpack_failed', 'repack_failed', 'build_error', 'agent_error'}:
        return 'Execution error: ' + str(row.get('termination_reason'))
    if row.get('agent_termination') == 'model_error':
        return 'Model request failed; inspect agent_message in record.json.'
    if expected is True and not verified(row):
        return 'Expected a verified repair; inspect record.json and build.log.'
    if expected is False:
        if row.get('termination_reason') != 'build_failed' or verified(row):
            return 'Negative control did not reach the expected failed build.'
        if not row.get('edits_applied') or not row.get('result'):
            return 'Negative control did not apply an edit and run a build.'
    return None

def capture(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {'exit_code': p.returncode, 'output': p.stdout.strip()}

def source_settings(repo):
    src = repo / 'buildbench-starter-kit-0.1.0-rc.2/agents/full-agent/src'
    values = {}
    for filename in ('contracts.py', 'loop.py'):
        tree = ast.parse((src / filename).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == 'Budget':
                for field in node.body:
                    if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                        values[field.target.id] = ast.literal_eval(field.value)
            if isinstance(node, ast.FunctionDef) and node.name == 'run_loop':
                for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                    if arg.arg in ('max_turns', 'max_tool_calls'):
                        values[arg.arg] = ast.literal_eval(default)
    return values

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=MODES)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True, help='new directory; never overwritten')
    p.add_argument('--review', action='store_true', help='live-full only: shared-cause + proposed-diff review')
    p.add_argument('--dry-run', action='store_true', help='print plan, no models/builds/files written')
    args = p.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    bundle, out = args.bundle.expanduser().resolve(), args.out.expanduser().resolve()
    if args.review and args.mode != 'live-full':
        p.error('--review is supported only for live-full; scripted replies do not include approvals')
    if not (repo / 'harness/run_case.py').is_file():
        p.error('Install this file at scripts/demo2_demo.py in the complete repository.')
    if out.exists():
        p.error('Output exists. Use a fresh --out; earlier evidence is preserved.')
    if not (bundle / 'run.sh').is_file() or not (bundle / 'case/manifest.json').is_file():
        p.error('Bundle needs run.sh and case/manifest.json; use harness.case_views for development-v1.')
    manifest = json.loads((bundle / 'case/manifest.json').read_text())
    if args.mode in ('controls', 'revise-replay') and manifest['case_id'] != RCRAN:
        p.error('The replay fixtures are rcran-specific. Use live mode for other cases.')
    jobs = plan(args.mode, bundle, out)
    if args.dry_run:
        for _, cmd, expected in jobs:
            print(json.dumps({'command': cmd, 'expected_repaired': expected}))
        return 0
    if sys.version_info < (3, 10):
        p.error('Python 3.10+ required; Ubuntu 24.04 Python 3.12 recommended.')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        p.error('This walkthrough requires native Linux ARM64; run it on EC2, not macOS.')
    for command in ('docker', 'dpkg-source', 'git', 'bash'):
        if not shutil.which(command):
            p.error('Missing required command: ' + command)
    env = controlled_env(os.environ, args.review)
    if args.mode.startswith('live'):
        key = env.get('BB_MODEL_API_KEY', '')
        if not key or not key.isascii() or any(c.isspace() for c in key):
            p.error('BB_MODEL_API_KEY must be nonempty ASCII without whitespace. Its value is never printed.')
    if capture(['docker', 'info', '--format', '{{.Architecture}}'], repo)['exit_code']:
        p.error('Docker is not accessible. Check service and group membership.')
    out.mkdir(parents=True)
    versions = {name: capture(cmd, repo) for name, cmd in {
        'git': ['git', '--version'], 'docker': ['docker', 'version'],
        'dpkg_source': ['dpkg-source', '--version'],
        'commit': ['git', 'rev-parse', 'HEAD'],
        'tracked_changes': ['git', 'status', '--short'],
    }.items()}
    hashes = {}
    for directory in ('harness', 'buildbench-starter-kit-0.1.0-rc.2/agents'):
        for f in sorted((repo / directory).rglob('*.py')):
            if f.is_file() and not f.is_symlink() and not any(x.startswith('.') or x == '__pycache__' for x in f.relative_to(repo).parts):
                hashes[str(f.relative_to(repo))] = hashlib.sha256(f.read_bytes()).hexdigest()
    metadata = {'created_at': datetime.now(timezone.utc).isoformat(), 'mode': args.mode,
        'classification': 'scripted pipeline control' if not args.mode.startswith('live') else 'live model experiment',
        'case_id': manifest['case_id'], 'python': sys.version, 'platform': platform.platform(),
        'versions': versions, 'source_sha256': hashes, 'budget_defaults': source_settings(repo),
        'bundle_manifest_sha256': hashlib.sha256((bundle / 'case/manifest.json').read_bytes()).hexdigest(),
        'build_limits': manifest.get('build'),
        'switches': {k: v for k, v in env.items() if k.startswith('BB_AGENT_') or k in ('BB_REPAIR_STRATEGY', 'BB_REVIEW_EDITS')},
        'commands': [j[1] for j in jobs]}
    (out / 'settings.json').write_text(json.dumps(metadata, indent=2) + '\n')
    summaries = []
    for name, cmd, expected in jobs:
        print(f'Starting {name}; evidence: {out}', flush=True)
        with (out / (name + '.console.log')).open('w') as log:
            run = subprocess.run(cmd, cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT)
        record = out / 'runs' / name / 'record.json'
        if not record.is_file():
            print(f'ERROR: no record; inspect {out / (name + ".console.log")}', file=sys.stderr)
            return 2
        row = json.loads(record.read_text())
        problem = check_row(row, expected)
        if run.returncode and not problem:
            problem = f'Harness exit code {run.returncode}'
        if name == 'revise' and not problem:
            builds = [a.get('build') for a in row.get('attempts', []) if a.get('build')]
            if not any(b.get('succeeded') is False for b in builds) or not any(b.get('succeeded') is True for b in builds):
                problem = 'Replay did not demonstrate both a failed and a successful loop build.'
        summary = {k: row.get(k) for k in ('case_id', 'run_id', 'repaired', 'termination_reason', 'agent_termination', 'usage', 'wall_seconds')}
        summary['check_error'] = problem
        summaries.append(summary)
        (out / 'summary.json').write_text(json.dumps(summaries, indent=2) + '\n')
        print(json.dumps(summary, indent=2), flush=True)
        if problem:
            return 2
    print('Pipeline completed. Live not-repaired results are valid outcomes, not claimed successes.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
