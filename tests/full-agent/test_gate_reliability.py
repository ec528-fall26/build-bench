import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_output_verification as gate
from src.output_verification import producer_context
from src.request_trace import trace_completion

class ReliabilityTests(unittest.TestCase):
    setUp = gate.OutputVerificationTests.setUp
    run_script = gate.OutputVerificationTests.run_script

    def test_format_retry_keeps_candidate_and_requires_approval(self):
        result = self.run_script([gate.edit('$(MAKE) -C docs html'), 'bad JSON',
                                  gate.check(), gate.APPROVE, gate.STOP])
        self.assertIn('SAME pending batch', str(self.calls[2][0]))
        self.assertEqual(self.calls[1][0][:2], self.calls[2][0][:2])
        self.assertTrue(all(s == self.original for _,s in self.calls[:4]))
        self.assertEqual(sum(a.applied for a in result.attempts), 1)
        self.assertEqual(result.usage, {'input_tokens':10,'output_tokens':5})

    def test_two_format_failures_stop_without_applying(self):
        result = self.run_script([gate.edit('dh_auto_build'), 'bad', 'bad'])
        self.assertEqual(result.termination_reason,'output_verification_format_failed')
        self.assertEqual(self.rules.read_text(),self.original)

    def test_retry_respects_turn_budget(self):
        result = self.run_script([gate.edit('dh_auto_build'), 'bad'],max_turns=2)
        self.assertEqual(result.termination_reason,'turn_budget')
        self.assertEqual(self.rules.read_text(),self.original)

    def test_no_manifest_still_supplies_setup_and_rules(self):
        (self.root/'input/debian/docs.install').unlink()
        (self.root/'input/setup.py').write_text('srcs = ["pyBigWig.c"]\n')
        self.run_script([gate.edit('dh_auto_build'),gate.check('insufficient_evidence'),gate.STOP])
        prompt=str(self.calls[1][0])
        self.assertIn('input/setup.py:1:',prompt)
        self.assertIn('input/debian/rules:1:',prompt)
        self.assertIn('does not prove outputs are absent',prompt)

    def test_context_does_not_follow_symlinks_and_marks_truncation(self):
        outside=self.root/'private.txt'; outside.write_text('DO_NOT_READ')
        (self.root/'input/setup.py').symlink_to(outside)
        (self.root/'input/Makefile').write_text('x'*10000)
        context=producer_context(self.root,[])
        self.assertNotIn('DO_NOT_READ',context)
        self.assertIn('unavailable',context)
        self.assertIn('truncated',context)
        self.assertLess(len(context),14000)

    def test_trace_pairs_exact_responses_with_requests(self):
        path=self.root/'trace.jsonl'
        response=('raw malformed JSON', 'stop', {'input_tokens':3})
        with patch.dict(os.environ,{'BB_MODEL_TRACE':str(path)}):
            call=trace_completion(lambda messages:response)
            call([{'role':'user','content':'pending patch A'}])
            call([{'role':'user','content':'pending patch B'}])
        records=[json.loads(x) for x in Path(str(path)+'.responses.jsonl').read_text().splitlines()]
        self.assertEqual([r['call'] for r in records],[1,2])
        self.assertEqual(records[0]['response'],list(response))
        self.assertEqual(Path(str(path)+'.responses.jsonl').stat().st_mode & 0o777,0o600)
