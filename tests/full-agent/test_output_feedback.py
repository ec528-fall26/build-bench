"""Keep unresolved output assessments across every repair/review response path."""
import json
import unittest
from unittest.mock import patch
import test_output_verification as gate

MARKER = 'OUTPUT VERIFICATION WITHHELD APPLICATION'


class OutputFeedbackTests(unittest.TestCase):
    setUp = gate.OutputVerificationTests.setUp
    run_script = gate.OutputVerificationTests.run_script

    def test_retained_after_repeat_malformed_and_tool_response(self):
        tool = {'action': 'tool', 'requests': [{'tool': 'read_file', 'args': {
            'path': 'input/debian/docs.install', 'start_line': 1, 'end_line': 10}}]}
        result = self.run_script([
            gate.edit('dh_auto_build'), gate.check('contradicted'),
            gate.edit('dh_auto_build'), {'action': 'read_file'}, tool, gate.STOP])
        self.assertEqual(result.termination_reason, 'model_stop')
        self.assertEqual(self.rules.read_text(), self.original)
        for messages, _ in self.calls[2:]:
            self.assertIn(MARKER, str(messages))
            self.assertIn('previously checked batch', str(messages))
        # Rejection is still present after tool output and parser feedback.
        self.assertIn('docs/_build/html/', str(self.calls[-1][0]))

    def test_retained_during_focused_anchor_correction_and_revised_review(self):
        revision = gate.edit('$(MAKE) -C docs html')
        revision['edits'][0]['old_text'] = '\toverride_dh_auto_build-indep:\n\tpython3 setup.py build_sphinx'
        revision['edits'][0]['new_text'] = '\toverride_dh_auto_build-indep:\n\t$(MAKE) -C docs html'
        result = self.run_script([
            gate.edit('dh_auto_build'), gate.check('contradicted'),
            revision, gate.edit('$(MAKE) -C docs html'), gate.check(), gate.APPROVE, gate.STOP])
        self.assertIn('Focused edit correction', str(self.calls[3][0]))
        for index in (2, 3):
            self.assertIn(MARKER, str(self.calls[index][0]))
            self.assertEqual(self.calls[index][1], self.original)
        self.assertIn('$(MAKE) -C docs html', self.rules.read_text())
        self.assertEqual(sum(a.applied for a in result.attempts), 1)

    def test_verifier_is_independent_and_supported_check_clears_feedback(self):
        self.run_script([
            gate.edit('dh_auto_build'), gate.check('contradicted'),
            gate.edit('$(MAKE) -C docs html'), gate.check(), gate.APPROVE, gate.STOP])
        self.assertIn(MARKER, str(self.calls[2][0]))
        self.assertNotIn(MARKER, str(self.calls[3][0]))  # Independent verifier
        self.assertNotIn(MARKER, str(self.calls[5][0]))  # Supported batch applied

    def test_latest_assessment_replaces_older_one_without_growing_history(self):
        first = gate.check('insufficient_evidence');first['reason'] = 'FIRST_CHECK_SENTINEL'
        second = gate.check('contradicted');second['reason'] = 'SECOND_CHECK_SENTINEL'
        self.run_script([gate.edit('dh_auto_build'), first,
                         gate.APPROVE, second, gate.STOP])
        final = str(self.calls[-1][0])
        self.assertIn('SECOND_CHECK_SENTINEL', final)
        self.assertNotIn('FIRST_CHECK_SENTINEL', final)
        self.assertEqual(final.count(MARKER), 1)
