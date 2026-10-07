"""The full agent's scaffold: shared contracts and the Part A, B, D stand-ins.

These pin the agreed interfaces (docs/full-agent.md, "Shared contracts") so a
change to them shows up in CI. When a part replaces its stand-in, it brings
its own tests; the signature checks here stay.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

import dataclasses
from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "buildbench-starter-kit-0.1.0-rc.2/agents"
AGENT = AGENTS / "full-agent"
sys.path.insert(0, str(AGENT))

from src.build_feedback import NoBuildFeedback  # noqa: E402
from src.contracts import (  # noqa: E402
    AgentConfig, Attempt, Budget, BuildFeedback, BuildResult, Diagnostic,
    ModelTurn, ToolRequest, ToolResult,
)
from src.evidence import diagnose  # noqa: E402
from src.tools import TOOLS, run_tool  # noqa: E402
from src.types import Edit  # noqa: E402


def fields(cls):
    return [f.name for f in dataclasses.fields(cls)]


class Contracts(unittest.TestCase):
    def test_field_names_match_the_plan(self):
        expected = {
            Diagnostic: ["message", "stage", "log_path", "line", "resolved",
                         "source_line", "log_offset"],
            ToolRequest: ["tool", "args"],
            ToolResult: ["request", "text", "truncated"],
            ModelTurn: ["action", "tool_requests", "hypothesis", "evidence",
                        "edits", "reason"],
            BuildResult: ["succeeded", "stage_reached", "diagnostics", "duration_seconds"],
            Attempt: ["number", "hypothesis", "edits", "applied", "build", "tokens"],
        }
        for cls, names in expected.items():
            with self.subTest(cls=cls.__name__):
                self.assertEqual(fields(cls), names)

    def test_budget_defaults_are_the_research_settings(self):
        self.assertEqual(dataclasses.asdict(Budget()), {
            "max_attempts": 5, "max_builds": 3, "max_tokens": 100_000,
            "max_seconds": 1_800, "reserve_seconds": 120})

    def test_every_component_is_on_by_default(self):
        self.assertTrue(all(dataclasses.asdict(AgentConfig()).values()))
        self.assertEqual(fields(AgentConfig), ["use_evidence_manager", "use_tools",
                                               "use_build_feedback", "use_history"])

    def test_an_attempt_holds_part4_edits_and_a_build(self):
        diagnostic = Diagnostic("fatal error: xmmintrin.h: No such file or directory",
                                "build", "crc32c/crc32c_prefetch.h", 18,
                                ["input/src/crc32c/crc32c_prefetch.h"],
                                "#include <xmmintrin.h>", 1234)
        build = BuildResult(False, "build", [diagnostic], 91.5)
        attempt = Attempt(1, "the x86 header is included unconditionally",
                          [Edit("input/src/a.h", "x", "y")], 1, build,
                          {"input_tokens": None, "output_tokens": None})
        self.assertEqual(attempt.build.diagnostics[0].resolved,
                         ["input/src/crc32c/crc32c_prefetch.h"])


class PartAStandIn(unittest.TestCase):
    def test_returns_diagnostics_for_a_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "build.log"
            log.write_text("make: *** [all] Error 1\n\n")
            result = diagnose(log, Path(tmp))
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Diagnostic)
        self.assertEqual(result[0].message, "make: *** [all] Error 1")

    def test_missing_or_empty_log_gives_nothing_and_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.log"
            empty.write_text("")
            self.assertEqual(diagnose(Path(tmp) / "missing.log", Path(tmp)), [])
            self.assertEqual(diagnose(empty, Path(tmp)), [])


class PartBStandIn(unittest.TestCase):
    def test_every_tool_answers_without_raising(self):
        for name in TOOLS + ("delete_everything",):
            with self.subTest(tool=name):
                request = ToolRequest(name, {"path": "../../etc/passwd"})
                result = run_tool(request, Path("/nonexistent"))
                self.assertIsInstance(result, ToolResult)
                self.assertIs(result.request, request)
                self.assertIn("unavailable", result.text)

    def test_tool_names_match_the_plan(self):
        self.assertEqual(set(TOOLS), {"list_dir", "find_files", "search", "read_file",
                                      "current_diff", "patch_series"})


class PartDStandIn(unittest.TestCase):
    def test_hosted_build_feedback_is_unavailable(self):
        feedback = NoBuildFeedback()
        self.assertIsInstance(feedback, BuildFeedback)
        self.assertIsNone(feedback.request_build(Path("/nonexistent")))


class SameModelAsBaseline(unittest.TestCase):
    # Both agents are packages named `src`, so compare the source text.
    def test_model_id_matches_the_frozen_baseline(self):
        pattern = re.compile(r'^MODEL_ID = "([^"]+)"$', re.MULTILINE)
        ids = [pattern.search((AGENTS / name / "src/model_client.py").read_text()).group(1)
               for name in ("one-shot", "full-agent")]
        self.assertEqual(ids[0], ids[1])


class NoHostSideCode(unittest.TestCase):
    def test_agent_never_runs_the_validator(self):
        # The local build callback belongs in the harness, never the ZIP.
        for path in (AGENT / "src").glob("*.py"):
            with self.subTest(file=path.name):
                text = path.read_text()
                self.assertNotIn("run.sh", text)
                self.assertNotIn("docker", text.lower())


if __name__ == "__main__":
    unittest.main()
