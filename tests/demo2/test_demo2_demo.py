import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('demo2_demo', ROOT / 'scripts/demo2_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

class DriverTests(unittest.TestCase):
    def test_controls_use_real_harness_with_distinct_runs(self):
        jobs = demo.plan('controls', Path('/bundle'), Path('/out'), 'python3')
        self.assertEqual([j[2] for j in jobs], [True, False])
        self.assertNotEqual(jobs[0][0], jobs[1][0])
        for _, cmd, _ in jobs:
            self.assertIn('harness.run_case', cmd)
            self.assertIn('--replay', cmd)
            self.assertNotIn('--log', cmd)  # reproduce baseline
            self.assertNotIn('--live', cmd)

    def test_live_never_supplies_reference(self):
        for mode in ('live-one-shot', 'live-full'):
            _, cmd, expectation = demo.plan(mode, Path('/b'), Path('/o'))[0]
            self.assertIn('--live', cmd)
            self.assertNotIn('--replay', cmd)
            self.assertIsNone(expectation)

    def test_stale_environment_is_reset(self):
        original = {'BB_AGENT_USE_HISTORY': '0', 'BB_MODEL_TRACE': '/old',
                    'BB_REPAIR_STRATEGY': 'small-group', 'BB_REVIEW_EDITS': '1',
                    'BB_MODEL_API_KEY': 'private'}
        env = demo.controlled_env(original)
        self.assertEqual(env['BB_AGENT_USE_HISTORY'], '1')
        self.assertEqual(env['BB_REVIEW_EDITS'], '0')
        self.assertEqual(env['BB_REPAIR_STRATEGY'], 'default')
        self.assertNotIn('BB_MODEL_TRACE', env)
        self.assertEqual(original['BB_AGENT_USE_HISTORY'], '0')
        self.assertEqual(env['BB_MODEL_API_KEY'], 'private')

    def test_review_is_explicit(self):
        env = demo.controlled_env({}, True)
        self.assertEqual(env['BB_REVIEW_EDITS'], '1')
        self.assertEqual(env['BB_REPAIR_STRATEGY'], 'shared-cause')

    def test_success_requires_final_artifacts(self):
        row = {'repaired': True, 'result': {'status': 'succeeded', 'artifact_validation_passed': False}}
        self.assertFalse(demo.verified(row))
        self.assertIsNotNone(demo.check_row(row, True))
        row['result']['artifact_validation_passed'] = True
        self.assertTrue(demo.verified(row))
        self.assertIsNone(demo.check_row(row, True))

    def test_negative_must_apply_and_build(self):
        row = {'termination_reason': 'no_edit_applied', 'repaired': False}
        self.assertIsNotNone(demo.check_row(row, False))
        row.update(termination_reason='build_failed', edits_applied=1,
                   result={'status': 'failed', 'artifact_validation_passed': False})
        self.assertIsNone(demo.check_row(row, False))

    def test_live_failure_is_not_forced_success(self):
        self.assertIsNone(demo.check_row({'repaired': False, 'termination_reason': 'build_failed'}, None))
        self.assertIsNotNone(demo.check_row({'agent_termination': 'model_error'}, None))
        self.assertIsNotNone(demo.check_row({'termination_reason': 'build_error'}, None))

    def test_revise_uses_full_agent_script(self):
        _, cmd, expectation = demo.plan('revise-replay', Path('/b'), Path('/o'))[0]
        self.assertEqual(cmd[cmd.index('--agent') + 1], 'full-agent')
        self.assertTrue(expectation)
        self.assertIn('harness/replays/rcran-full-revise.json', cmd)

if __name__ == '__main__':
    unittest.main()
