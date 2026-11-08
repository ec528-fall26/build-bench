"""Budgeted output gate: no write until explicit review and static verification agree."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

AGENT = Path(__file__).resolve().parents[2] / 'buildbench-starter-kit-0.1.0-rc.5/agents/full-agent'
sys.path.insert(0, str(AGENT))
from src.contracts import AgentConfig, Budget
from src.types import CaseContext
from src.loop import run_loop
from src.model_adapter import ModelAdapter
from src import output_verification


def edit(command):
    return {'action': 'edit', 'hypothesis': 'preserve documentation output',
            'evidence': ['input/debian/docs.install:1'],
            'edits': [{'path': 'input/debian/rules', 'old_text': '\tpython3 setup.py build_sphinx',
                       'new_text': '\t' + command}]}


def check(verdict='supported'):
    return {'verdict': verdict, 'outputs': [{'path': 'docs/_build/html/',
            'required_by': 'input/debian/docs.install:1', 'replacement_command': '$(MAKE) -C docs html',
            'working_directory': 'docs', 'producer_evidence': ['input/docs/Makefile:2'],
            'status': verdict}], 'unsupported_assumptions': [], 'reason': 'Source producer chain checked'}

APPROVE = {'action': 'approve', 'reason': 'reviewed'}
STOP = {'action': 'stop', 'reason': 'done'}


class OutputVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'input/debian').mkdir(parents=True)
        (self.root / 'input/docs').mkdir()
        self.rules = self.root / 'input/debian/rules'
        self.original = 'override_dh_auto_build-indep:\n\tpython3 setup.py build_sphinx\n'
        self.rules.write_text(self.original)
        (self.root / 'input/debian/docs.install').write_text('docs/_build/html/ usr/share/doc/example/\n')
        (self.root / 'input/docs/Makefile').write_text('html:\n\tsphinx-build -b html . _build/html\n')
        self.context = CaseContext('fixture', self.root, '', {})
        self.calls = []

    def run_script(self, replies, enabled='1', **kwargs):
        iterator = iter(replies)
        config = AgentConfig(use_build_feedback=False, use_no_build_control=False)
        def complete(messages):
            self.calls.append((messages, self.rules.read_text()))
            reply = next(iterator)
            if isinstance(reply, Exception):
                raise reply
            return (reply if isinstance(reply, str) else json.dumps(reply)), 'stop', {'input_tokens': 2, 'output_tokens': 1}
        with patch.dict(os.environ, {'BB_EXPERIMENT_OUTPUT_VERIFICATION': enabled,
                'BB_EXPERIMENT_FRESH_REVIEW': '1', 'BB_EXPERIMENT_PACKAGING_CONTEXT': '1',
                'BB_REPAIR_STRATEGY': 'default', 'BB_FFI_ANALYSIS': '0'}):
            adapter = ModelAdapter(complete, self.context, config=config, budget=kwargs.get('budget', Budget()),
                                   diagnostics=[], log_path=None)
            self.addCleanup(adapter.close)
            result = run_loop(self.context, next_turn=adapter, config=config,
                              review_edits=True, **kwargs)
        self.adapter = adapter
        return result

    def test_default_off_requires_no_additional_call(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(output_verification.enabled())
        result = self.run_script([edit('dh_auto_build'), APPROVE, STOP], enabled='0')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(sum(a.applied for a in result.attempts), 1)

    def test_explicit_approval_does_not_write_until_supported_verification(self):
        result = self.run_script([edit('$(MAKE) -C docs html'), check(), APPROVE, STOP])
        self.assertEqual([text for _, text in self.calls[:3]], [self.original] * 3)
        self.assertIn('$(MAKE) -C docs html', self.rules.read_text())
        self.assertEqual(sum(a.applied for a in result.attempts), 1)
        self.assertEqual(result.usage, {'input_tokens': 8, 'output_tokens': 4})
        prompt = str(self.calls[1][0])
        self.assertIn('input/debian/docs.install:1', prompt)
        self.assertIn('input/docs/Makefile:2', prompt)
        self.assertNotIn('Return {"action":"approve"', prompt)

    def test_rejected_batch_revised_then_reviewed_and_verified_again(self):
        result = self.run_script([edit('dh_auto_build'), check('contradicted'),
                                  edit('$(MAKE) -C docs html'), check(), APPROVE, STOP])
        self.assertTrue(all(text == self.original for _, text in self.calls[:5]))
        self.assertIn('OUTPUT VERIFICATION WITHHELD', str(self.calls[2][0]))
        self.assertIn('$(MAKE) -C docs html', str(self.calls[3][0]))
        events = [e for e in result.review_events if e['event'] == 'output_verification']
        self.assertEqual([e['assessment']['verdict'] for e in events], ['contradicted', 'supported'])
        self.assertNotEqual(events[0]['signature'], events[1]['signature'])

    def test_insufficient_malformed_and_approval_cannot_bypass_gate(self):
        result = self.run_script([edit('dh_auto_build'), check('insufficient_evidence'),
                                  APPROVE, 'not JSON', APPROVE, APPROVE])
        self.assertEqual(result.termination_reason, 'output_verification_format_failed')
        self.assertEqual(self.rules.read_text(), self.original)
        self.assertEqual(len(self.calls), 5)
        self.assertFalse(result.attempts)

    def test_turn_limit_before_verification_leaves_source_untouched(self):
        result = self.run_script([edit('dh_auto_build'), check()], max_turns=2)
        self.assertEqual(result.termination_reason, 'turn_budget')
        self.assertEqual(self.rules.read_text(), self.original)

    def test_source_change_invalidates_approval_and_requires_new_verification(self):
        # Simulate a source change between review and verification. Existing
        # snapshot signatures must force a new review before any patch is written.
        original_messages = output_verification.messages
        changed = False
        def messages(*args):
            nonlocal changed
            if not changed:
                self.rules.write_text(self.original + '# concurrent source change\n')
                changed = True
            return original_messages(*args)
        with patch.object(output_verification, 'messages', messages):
            result = self.run_script([edit('$(MAKE) -C docs html'), check(), APPROVE,
                                      check(), APPROVE, STOP])
        checks = [e for e in result.review_events if e['event'] == 'output_verification']
        self.assertEqual(len(checks), 2)
        self.assertNotEqual(checks[0]['signature'], checks[1]['signature'])
        self.assertIn('$(MAKE) -C docs html', self.rules.read_text())

    def test_token_limit_before_verification_stops_extra_call(self):
        result = self.run_script([edit('dh_auto_build')], budget=Budget(max_tokens=3))
        self.assertEqual(result.termination_reason, 'token_budget')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.rules.read_text(), self.original)

    def test_verifier_usage_reaching_limit_does_not_apply(self):
        result = self.run_script([edit('dh_auto_build'), check()], budget=Budget(max_tokens=6))
        self.assertEqual(result.termination_reason, 'token_budget')
        self.assertEqual(self.rules.read_text(), self.original)

    def test_verifier_api_failure_never_applies(self):
        result = self.run_script([edit('dh_auto_build'), RuntimeError('offline failure')])
        self.assertEqual(result.termination_reason, 'model_error')
        self.assertEqual(self.rules.read_text(), self.original)

    def test_truncated_and_inconsistent_assessments_fail_closed(self):
        for mutate in ('empty', 'conflict', 'assumption', 'citation'):
            value = check()
            if mutate == 'empty': value['outputs'] = []
            if mutate == 'conflict': value['outputs'][0]['status'] = 'contradicted'
            if mutate == 'assumption': value['unsupported_assumptions'] = ['No generator evidence']
            if mutate == 'citation': value['outputs'][0]['producer_evidence'] = []
            with self.subTest(mutate=mutate):
                self.assertEqual(output_verification.assessment(json.dumps(value), 'stop')['verdict'], 'invalid_response')
        self.assertEqual(output_verification.assessment(json.dumps(check()), 'length')['verdict'], 'invalid_response')

    def test_flag_requires_review_and_valid_value(self):
        with patch.dict(os.environ, {'BB_EXPERIMENT_OUTPUT_VERIFICATION': 'yes'}):
            with self.assertRaises(ValueError): output_verification.enabled()
        with patch.dict(os.environ, {'BB_EXPERIMENT_OUTPUT_VERIFICATION': '1', 'BB_EXPERIMENT_PACKAGING_CONTEXT': '1'}):
            with self.assertRaisesRegex(ValueError, 'BB_REVIEW_EDITS'):
                run_loop(self.context, next_turn=lambda request: None, review_edits=False)
