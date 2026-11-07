"""read_log and search_log, tools during a correction, and no-build wording.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import prompts  # noqa: E402
from src.contracts import AgentConfig, ToolRequest  # noqa: E402
from src.tools import LOG_LINE_CHARS, MAX_OUTPUT_CHARS, run_tool  # noqa: E402
from src.types import CaseContext  # noqa: E402
import test_wiring  # noqa: E402
from test_wiring import Script, edit, reply  # noqa: E402


class LogTools(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = self.root / "initial-build.log"
        self.log.write_bytes(b"".join(b"step %d\n" % n for n in range(1, 101))
                             + b"\x1b[31mdpkg-gensymbols: warning: some symbols disappeared\x1b[0m\n"
                             + b"- _ZN3bpp4Text4wrapEm@Base 2.4.1\n")

    def tool(self, tool, **args):
        return run_tool(ToolRequest(tool, args), self.root, log_path=self.log)

    def test_read_log_numbers_the_requested_lines(self):
        result = self.tool("read_log", start_line=99, end_line=200)
        self.assertEqual(result.text.splitlines(), [
            "=== build log, lines 99-102 ===", "99: step 99", "100: step 100",
            "101: dpkg-gensymbols: warning: some symbols disappeared",
            "102: - _ZN3bpp4Text4wrapEm@Base 2.4.1"])
        self.assertFalse(result.truncated)

    def test_search_log_finds_lines_and_counts_the_log(self):
        result = self.tool("search_log", regex=r"symbols|@Base")
        self.assertIn("the log has 102 lines", result.text)
        self.assertIn("101: dpkg-gensymbols", result.text)
        self.assertIn("102: - _ZN3bpp", result.text)
        self.assertNotIn("step", result.text)

    def test_output_is_bounded(self):
        self.log.write_text(("x" * 2000 + "\n") * 500)
        for result in (self.tool("read_log", start_line=1, end_line=500),
                       self.tool("search_log", regex="x")):
            with self.subTest(tool=result.request.tool):
                self.assertTrue(result.truncated)
                self.assertLess(len(result.text), MAX_OUTPUT_CHARS + 200)
                self.assertIn("[cut]", result.text)
                self.assertLess(max(map(len, result.text.splitlines())), LOG_LINE_CHARS + 20)
        self.assertIn("the log has 500 lines", self.tool("search_log", regex="x").text)

    def test_bad_arguments_and_no_log_are_reported_not_raised(self):
        cases = [("read_log", {"start_line": 5, "end_line": 1}, "invalid line range"),
                 ("read_log", {"start_line": "1", "end_line": 2}, "must be integers"),
                 ("search_log", {"regex": "("}, "invalid regular expression"),
                 ("search_log", {}, "nonempty string")]
        for tool, args, reason in cases:
            with self.subTest(tool=tool, args=args):
                self.assertIn(reason, self.tool(tool, **args).text)
        result = run_tool(ToolRequest("read_log", {"start_line": 1, "end_line": 2}), self.root)
        self.assertIn("unavailable (no build log", result.text)

    def test_past_the_end(self):
        self.assertIn("only 102 lines", self.tool("read_log", start_line=500, end_line=600).text)


class Wiring(unittest.TestCase):
    setUp = test_wiring.Adapter.setUp
    run_with = test_wiring.Adapter.run_with

    def test_the_model_can_read_the_initial_log(self):
        script = Script(
            reply(action="tool", requests=[
                {"tool": "search_log", "args": {"regex": "fatal error"}}], why="look"),
            reply(action="stop", reason="done"))
        self.run_with(script)
        self.assertIn("2: crc32c/crc32c_prefetch.h:18:10: fatal error", script.user(1))

    def test_without_build_feedback_the_prompts_say_edits_are_not_built(self):
        # A submitted run: the switch is on but no adapter exists.
        script = Script(reply(action="stop", reason="done"))
        self.run_with(script, config=AgentConfig(use_no_build_control=False))
        system, user = script.sent[0]
        self.assertIn("not test-built", system["content"])
        self.assertIn("once your edits form a complete repair", system["content"])
        self.assertNotIn("builds used", user["content"])

    def test_a_correction_can_read_the_file_before_fixing_old_text(self):
        config = "input/src/crc32c/crc32c_config.h"
        script = Script(
            edit("#define HAVE_MM_PREFETCH  1\n", "#define HAVE_MM_PREFETCH 0\n", "guess"),
            reply(action="tool", requests=[{"tool": "read_file", "args": {
                "path": config, "start_line": 14, "end_line": 16}}], why="copy"),
            edit("#define HAVE_MM_PREFETCH 1\n", "#define HAVE_MM_PREFETCH 0\n", "fixed"),
            reply(action="stop", reason="done"))
        result, _ = self.run_with(script)
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertIn("Results of your tool requests", script.user(2))
        self.assertIn("HAVE_MM_PREFETCH 0", (self.worktree / config).read_text())


class LogWindow(unittest.TestCase):
    def test_tools_off_prompt_does_not_name_the_log_tools(self):
        text = prompts.system_prompt(AgentConfig(use_no_build_control=False, use_tools=False))
        self.assertNotIn("read_log", text)
        self.assertNotIn("search_log", text)

    def test_context_is_unchanged(self):
        # CaseContext is shared by every part; the log stays a loop argument.
        self.assertEqual(list(CaseContext.__dataclass_fields__),
                         ["case_id", "worktree", "log_tail", "task_metadata"])


if __name__ == "__main__":
    unittest.main()
