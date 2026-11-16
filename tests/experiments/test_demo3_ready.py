"""No Docker, credentials or model calls: test local readiness configuration."""
import importlib.util
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('demo3_ready', ROOT / 'scripts/demo3_ready.py')
ready = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ready)


class LocalReadiness(unittest.TestCase):
    def test_live_runner_keeps_security_and_inherits_key_by_name(self):
        original = (ROOT / ready.KIT / 'runner/run-agent-case.sh').read_text()
        changed = ready.enable_local_model(original)
        self.assertIn('--network bridge', changed)
        self.assertIn('-e BB_MODEL_API_KEY', changed)
        for option in ('--read-only', '--cap-drop ALL', '--security-opt no-new-privileges'):
            self.assertIn(option, changed)
        subprocess.run(['bash', '-n'], input=changed, text=True, check=True)
        self.assertEqual((ROOT / ready.KIT / 'runner/run-agent-case.sh').read_text(), original)

    def test_offline_environment_has_no_model_key_or_stale_flags(self):
        env = ready.clean_environment({'PATH': '/bin', 'BB_MODEL_API_KEY': 'private',
                                       'BB_EXPERIMENT_FRESH_REVIEW': '1'}, '', '', False)
        self.assertEqual(env, {'PATH': '/bin'})

    def test_live_region_reaches_actual_agent_and_hosted_overrides_removed(self):
        env = ready.clean_environment({'BB_MODEL_NAME': 'wrong', 'BB_MODEL_TOKEN_FILE': '/wrong'},
                                      'private', 'https://example.invalid/openai/v1', True)
        self.assertEqual(env['BB_MODEL_BASE_URL'], 'https://example.invalid/openai/v1')
        self.assertEqual(env['BB_MODEL_API_KEY'], 'private')
        self.assertNotIn('BB_MODEL_NAME', env)
        self.assertNotIn('BB_MODEL_TOKEN_FILE', env)
        self.assertEqual(env['BB_AGENT_USE_BUILD_FEEDBACK'], '0')

    def test_unexpected_runner_is_rejected(self):
        with self.assertRaises(ValueError):
            ready.enable_local_model('docker run --network host')
        with self.assertRaises(ValueError):
            ready.enable_local_model('  --network none \\\n' * 2)
