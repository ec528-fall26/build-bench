#!/usr/bin/env python3
"""Verify the archived Demo 1 evidence. Does not run a new build."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
evidence = root / 'docs/evidence/demo-1'

def read(name):
    return json.loads((evidence / name).read_text())

def require(condition, message):
    if not condition:
        raise SystemExit('FAIL: ' + message)

initial, agent, final = (read(n) for n in (
    'initial-build-result.json', 'agent-result.json', 'build-result.json'))
require(initial['status'] == 'failed', 'initial build must reproduce the failure')
require(agent['status'] == 'completed', 'example agent must complete')
require(final['status'] == 'succeeded', 'final build must succeed')
require(final['build_exit_code'] == 0, 'final build exit code')
require(final['patch_applied'] is True, 'canonical patch application')
require(final['artifact_validation_passed'] is True, 'artifact validation')
require(final['timed_out'] is False, 'final build timeout')
require(final['case_id'] == initial['case_id'] == 'hello-demo', 'case identity')
require(len(final['artifacts']) == 2, 'expected artifact count')
for artifact in final['artifacts']:
    target = (evidence / artifact['path']).resolve()
    require(target.is_relative_to(evidence.resolve()), 'artifact path outside evidence directory')
    payload = target.read_bytes()
    require(len(payload) == artifact['size_bytes'], f"size: {artifact['path']}")
    require(hashlib.sha256(payload).hexdigest() == artifact['sha256'], f"hash: {artifact['path']}")
summary = read('dataset-summary.json')['summary']
require(summary['case_count'] == 200 and summary['unique_packages'] == 188, 'dataset counts')
require(summary['directions'] == {'aarch64-to-x86_64': 100, 'x86_64-to-aarch64': 100}, 'direction counts')
require(sum(summary['ubuntu_series'].values()) == 200, 'release counts')
require(summary['checksum_entries'] == 1510 and not summary['checksum_failures'], 'dataset checksums')
require(summary['source_checksum_entries'] == 705 and not summary['source_checksum_failures'], 'source checksums')
for record in (evidence / 'SHA256SUMS').read_text().splitlines():
    expected, relative = record.split('  ', 1)
    target = (evidence / relative).resolve()
    require(target.is_relative_to(evidence.resolve()), 'checksum path outside evidence directory')
    require(hashlib.sha256(target.read_bytes()).hexdigest() == expected, f'archived file: {relative}')
print(f"PASS: initial failure, successful clean repair, 2 artifact hashes, {final['duration_seconds']} s final build.")
print('PASS: archived dataset summary has 200 cases / 188 packages / 100 cases per direction.')
print('Scope: saved evidence only; no new build or general LLM-agent evaluation.')
