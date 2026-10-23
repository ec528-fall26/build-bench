import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import demo2_retest as retest

class RetestTests(unittest.TestCase):
    def test_schedule_includes_all_cases_and_agents(self):
        cases = ['rcran'] + [f'case-{i}' for i in range(10)]
        jobs = retest.make_jobs(cases, 1)
        self.assertEqual(len(jobs), 22)
        self.assertEqual(len({j['job_id'] for j in jobs}), 22)
        self.assertEqual([j['case_id'] for j in jobs[:2]], ['rcran', 'rcran'])
        for case in cases:
            self.assertEqual({j['agent'] for j in jobs if j['case_id'] == case}, {'one-shot', 'full-agent'})

    def test_three_repeats_not_only_successes(self):
        jobs = retest.make_jobs(['rcran'] + [str(i) for i in range(10)], 3)
        self.assertEqual(len(jobs), 66)
        self.assertEqual({j['repeat'] for j in jobs}, {1, 2, 3})

    def test_interrupted_attempt_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first = retest.next_attempt(base)
            first.mkdir()
            (first / 'partial.log').write_text('interrupted')
            second = retest.next_attempt(base)
            self.assertNotEqual(first, second)
            self.assertFalse(retest.completed(first))
            self.assertEqual((first / 'partial.log').read_text(), 'interrupted')

    def test_failed_completed_job_counts_as_evaluated(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            jobs = retest.make_jobs(['case-a'], 1)
            folder = out / 'jobs' / jobs[0]['job_id'] / 'attempt-01'
            folder.mkdir(parents=True)
            (folder / 'summary.json').write_text(json.dumps([{
                'repaired': False, 'termination_reason': 'build_failed', 'check_error': None}]))
            rows = retest.summarize(out, jobs)
            self.assertEqual(rows[0]['status'], 'completed')
            self.assertFalse(rows[0]['repaired'])
            self.assertEqual(rows[1]['status'], 'not_completed')
            self.assertTrue(retest.completed(folder))

    def test_already_building_not_a_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            jobs = retest.make_jobs(['case-a'], 1)
            folder = out / 'jobs' / jobs[0]['job_id'] / 'attempt-01'
            folder.mkdir(parents=True)
            (folder / 'summary.json').write_text(json.dumps([{
                'repaired': False, 'termination_reason': 'case_not_failing'}]))
            rows = retest.summarize(out, jobs)
            self.assertFalse(rows[0]['repaired'])
            self.assertIn('case_not_failing', (out / 'results.md').read_text())

if __name__ == '__main__':
    unittest.main()
