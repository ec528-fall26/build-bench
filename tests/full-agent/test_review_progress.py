"""Pending-patch progress and cited command semantics without live builds."""
import unittest
import test_reviewed_repairs as reviewed
from test_reviewed_repairs import turn
from src.types import Edit
from src.contracts import ToolRequest
from src.proposal_review import edit_identity, render_review
from src.command_evidence import command_evidence

class ReviewProgressTests(unittest.TestCase):
    setUp = reviewed.ReviewedRepairs.setUp
    loop = reviewed.ReviewedRepairs.loop
    snapshot = reviewed.ReviewedRepairs.snapshot

    def test_rewording_does_not_reset_pending_review(self):
        actions = [turn('edit', [self.edit]) for _ in range(4)]
        for number, action in enumerate(actions):
            action.hypothesis = f'different explanation {number}'
        result = self.loop(actions)
        self.assertEqual(result.termination_reason, 'review_stalled')
        self.assertEqual(self.a.read_text(), 'oldtype oldtype\n')
        self.assertEqual(len(result.attempts), 0)
        self.assertEqual(sum(e['event'] == 'repeated_proposal' for e in result.review_events), 3)
        self.assertFalse(any(e['event'] == 'approved' for e in result.review_events))
        self.assertIn('already pending review', self.requests[2].controller_note)

    def test_explicit_approval_after_repeat_applies_once(self):
        result = self.loop([turn('edit', [self.edit]), turn('edit', [self.edit]),
                            turn('approve'), turn('stop')])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertEqual(self.a.read_text(), 'newtype newtype\n')

    def test_actual_revision_resets_repeat_counter(self):
        revised = Edit('input/a.c', 'oldtype', 'replacement', True)
        result = self.loop([turn('edit', [self.edit]), turn('edit', [self.edit]),
                            turn('edit', [self.edit]), turn('edit', [revised]),
                            turn('edit', [revised]), turn('approve'), turn('stop')])
        self.assertEqual(result.termination_reason, 'model_stop')
        self.assertEqual(self.a.read_text(), 'replacement replacement\n')

    def test_read_only_tools_do_not_reset_repeat_count(self):
        read = turn('tool', tools=[ToolRequest('read_file', {'path': 'input/a.c'})])
        result = self.loop([turn('edit', [self.edit]), turn('edit', [self.edit]), read,
                            turn('edit', [self.edit]), turn('edit', [self.edit])])
        self.assertEqual(result.termination_reason, 'review_stalled')
        self.assertEqual(self.a.read_text(), 'oldtype oldtype\n')

    def test_identity_preserves_order_and_replace_all(self):
        one = turn('edit', [self.edit, Edit('input/b.c', 'oldtype', 'x')])
        two = turn('edit', list(reversed(one.edits)))
        self.assertNotEqual(edit_identity(one), edit_identity(two))
        self.assertNotEqual(edit_identity(turn('edit', [self.edit])),
                            edit_identity(turn('edit', [Edit('input/a.c', 'oldtype', 'newtype')])))

    def test_command_reference_is_specific_and_cited(self):
        e = Edit('input/debian/rules', 'python3 setup.py build_sphinx', 'dh_sphinxdoc')
        text = command_evidence([e, e])
        self.assertIn('does not generate', text)
        self.assertIn('https://sources.debian.org/', text)
        self.assertIn('not target-environment observations', text)
        self.assertEqual(text.count('=== Command reference:'), 1)
        self.assertNotIn('intbitset', text)
        self.assertNotIn('docs/_build/html', text)

    def test_no_reference_for_unrelated_or_substring_commands(self):
        for path, command in [('input/a.c', 'dh_sphinxdoc'),
                              ('input/debian/rules', 'my_dh_sphinxdoc_wrapper'),
                              ('input/debian/rules', 'make all')]:
            self.assertEqual(command_evidence([Edit(path, command, 'other')]), '')

    def test_reference_reaches_pending_review_without_file_changes(self):
        path = self.root / 'input/debian/rules'
        path.parent.mkdir()
        path.write_text('python3 setup.py build_sphinx\n')
        edit = Edit('input/debian/rules', 'python3 setup.py build_sphinx', 'dh_sphinxdoc')
        text, _ = render_review(self.snapshot([edit]), turn('edit', [edit]), [])
        self.assertIn('does not generate', text)
        self.assertIn('Required output review', text)
        self.assertEqual(path.read_text(), 'python3 setup.py build_sphinx\n')
