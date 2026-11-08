"""Malformed revisions must be explained, retained safely, and correctable."""
import json
import os
import unittest
from unittest.mock import patch
import test_reviewed_repairs as reviewed
from src.contracts import AgentConfig, Budget
from src.loop import run_loop
from src.model_adapter import ModelAdapter
from src.proposal_review import REVIEW_INSTRUCTIONS, malformed_review_message
from src.reply import parse_turn

class ReviewRevisionTests(unittest.TestCase):
    setUp = reviewed.ReviewedRepairs.setUp

    def run_script(self, responses):
        calls = []
        iterator = iter(responses)
        def complete(messages):
            calls.append(messages)
            response = next(iterator)
            if callable(response):
                response = response(messages)
            return json.dumps(response), 'stop', {}
        with patch.dict(os.environ, {'BB_REPAIR_STRATEGY':'default'}):
            adapter = ModelAdapter(complete, self.context,
                config=AgentConfig(use_build_feedback=False, use_no_build_control=False),
                budget=Budget(), diagnostics=[], log_path=None)
            result = run_loop(self.context, next_turn=adapter,
                              config=adapter.config, review_edits=True)
        return result, calls

    def edit_response(self, replacement, hypothesis=True):
        value = {'action':'edit', 'edits':[{'path':'input/a.c','old_text':'oldtype',
                  'new_text':replacement,'replace_all':True}]}
        if hypothesis:
            value.update(hypothesis='replace the old type', evidence=['input/a.c:1'])
        return value

    def test_malformed_then_corrected_revision_then_explicit_approval(self):
        def correct(messages):
            text = messages[1]['content']
            self.assertIn('Review response rejected', text)
            self.assertIn('must state a "hypothesis"', text)
            self.assertIn('did not replace the previous pending batch', text)
            self.assertIn('+first_candidate first_candidate', text)
            self.assertEqual(self.a.read_text(), 'oldtype oldtype\n')
            return self.edit_response('corrected_candidate')
        def approve(messages):
            text = messages[1]['content']
            self.assertIn('+corrected_candidate corrected_candidate', text)
            self.assertNotIn('Review response rejected', text)
            self.assertEqual(self.a.read_text(), 'oldtype oldtype\n')
            return {'action':'approve','reason':'reviewed the corrected candidate'}
        result, _ = self.run_script([
            self.edit_response('first_candidate'), self.edit_response('corrected_candidate', False),
            correct, approve, {'action':'stop','reason':'done'}])
        self.assertEqual(self.a.read_text(), 'corrected_candidate corrected_candidate\n')
        self.assertEqual(result.malformed_count, 1)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual([e['event'] for e in result.review_events],
                         ['proposed','revised','proposed','approved'])

    def test_stop_after_rejected_revision_does_not_apply_either_patch(self):
        result, calls = self.run_script([
            self.edit_response('first_candidate'), self.edit_response('rejected_candidate', False),
            {'action':'stop','reason':'unsupported'}])
        self.assertIn('Review response rejected', calls[-1][1]['content'])
        self.assertEqual(self.a.read_text(), 'oldtype oldtype\n')
        self.assertEqual(len(result.attempts), 0)

    def test_review_examples_are_parseable_and_include_required_fields(self):
        examples = [json.loads(line) for line in REVIEW_INSTRUCTIONS.splitlines()
                    if line.startswith('{')]
        self.assertEqual({e['action'] for e in examples}, {'approve','edit','stop'})
        for example in examples:
            with self.subTest(action=example['action']):
                self.assertEqual(parse_turn(json.dumps(example), allow_review=True).action,
                                 example['action'])
        edit = next(e for e in examples if e['action']=='edit')
        self.assertTrue(edit['hypothesis'])
        self.assertIn('evidence', edit)

    def test_reason_is_not_silently_used_as_hypothesis(self):
        value = self.edit_response('revision', False)
        value['reason'] = 'looks correct'
        parsed = parse_turn(json.dumps(value), allow_review=True)
        self.assertEqual(parsed.action, 'malformed')
        self.assertIn(parsed.reason, malformed_review_message(parsed.reason))
