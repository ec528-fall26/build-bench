"""The actual review messages must not carry the ordinary edit-only menu."""
import json
import os
import unittest
from unittest.mock import patch
import test_reviewed_repairs as reviewed
from src.contracts import AgentConfig, Budget
from src.loop import LoopRequest, run_loop
from src.model_adapter import ModelAdapter

class ReviewMenuTests(unittest.TestCase):
    setUp = reviewed.ReviewedRepairs.setUp

    def adapter(self, complete=lambda messages: ('', 'stop', {})):
        with patch.dict(os.environ, {'BB_REPAIR_STRATEGY': 'default'}):
            return ModelAdapter(complete, self.context,
                config=AgentConfig(use_build_feedback=False, use_no_build_control=False),
                budget=Budget(), diagnostics=[], log_path=None)

    def test_pending_review_replaces_normal_reply_menu(self):
        messages = self.adapter().messages(LoopRequest(self.context, proposal_review='SIMULATED diff'))
        self.assertNotIn('object, which must be one of:', messages[0]['content'])
        self.assertIn('"action":"approve"', messages[0]['content'])
        self.assertIn('PENDING REVIEW', messages[1]['content'])
        self.assertIn('not yet applied', messages[1]['content'])

    def test_normal_turn_keeps_normal_menu(self):
        messages = self.adapter().messages(LoopRequest(self.context))
        self.assertIn('object, which must be one of:', messages[0]['content'])
        self.assertNotIn('PENDING REVIEW', messages[1]['content'])

    def test_review_with_exhausted_tools_does_not_demand_another_edit(self):
        adapter = self.adapter()
        adapter._investigating = 100
        adapter._tool_turns = 100
        text = adapter.messages(LoopRequest(self.context, proposal_review='diff'))[1]['content']
        self.assertIn('Tools are unavailable', text)
        self.assertNotIn('Propose your best fix now', text)
        self.assertIn('do not resubmit it unchanged', text)

    def test_completion_receives_review_then_explicit_approval_applies(self):
        calls = []
        responses = iter([
            {'action':'edit', 'hypothesis':'fix', 'edits':[
                {'path':'input/a.c','old_text':'oldtype','new_text':'newtype','replace_all':True}]},
            {'action':'approve','reason':'source supports this replacement'},
            {'action':'stop','reason':'done'},
        ])
        def complete(messages):
            calls.append(messages)
            return json.dumps(next(responses)), 'stop', {}
        adapter = self.adapter(complete)
        result = run_loop(self.context, next_turn=adapter, config=adapter.config,
                          review_edits=True)
        self.assertIn('PENDING REVIEW', calls[1][1]['content'])
        self.assertNotIn('object, which must be one of:', calls[1][0]['content'])
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertEqual(self.a.read_text(), 'newtype newtype\n')
