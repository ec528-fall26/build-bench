"""Verify each valid proposal before ordinary review; never treat support as approval."""
import unittest
import test_output_verification as gate

SUPPORTED = 'OUTPUT VERIFICATION SUPPORTS THE CURRENT PENDING BATCH'


class VerificationOrderTests(unittest.TestCase):
    setUp = gate.OutputVerificationTests.setUp
    run_script = gate.OutputVerificationTests.run_script

    def test_supported_candidate_still_needs_explicit_approval(self):
        result = self.run_script([gate.edit('$(MAKE) -C docs html'), gate.check(), gate.STOP])
        self.assertEqual(self.rules.read_text(), self.original)
        self.assertFalse(result.attempts)
        self.assertIn(SUPPORTED, str(self.calls[2][0]))
        self.assertIn('Explicit approval is still required', str(self.calls[2][0]))
        self.assertNotIn('approved', [event['event'] for event in result.review_events])

    def test_current_supported_assessment_replaces_previous_rejection_before_review(self):
        result = self.run_script([
            gate.edit('dh_auto_build'), gate.check('contradicted'),
            gate.edit('$(MAKE) -C docs html'), gate.check(), gate.APPROVE, gate.STOP])
        review = str(self.calls[4][0])
        self.assertIn(SUPPORTED, review)
        self.assertNotIn('OUTPUT VERIFICATION WITHHELD', review)
        self.assertIn('+\\t$(MAKE) -C docs html', review)
        events = [event['event'] for event in result.review_events]
        self.assertEqual(events, ['proposed', 'output_verification_requested', 'output_verification',
            'revised', 'proposed', 'output_verification_requested', 'output_verification', 'approved'])
        self.assertEqual(sum(a.applied for a in result.attempts), 1)

    def test_revision_after_support_is_verified_again_before_review(self):
        result = self.run_script([
            gate.edit('$(MAKE) -C docs html'), gate.check(),
            gate.edit('dh_auto_build'), gate.check('contradicted'), gate.STOP])
        self.assertIn(SUPPORTED, str(self.calls[2][0]))
        self.assertNotIn(SUPPORTED, str(self.calls[3][0]))
        self.assertNotIn(SUPPORTED, str(self.calls[4][0]))
        self.assertEqual(self.rules.read_text(), self.original)
        self.assertFalse(result.attempts)

    def test_guards_run_before_verification(self):
        result = self.run_script([gate.edit(':'), gate.STOP])
        self.assertEqual(len(self.calls), 2)
        self.assertIn('rules_guard', [e['event'] for e in result.review_events])
        self.assertNotIn('output_verification_requested', [e['event'] for e in result.review_events])
        self.assertEqual(self.rules.read_text(), self.original)
