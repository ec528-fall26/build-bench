"""Fresh review isolates failed-attempt prose, not approval or safety state."""
import json
import os
import unittest
from unittest.mock import patch
import test_reviewed_repairs as reviewed
from src.contracts import AgentConfig, Budget, Attempt, ToolRequest, ToolResult
from src.loop import LoopRequest, run_loop
from src.model_adapter import ModelAdapter
from src.reply import parse_turn

class FreshReviewTests(unittest.TestCase):
    setUp = reviewed.ReviewedRepairs.setUp

    def adapter(self, mode='1', complete=lambda messages: ('', 'stop', {})):
        with patch.dict(os.environ, {'BB_EXPERIMENT_FRESH_REVIEW':mode,
                                     'BB_REPAIR_STRATEGY':'default'}):
            return ModelAdapter(complete, self.context,
                config=AgentConfig(use_build_feedback=False, use_no_build_control=False),
                budget=Budget(), diagnostics=[], log_path=None)

    def request(self):
        attempt = Attempt(1, 'FAILED_HYPOTHESIS_SENTINEL', [], 0, None, {})
        return LoopRequest(self.context, history=[attempt], latest_attempt=attempt,
            proposal_review='CURRENT_PENDING_PATCH_AND_REFERENCE',
            notes=['FAILED_REJECTION_SENTINEL'], feedback='FAILED_FEEDBACK_SENTINEL',
            edit_validation_feedback='FAILED_VALIDATION_SENTINEL')

    def test_excludes_failed_attempt_narrative_keeps_pending_review(self):
        adapter = self.adapter()
        text = str(adapter.messages(self.request()))
        self.assertNotIn('FAILED_', text)
        self.assertIn('CURRENT_PENDING_PATCH_AND_REFERENCE', text)
        self.assertIn('"action":"approve"', text)
        self.assertIn('Fresh pending-patch review', text)

    def test_flag_off_retains_original_history(self):
        text = str(self.adapter('0').messages(self.request()))
        self.assertIn('FAILED_HYPOTHESIS_SENTINEL', text)
        self.assertNotIn('Fresh pending-patch review', text)

    def test_nonreview_proposal_messages_unchanged(self):
        request = self.request();request.proposal_review=''
        a=self.adapter('0');b=self.adapter('1')
        a.clock=b.clock=lambda:0
        a.started=b.started=0
        self.assertEqual(a.messages(request),b.messages(request))

    def test_preserves_current_tools_parser_error_and_prior_source_changes(self):
        adapter = self.adapter()
        adapter._last_turn=parse_turn('{"action":"edit","edits":[]}', allow_review=True)
        request=self.request()
        request.tool_results=[ToolResult(ToolRequest('read_file',{'path':'input/a.c'}),
                                         'CURRENT_TOOL_SENTINEL',False)]
        request.controller_note='REPEAT_LIMIT_SENTINEL'
        request.own_edits='APPLIED_SOURCE_DIFF_SENTINEL'
        text=str(adapter.messages(request))
        for marker in ('CURRENT_TOOL_SENTINEL','REPEAT_LIMIT_SENTINEL',
                       'APPLIED_SOURCE_DIFF_SENTINEL','Review response rejected'):
            self.assertIn(marker,text)
        self.assertNotIn('FAILED_',text)

    def test_invalid_flag_rejected(self):
        with self.assertRaisesRegex(ValueError,'BB_EXPERIMENT_FRESH_REVIEW'):
            self.adapter('yes')

    def test_revision_still_requires_approval_and_is_recorded(self):
        def edit(value):
            return {'action':'edit','hypothesis':'replace type','evidence':['input/a.c:1'],
                    'edits':[{'path':'input/a.c','old_text':'oldtype','new_text':value,'replace_all':True}]}
        replies=iter([edit('first'),edit('second'),{'action':'approve','reason':'supported'},
                      {'action':'stop','reason':'done'}])
        calls=[]
        def complete(messages):
            calls.append(messages)
            if len(calls)<=3:
                self.assertEqual(self.a.read_text(),'oldtype oldtype\n')
            return json.dumps(next(replies)),'stop',{}
        adapter=self.adapter(complete=complete)
        result=run_loop(self.context,next_turn=adapter,config=adapter.config,review_edits=True)
        self.assertEqual(self.a.read_text(),'second second\n')
        self.assertIn('+second second',calls[2][1]['content'])
        self.assertEqual([t['fresh_review'] for t in adapter.turns],[False,True,True,False])
        self.assertEqual(len(result.attempts),1)
