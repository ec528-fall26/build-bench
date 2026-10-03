"""Guard: the one-shot baseline is frozen as one-shot-v1.

Every Demo 2, Demo 3 and final comparison runs against this exact agent: the
same code, model, prompt and settings. If this test fails, someone changed the
baseline. That is never a routine fix:

  - If the change was not meant for the baseline, undo it and make it in the full
    agent's own folder instead.
  - If it was deliberate, it creates one-shot-v2: bump agent.yaml's version,
    update FROZEN below, tag the commit, re-run every baseline number, and
    announce the change at the next demo.

The fingerprint covers agent.yaml's version and every .py file in the agent's
src/ (harness.run_case.agent_version). See docs/one-shot-agent.md.

Run from the repository root:  python3 -m unittest discover -s harness/tests -t .
"""

import unittest

from harness.run_case import AGENT_DIR, agent_version


FROZEN = "1.0.0+16e31a424d0d"  # tag one-shot-v1


class FrozenBaseline(unittest.TestCase):
    def test_baseline_is_unchanged(self):
        self.assertEqual(
            agent_version(AGENT_DIR), FROZEN,
            "The frozen one-shot baseline changed. Read this test's docstring before "
            "updating FROZEN: a deliberate change means one-shot-v2 and re-running "
            "every baseline number.",
        )


if __name__ == "__main__":
    unittest.main()
