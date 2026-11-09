"""Loop control and protocol hygiene (campaign 2026-11-09, M5 and the cheap parts of M7).

A stub model drives the loop (all-fixed stop gate, identical-request stubs, the
investigation soft cap) and the model adapter (reasoning-only replies, malformed
follow-ups, VERDICT-first attempt feedback, dependency facts). No network, no build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent -p test_loop_control.py
"""

from copy import deepcopy
import itertools
from pathlib import Path
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(ROOT))

from src import loop, prompts  # noqa: E402
from src.contracts import AgentConfig, Attempt, BuildResult, Diagnostic  # noqa: E402
from src.loop import ALL_FIXED_STOP, STUB_PREFIX, run_loop  # noqa: E402
from src.reply import extract_action_json, parse_turn  # noqa: E402
from src.types import Edit  # noqa: E402
import test_wiring  # noqa: E402
from test_wiring import CONFIG, Script, edit as wedit, reply  # noqa: E402
from test_arch_slice import PREFETCH, STOP, Worktree, diagnostic, edit, turn  # noqa: E402

PREFETCH_PATH = "input/src/crc32c/crc32c_prefetch.h"
CONFIG_PATH = "input/src/crc32c/crc32c_config.h"
FIXING_EDIT = turn([edit("#define HAVE_MM_PREFETCH 1", "#define HAVE_MM_PREFETCH 0", CONFIG_PATH)])
INCLUDE = diagnostic("fatal error: xmmintrin.h: No such file or directory",
                     "crc32c/crc32c_prefetch.h", 8, [PREFETCH_PATH], "#include <xmmintrin.h>")


def tool(*requests):
    return SimpleNamespace(
        action="tool", reason="look", edits=[],
        tool_requests=[SimpleNamespace(tool=t, args=a) for t, a in requests])


def read(path, end=5):
    return ("read_file", {"path": path, "start_line": 1, "end_line": end})


class LoopWorld(Worktree):
    def setUp(self):
        super().setUp()
        self.write(PREFETCH_PATH, PREFETCH)
        self.write(CONFIG_PATH, "#define HAVE_MM_PREFETCH 1\n")
        for name in "abcd":
            self.write(f"input/src/{name}.c", f"int {name};\n")
        self.context = SimpleNamespace(
            worktree=self.root, case_id="launchpad-mantic-amd64-arm64-r-cran-digest-7a42",
            task_metadata={"target_arch": "arm64"}, log_tail="")
        self.requests = []

    def run_script(self, proposals, **kw):
        proposals = itertools.chain(proposals, itertools.repeat(STOP))

        def next_turn(request):
            self.requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}
        kw.setdefault("initial_diagnostics", [INCLUDE])
        return run_loop(self.context, next_turn=next_turn,
                        config=AgentConfig(use_build_feedback=False), **kw)


class AllFixedStopGate(LoopWorld):
    def test_third_tool_turn_after_all_fixed_is_refused_with_the_verdict(self):
        result = self.run_script([
            FIXING_EDIT,
            tool(read("input/src/a.c")), tool(read("input/src/b.c")), tool(read("input/src/c.c")),
            STOP])
        self.assertEqual(self.requests[1].prediction_status, "fixed")
        self.assertEqual(result.termination_reason, "model_stop")
        for n in (2, 3):  # the two permitted turns carry no verdict message
            self.assertNotIn(ALL_FIXED_STOP, self.requests[n].controller_note)
        self.assertIn(ALL_FIXED_STOP, self.requests[4].controller_note)
        self.assertEqual(len(self.requests[4].tool_results), 2)  # the third read was not run
        self.assertEqual(self.requests[4].uncounted_tool_turns, 1)

    def test_a_file_already_read_is_refused_at_once(self):
        self.run_script([
            tool(read("input/src/a.c")), FIXING_EDIT, tool(read("input/src/a.c")), STOP])
        self.assertIn(ALL_FIXED_STOP, self.requests[3].controller_note)
        self.assertEqual(len(self.requests[3].tool_results), 1)

    def test_no_gate_while_something_is_not_fixed(self):
        self.run_script([
            turn([edit("#if defined(_MSC_VER)", "#if defined(_MSC_VER) /* x */", PREFETCH_PATH)]),
            tool(read("input/src/a.c")), tool(read("input/src/b.c")), tool(read("input/src/c.c")),
            STOP])
        self.assertEqual(self.requests[1].prediction_status, "not_fixed")
        self.assertEqual(len(self.requests[4].tool_results), 3)
        self.assertNotIn(ALL_FIXED_STOP, "".join(r.controller_note for r in self.requests))

    def test_a_model_that_ignores_the_verdict_is_ended(self):
        result = self.run_script([FIXING_EDIT] + [tool(read(f"input/src/a.c", n)) for n in range(2, 12)],
                                 max_turns=20)
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertIn("controller", result.message)


class IdenticalRequests(LoopWorld):
    SEARCH = ("search", {"regex": "HAVE_MM", "path_glob": "input/**"})

    def test_an_identical_search_is_a_stub_and_is_not_counted(self):
        # max_tool_calls=1: a counted second search would end the run with tool_budget.
        result = self.run_script([tool(self.SEARCH), tool(self.SEARCH), STOP], max_tool_calls=1)
        self.assertEqual(result.termination_reason, "model_stop")
        first, second = self.requests[2].tool_results
        self.assertNotIn(STUB_PREFIX, first.text)
        self.assertEqual(second.text, f"{STUB_PREFIX} 1; the result has not changed")
        self.assertEqual(self.requests[2].uncounted_tool_turns, 1)

    def test_a_search_runs_again_after_an_edit(self):
        self.run_script([tool(self.SEARCH), FIXING_EDIT, tool(self.SEARCH), STOP], max_tool_calls=2,
                        initial_diagnostics=[])
        self.assertNotIn(STUB_PREFIX, self.requests[3].tool_results[-1].text)

    def test_a_read_stays_cached_when_another_file_is_edited(self):
        request = read("input/src/a.c")
        self.run_script([tool(request), FIXING_EDIT, tool(request), STOP], max_tool_calls=1,
                        initial_diagnostics=[])
        self.assertTrue(self.requests[3].tool_results[-1].text.startswith(STUB_PREFIX))

    def test_a_repeat_runs_again_once_its_result_scrolled_out_of_view(self):
        # The adapter shows only the last SHOWN_TOOL_RESULTS distinct results; a stub
        # pointing at a result the model can no longer see would hide it for good.
        first = read("input/src/a.c")
        fillers = [tool(read("input/src/b.c", end=n)) for n in range(1, loop.SHOWN_TOOL_RESULTS + 2)]
        self.run_script([tool(first)] + fillers + [tool(first), STOP], max_tool_calls=40)
        last = self.requests[-2].tool_results[-1]
        self.assertNotIn(STUB_PREFIX, last.text)
        self.assertIn("int a;", last.text)

    def test_a_read_of_an_edited_file_runs_again(self):
        request = read(CONFIG_PATH)
        self.run_script([tool(request), FIXING_EDIT, tool(request), STOP], max_tool_calls=2,
                        initial_diagnostics=[])
        self.assertNotIn(STUB_PREFIX, self.requests[3].tool_results[-1].text)
        self.assertIn("HAVE_MM_PREFETCH 0", self.requests[3].tool_results[-1].text)


class InvestigationCap(LoopWorld):
    def reads(self, n):
        return [tool(read("input/src/a.c", 2 + i)) for i in range(n)]

    def test_nudge_at_eight_turns_without_an_edit(self):
        self.run_script(self.reads(9) + [STOP])
        self.assertEqual(self.requests[7].controller_note, "")  # 7 tool turns done
        self.assertIn("You have used 8 investigation turns without proposing an edit. State the most "
                      "probable cause and the edit, or stop and name the evidence that is missing.",
                      self.requests[8].controller_note)
        self.assertEqual(self.requests[9].controller_note, "")  # one-off

    def test_from_twelve_the_next_reply_must_be_an_edit_or_a_stop(self):
        self.run_script(self.reads(13) + [STOP])
        self.assertIn("12 investigation turns", self.requests[12].controller_note)
        self.assertIn("next reply must be an edit or a stop", self.requests[12].controller_note)

    def test_an_edit_resets_the_count(self):
        self.run_script(self.reads(7) + [FIXING_EDIT] + self.reads(3) + [STOP], initial_diagnostics=[])
        self.assertNotIn("investigation turns", "".join(r.controller_note for r in self.requests))


class Protocol(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.worktree = root / "work"
        source = self.worktree / "input/src/crc32c"
        source.mkdir(parents=True)
        shutil.copy(test_wiring.FIXTURES / "crc32c_config.h", source)
        (source / "crc32c_prefetch.h").write_text("#include <xmmintrin.h>\n")
        self.log = root / "initial-build.log"
        self.log.write_text("   dh_auto_install\ncrc32c/crc32c_prefetch.h:18:10: fatal error: "
                            "xmmintrin.h: No such file or directory\n   18 | #include <xmmintrin.h>\n")

    def run_with(self, script, config=None):
        context = test_wiring.CaseContext("rcran", self.worktree, "", {"target_arch": "aarch64"})
        return test_wiring.run_agent(context, self.log, script,
                                     config=config or AgentConfig(use_no_build_control=False))

    def test_the_prompt_asks_for_the_json_object_only(self):
        self.assertIn("Reply with the JSON object only; no text before or after it", prompts._REPLY)
        self.assertNotIn("reason briefly", prompts._REPLY)

    def test_a_reasoning_only_reply_gets_a_short_follow_up(self):
        script = Script("I will look at the header first.", reply(action="stop", reason="done"))
        result, adapter = self.run_with(script)
        self.assertTrue(adapter.turns[0]["follow_up"])
        last = script.sent[1][-1]
        self.assertEqual(last["role"], "user")
        self.assertLess(len(last["content"]), 200)
        self.assertNotIn("Target architecture", last["content"])
        self.assertEqual(len(adapter.turns[0]["calls"]), 2)
        self.assertEqual(adapter.turns[0]["calls"][0]["finish_reason"], "stop")
        self.assertEqual(adapter.turns[0]["calls"][0]["completion_tokens"], 10)

    def test_wrong_json_continues_the_conversation_instead_of_rebuilding_it(self):
        bad = reply(action="tool", requests=[{"tool": "delete_all", "args": {}}])
        script = Script(bad, reply(action="stop", reason="done"))
        result, _ = self.run_with(script)
        self.assertEqual(result.malformed_count, 1)
        second = script.sent[1]
        self.assertEqual(second[:2], script.sent[0])          # the evidence, once, unchanged
        self.assertEqual(second[2], {"role": "assistant", "content": bad})
        self.assertIn("unknown tool", second[3]["content"])
        self.assertLess(len(second[3]["content"]), 600)

    def test_reasoning_blocks_are_stripped_closed_or_not(self):
        action = '{"action": "stop", "reason": "done"}'
        self.assertEqual(parse_turn(f"<reasoning>draft {action}</reasoning>\n{action}").action, "stop")
        self.assertEqual(parse_turn(f'<think>try {{"action": "tool"}}</think>{action}').action, "stop")
        unclosed = parse_turn(f'<reasoning>maybe {action} but let me')
        self.assertEqual(unclosed.action, "malformed")
        with self.assertRaises(ValueError):
            extract_action_json(f"<think>{action}")

    def test_content_parts_and_a_reasoning_field_do_not_leak_into_the_reply(self):
        body = {"choices": [{"finish_reason": "stop", "message": {
            "content": [{"type": "reasoning", "text": "think"}, {"type": "text", "text": "{}"}],
            "reasoning_content": "secret"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
        client = SimpleNamespace(api_key="k", timeout=100, base_url="u", opener=None, sleep=None,
                                 max_attempts=1)
        with patch("src.model_adapter.request_completion", return_value=body):
            text, finish, usage = test_wiring.client_completion(client)([])
        self.assertEqual((text, finish), ("{}", "stop"))
        self.assertEqual(usage["output_tokens"], 2)

    def test_attempt_feedback_leads_with_the_verdict_line(self):
        script = Script(wedit("#define HAVE_MM_PREFETCH 1\n", "#define HAVE_MM_PREFETCH 0\n"),
                        reply(action="stop", reason="done"), reply(action="stop", reason="done"))
        self.run_with(script)
        second = script.user(1)
        self.assertIn("=== Attempt 1 ===\nVERDICT: applied 1/1; ", second)
        self.assertLess(second.index("VERDICT:"), second.index("Applied 1 of 1 edit"))

    def test_verdict_line_wording(self):
        one = Attempt(1, "h", [Edit("input/a", "x", "y")], 1, None, {})
        self.assertEqual(prompts.verdict_line(one, "fixed", "all good"),
                         "VERDICT: applied 1/1; FIXED (predicted, not built): all good")
        self.assertIn("NOT FIXED (predicted, not built): bad", prompts.verdict_line(one, "not_fixed", "bad"))
        self.assertIn("applied 1/1; unknown: ", prompts.verdict_line(one))
        none = Attempt(2, "h", [Edit("input/a", "x", "y")], 0, None, {})
        self.assertIn("applied 0/1; NOT FIXED: nothing was changed", prompts.verdict_line(none, "fixed"))
        built = Attempt(3, "h", [Edit("input/a", "x", "y")], 1,
                        BuildResult(False, "build", [diagnostic("boom")], 1.0), {})
        self.assertIn("NOT FIXED: the build failed at build: boom", prompts.verdict_line(built, "fixed"))

    def test_notes_are_capped_at_three_and_come_before_the_details(self):
        attempt = Attempt(1, "h", [Edit("input/a", "x", "y")], 1, None, {})
        text = prompts.attempt_result_message(
            attempt, verdict="VERDICT: applied 1/1; unknown: x", notes=["n1", "n2", "n3", "n4"],
            prediction="=== Static prediction ===", note="DETAILS")
        self.assertNotIn("n4", text)
        order = [text.index(part) for part in ("VERDICT", "n1", "n3", "=== Static prediction", "Applied 1 of 1", "DETAILS")]
        self.assertEqual(order, sorted(order))

    def test_dependency_facts_follow_the_architecture_block(self):
        fake = SimpleNamespace(render=lambda worktree, lines, arch: "DEPENDENCY FACTS HERE")
        script = Script(*[reply(action="stop", reason="done")] * 2)
        with patch.dict(sys.modules, {"src.dep_audit": fake}):
            self.run_with(script, AgentConfig())
        user = script.user(0)
        self.assertIn("DEPENDENCY FACTS HERE", user)
        self.assertGreater(user.index("DEPENDENCY FACTS HERE"), user.index("What differs between"))

    def test_a_failing_or_absent_dep_audit_is_silent(self):
        def boom(*args):
            raise RuntimeError("no")
        script = Script(*[reply(action="stop", reason="done")] * 2)
        with patch.dict(sys.modules, {"src.dep_audit": SimpleNamespace(render=boom)}):
            result, _ = self.run_with(script, AgentConfig())
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertIn("What differs between", script.user(0))


class HarnessParity(unittest.TestCase):
    def test_no_build_runs_get_the_hosted_attempt_cap(self):
        from harness import run_case as rc
        agent = rc.load_agent(AGENT)
        seen = {}

        def fake_run_agent(*args, **kwargs):
            seen["budget"] = kwargs.get("budget")
            raise RuntimeError("stop here")
        agent.run_agent = fake_run_agent
        for flag, cap in (("0", 3), ("1", None)):
            seen.clear()
            with patch.dict(os.environ, {"BB_AGENT_USE_BUILD_FEEDBACK": flag}), \
                    patch("harness.build_feedback.LocalBuildFeedback"):
                with self.assertRaises(RuntimeError):
                    rc.run_full_agent(agent, None, Path("x"), SimpleNamespace(), {"patch_policy": {},
                                      "build": {"recipe": "a_1.dsc"}}, Path("b"), Path("r"), {},
                                      excerpt=False, runner=None)
            budget = seen["budget"]
            self.assertEqual(None if budget is None else budget.max_attempts, cap)


if __name__ == "__main__":
    unittest.main()
