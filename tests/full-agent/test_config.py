"""Part E: the ablation switches read from the environment.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

import dataclasses
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
                      "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"))
from src.config import config_env, config_from_env, config_record, env_name  # noqa: E402
from src.contracts import AgentConfig  # noqa: E402


FIELDS = [field.name for field in dataclasses.fields(AgentConfig)]


class FromEnvironment(unittest.TestCase):
    def test_nothing_set_is_the_competition_configuration(self):
        self.assertEqual(config_from_env({}), AgentConfig())
        self.assertEqual(config_from_env({"BB_AGENT_USE_HISTORY": "", "BB_MODEL_API_KEY": "k"}),
                         AgentConfig())

    def test_one_variable_per_contract_field(self):
        self.assertEqual([env_name(name) for name in FIELDS], [
            "BB_AGENT_USE_EVIDENCE_MANAGER", "BB_AGENT_USE_TOOLS",
            "BB_AGENT_USE_BUILD_FEEDBACK", "BB_AGENT_USE_HISTORY"])
        for name in FIELDS:
            with self.subTest(field=name):
                config = config_from_env({env_name(name): "0"})
                self.assertEqual(dataclasses.asdict(config),
                                 {other: other != name for other in FIELDS})

    def test_accepted_spellings(self):
        for value in ("1", "true", "YES", " on "):
            self.assertTrue(config_from_env({"BB_AGENT_USE_HISTORY": value}).use_history, value)
        for value in ("0", "False", "no", "OFF"):
            self.assertFalse(config_from_env({"BB_AGENT_USE_HISTORY": value}).use_history, value)

    def test_an_unclear_value_raises_instead_of_running_the_wrong_arm(self):
        for value in ("2", "disabled", "of", "-"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    config_from_env({"BB_AGENT_USE_HISTORY": value})
                self.assertIn("BB_AGENT_USE_HISTORY", str(caught.exception))


class RoundTrip(unittest.TestCase):
    def test_config_env_reproduces_the_configuration(self):
        for config in (AgentConfig(), AgentConfig(use_history=False),
                       AgentConfig(False, False, False, False)):
            with self.subTest(config=config):
                self.assertEqual(config_from_env(config_env(config)), config)

    def test_record_is_the_switches_by_field_name(self):
        self.assertEqual(config_record(AgentConfig(use_history=False)),
                         {"use_evidence_manager": True, "use_tools": True,
                          "use_build_feedback": True, "use_history": False})


if __name__ == "__main__":
    unittest.main()
