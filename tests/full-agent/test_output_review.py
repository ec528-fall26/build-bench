"""Output-preservation review is conditional and does not claim build success."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
AGENT = Path(__file__).resolve().parents[2] / 'buildbench-starter-kit-0.1.0-rc.5/agents/full-agent'
sys.path.insert(0, str(AGENT))
from src.types import Edit
from src.contracts import Attempt
from src.candidates import CandidateSnapshot
from src.prompts import attempt_result_message
from src.proposal_review import render_review, packaging_review_instructions

class OutputReviewTests(unittest.TestCase):
    def test_no_guess_about_why_empty_batch_failed(self):
        message = attempt_result_message(Attempt(1, 'h', [], 0, None, {}),
                                         note='Refused edit: required outputs removed')
        self.assertIn('Refused edit: required outputs removed', message)
        self.assertNotIn('usual cause', message)
        self.assertIn('actual cause', message)

    def test_no_packaging_review_for_unrelated_source(self):
        self.assertEqual(packaging_review_instructions([Edit('input/src/a.c', 'a', 'b')]), '')

    def test_build_recipe_requires_output_to_producer_connection(self):
        message = packaging_review_instructions([Edit('input/debian/rules', 'old', 'new')])
        for phrase in ('file:line', 'working directory', 'replacement command',
                       'installing', 'approval reason', 'not build feedback'):
            self.assertIn(phrase, message)

    def test_pending_review_contains_diff_and_output_check_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'input/debian/rules'
            path.parent.mkdir(parents=True)
            original = 'override_dh_auto_build:\n\told-builder\n'
            path.write_text(original)
            edit = Edit('input/debian/rules', 'old-builder', 'new-builder')
            snapshot = CandidateSnapshot.capture(root, [edit.path])
            turn = SimpleNamespace(edits=[edit], hypothesis='replace producer')
            text, counts = render_review(snapshot, turn, [], historical=True)
            self.assertIn('Required output review', text)
            self.assertIn('+\tnew-builder', text)
            self.assertEqual(counts[0]['matches'], 1)
            self.assertEqual(path.read_text(), original)
