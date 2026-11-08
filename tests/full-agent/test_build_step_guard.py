"""Explicit build bypass detection, including valid replacements and fallback."""
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
from src.rules_guard import check

class BuildStepGuardTests(unittest.TestCase):
    def test_rejects_build_producers_replaced_with_noops(self):
        for command in ("python3 setup.py build_sphinx", "dh_auto_build",
                        "sphinx-build -b html docs docs/out", "$(MAKE) -C docs html",
                        "cmake --build build", "make all", "ninja build"):
            for replacement in (":", "true", "/bin/true", "@true # bypass"):
                with self.subTest(command=command, replacement=replacement):
                    self.assertIn("required outputs", check("input/debian/rules", command, replacement))

    def test_rejects_complete_override_recipe(self):
        before = "override_dh_auto_build-indep:\n\tpython3 setup.py build_sphinx\n"
        after = "override_dh_auto_build-indep:\n\t: \n"
        self.assertIsNotNone(check("input/debian/rules", before, after))

    def test_allows_actual_producer_replacement(self):
        self.assertIsNone(check("input/debian/rules", "python3 setup.py build_sphinx",
                                "$(MAKE) -C docs html"))

    def test_allows_removing_override_to_restore_default(self):
        self.assertIsNone(check("input/debian/rules",
                                "override_dh_auto_build:\n\tdh_auto_build\n", ""))

    def test_allows_existing_noop_and_nonbuild_commands(self):
        for before in (":", "echo informational message", "$(MAKE) clean"):
            self.assertIsNone(check("input/debian/rules", before, ":"))

    def test_does_not_match_source_strings(self):
        self.assertIsNone(check("input/module.py", "python3 setup.py build_sphinx", ":"))

    def test_keeps_real_command_alongside_noop(self):
        self.assertIsNone(check("input/debian/rules", "dh_auto_build", ":\ndh_auto_build"))

import test_wiring
from test_wiring import Script, reply
from src.contracts import AgentConfig

class BuildStepGuardIntegration(unittest.TestCase):
    setUp = test_wiring.Adapter.setUp
    run_with = test_wiring.Adapter.run_with

    def test_noop_is_refused_before_write_and_reason_reaches_model(self):
        target = self.worktree / 'input/debian/rules'
        target.parent.mkdir(parents=True, exist_ok=True)
        original = 'override_dh_auto_build-indep:\n\tpython3 setup.py build_sphinx\n'
        target.write_text(original)
        edit = {'path': 'input/debian/rules', 'old_text': 'python3 setup.py build_sphinx',
                'new_text': ':'}
        stop = reply(action='stop', reason='done')
        script = Script(reply(action='edit', hypothesis='h', evidence=['e'], edits=[edit]),
                        stop, stop, stop)
        result, _ = self.run_with(script, config=AgentConfig(use_rules_guard=True))
        self.assertEqual(target.read_text(), original)
        self.assertEqual(result.attempts[0].applied, 0)
        self.assertIn('required outputs', script.user(1))
