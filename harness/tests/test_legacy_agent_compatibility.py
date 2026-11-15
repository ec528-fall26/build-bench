"""Check that the harness accepts the historical 0.3.2 configuration."""

import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

run_case = importlib.import_module("harness.run_case")


class LegacyAgentCompatibility(unittest.TestCase):
    def test_no_feedback_without_new_budget_settings(self):
        # Deliberately omit use_no_build_control and no_build_max_attempts.
        config = SimpleNamespace(use_build_feedback=False)
        result = SimpleNamespace(
            attempts=[], kept_attempts=[], message="No repair", usage={}
        )
        adapter = SimpleNamespace(replies=[])
        record = {
            "termination_reason": "model_stop",
            "termination_message": "No repair",
            "config": {"use_build_feedback": False},
            "attempts": [],
            "turns": [],
            "kept_attempts": [],
            "malformed_count": 0,
        }
        agent = SimpleNamespace(
            config_from_env=lambda: config,
            client_completion=lambda model: model,
            run_agent=Mock(return_value=(result, adapter)),
            result_record=Mock(return_value=record),
        )
        feedback = SimpleNamespace(records=[])
        manifest = {
            "patch_policy": {},
            "build": {"recipe": "example_1.0.dsc"},
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = {}
            with patch(
                "harness.build_feedback.LocalBuildFeedback",
                return_value=feedback,
            ):
                _, applied = run_case.run_full_agent(
                    agent, SimpleNamespace(case_id="example"),
                    root / "build.log", object(), manifest,
                    root, root, row, excerpt=False, runner=Mock(),
                )

            self.assertEqual(applied, 0)
            self.assertEqual(row["builds"], [])
            self.assertIsNone(agent.run_agent.call_args.kwargs["budget"])
            self.assertIs(
                agent.run_agent.call_args.kwargs["config"], config
            )
            self.assertTrue((root / "replies.jsonl").is_file())
