"""Frozen pre-refactor outputs for the optional source collectors."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
    'buildbench-starter-kit-0.1.0-rc.5/agents/full-agent'))
from src.contracts import Diagnostic
from src.source_context import enrich_diagnostics


class SourceContextCharacterization(unittest.TestCase):
    def test_preserves_output_across_switches_and_limits(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' /
                              'source_context_characterization.json').read_text())
        cases = {case['name']: case['files'] for case in fixture['cases']}
        for sample in fixture['results']:
            with self.subTest(case=sample['case'], cmake=sample['cmake'],
                              packaging=sample['packaging']):
                with tempfile.TemporaryDirectory() as directory:
                    worktree = Path(directory)
                    for name, text in cases[sample['case']].items():
                        path = worktree / 'input' / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(text)
                    diagnostics = [
                        Diagnostic(message, 'unknown', None, None, [], None,
                                   number, 'existing context' if number == 0 else '')
                        for number, message in enumerate(sample['messages'])
                    ]
                    env = {
                        'BB_EXPERIMENT_CMAKE_CONTEXT': sample['cmake'],
                        'BB_EXPERIMENT_PACKAGING_CONTEXT': sample['packaging'],
                    }
                    with patch.dict(os.environ, env, clear=True):
                        enrich_diagnostics(diagnostics, worktree)
                    self.assertEqual([asdict(item) for item in diagnostics],
                                     sample['expected'])
