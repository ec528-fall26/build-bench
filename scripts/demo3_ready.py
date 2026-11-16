#!/usr/bin/env python3
"""Record offline or explicitly network-enabled local Demo 3 readiness.

The tracked starter-kit runner is never modified. A disposable copy is used.
A local result, including a pass, is not a hosted/competition result.
"""
from __future__ import annotations
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

KIT = 'buildbench-starter-kit-0.1.0-rc.5'
AGENT = 'full-agent-v032'
MODEL = 'openai.gpt-oss-120b-1:0'
CONFIG = {
    'BB_AGENT_USE_EVIDENCE_MANAGER': '1', 'BB_AGENT_USE_TOOLS': '1',
    'BB_AGENT_USE_BUILD_FEEDBACK': '0', 'BB_AGENT_USE_HISTORY': '1',
    'BB_REPAIR_STRATEGY': 'default', 'BB_REVIEW_EDITS': '0',
}


def enable_local_model(text: str) -> str:
    """Opt in only in the temporary copy; fail if the expected runner changed."""
    old = '  --network none \\\n'
    if text.count(old) != 1:
        raise ValueError('Expected exactly one offline agent network option')
    # Docker inherits exported values by name: the key is not in argv or logs.
    options = ['  --network bridge \\', '  -e BB_MODEL_API_KEY \\',
               '  -e BB_MODEL_BASE_URL \\']
    options += [f'  -e {name} \\' for name in CONFIG]
    return text.replace(old, '\n'.join(options) + '\n')


def clean_environment(source, key, base, live):
    env = {k: v for k, v in source.items() if not k.startswith('BB_')}
    if live:
        env.update(CONFIG, BB_MODEL_API_KEY=key, BB_MODEL_BASE_URL=base)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out', type=Path, required=True, help='new evidence directory')
    parser.add_argument('--live-model', action='store_true', help='allow paid model calls over Docker bridge')
    parser.add_argument('--region', default='us-east-1', help='commercial AWS region; default us-east-1')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', args.region):
        parser.error('Invalid AWS region')
    repo = args.repo.resolve()
    kit = repo / KIT
    if not (kit / 'agents' / AGENT / 'agent.yaml').is_file():
        parser.error('Selected Demo 3 agent is missing')
    subprocess.run(['docker', 'info'], check=True, stdout=subprocess.DEVNULL)
    key = ''
    if args.live_model:
        key = (os.environ.get('BB_MODEL_API_KEY') or getpass.getpass('Model API key (hidden): ')).strip()
        if not key:
            parser.error('Empty API key')
    base = f'https://bedrock-runtime.{args.region}.amazonaws.com/openai/v1'
    env = clean_environment(os.environ, key, base, args.live_model)
    out = args.out.resolve()
    if out.is_relative_to(kit):
        parser.error('Keep evidence outside the starter-kit directory')
    out.mkdir(parents=True, exist_ok=False)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    # Only public configuration is persisted, never the key or full environment.
    metadata = {'started_utc': datetime.now(timezone.utc).isoformat(), 'commit': commit,
                'agent': AGENT, 'mode': 'local-network-enabled' if args.live_model else 'official-offline-runner',
                'network': 'bridge' if args.live_model else 'none',
                'model': MODEL if args.live_model else None,
                'base_url': base if args.live_model else None,
                'config': CONFIG if args.live_model else {},
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out / 'profile.json').write_text(json.dumps(metadata, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='demo3-ready-') as temporary:
        copy = Path(temporary) / KIT
        shutil.copytree(kit, copy, ignore=shutil.ignore_patterns('runs', 'dist', '__pycache__', '.git', '.venv', '*.pyc'))
        runner = copy / 'runner/run-agent-case.sh'
        original = runner.read_text()
        (out / 'run-agent-case.original.sh').write_text(original)
        if args.live_model:
            runner.write_text(enable_local_model(original))
        (out / 'run-agent-case.used.sh').write_text(runner.read_text())
        subprocess.run(['bash', '-n', str(runner)], check=True)
        print(f'Readiness mode: {metadata["mode"]}. Evidence: {out}', flush=True)
        print('Running bundled example validation; this may take time.', flush=True)
        with (out / 'ready-result.json').open('w') as stdout, (out / 'ready-progress.log').open('w') as stderr:
            completed = subprocess.run(['bash', './bb', 'ready', '--agent', f'./agents/{AGENT}',
                                        '--output', 'dist/demo3-readiness.zip', '--json'],
                                       cwd=copy, env=env, stdout=stdout, stderr=stderr)
        (out / 'exit-code.txt').write_text(f'{completed.returncode}\n')
        if (copy / 'runs').exists():
            shutil.copytree(copy / 'runs', out / 'runs')
        artifact = copy / 'dist/demo3-readiness.zip'
        try:
            result = json.loads((out / 'ready-result.json').read_text())
        except (ValueError, OSError):
            result = {}
        verified = False
        if completed.returncode == 0 and result.get('status') == 'succeeded' and artifact.is_file():
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            # Metadata layout is checked against the kit's actual JSON artifact list.
            verified = any(a.get('sha256') == digest for a in result.get('artifacts', []) if isinstance(a, dict))
            shutil.copy2(artifact, out / artifact.name)
            (out / 'SHA256SUMS').write_text(f'{digest}  {artifact.name}\n')
        print((out / 'ready-result.json').read_text(), flush=True)
        print(f'Process exit: {completed.returncode}; ZIP hash verified: {verified}', flush=True)
        print('This is local readiness evidence, not a hosted result or eleven-case repair score.')
        return 0 if verified else (completed.returncode or 1)


if __name__ == '__main__':
    raise SystemExit(main())
