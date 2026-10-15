"""The full agent wired together: model adapter, main(), and the harness's full mode.

No network and no validator: the model is a scripted list of reply texts, and
dpkg-source and run.sh are harness/tests/test_run_case.py's FakeHost.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(ROOT))

from harness.run_case import load_agent, run_case  # noqa: E402
from harness.tests.test_run_case import DSC, FIXTURES, FakeHost  # noqa: E402
from src import main as agent_main  # noqa: E402
from src.contracts import AgentConfig, Budget, BuildResult, Diagnostic  # noqa: E402
from src.main import run_agent  # noqa: E402
from src.model_adapter import client_completion  # noqa: E402
from src.model_client import ModelClient  # noqa: E402
from src.types import CaseContext  # noqa: E402


CONFIG = "input/src/crc32c/crc32c_config.h"
ERROR = Diagnostic("fatal error: xmmintrin.h: No such file or directory", "install",
                   "crc32c/crc32c_prefetch.h", 18, ["input/src/crc32c/crc32c_prefetch.h"],
                   "#include <xmmintrin.h>", 0)
REVISE = ROOT / "harness/replays/rcran-full-revise.json"


def reply(**action):
    return "Some reasoning first. " + json.dumps(action)


def edit(old, new, hypothesis="the prefetch intrinsic is x86 only"):
    return reply(action="edit", hypothesis=hypothesis, evidence=["crc32c_prefetch.h:18"],
                 edits=[{"path": CONFIG, "old_text": old, "new_text": new}])


class Script:
    """A model that sends the given replies in order and records every request."""

    def __init__(self, *replies, usage=(100, 10)):
        self.replies, self.sent = list(replies), []
        self.usage = {"input_tokens": usage[0], "output_tokens": usage[1]}

    def __call__(self, messages):
        self.sent.append(messages)
        return self.replies.pop(0), "stop", dict(self.usage)

    def user(self, turn):
        return self.sent[turn][1]["content"]


class FakeBuild:
    """Succeeds once HAVE_MM_PREFETCH is 0, otherwise reports the rcran error."""

    def __init__(self):
        self.calls = 0

    def request_build(self, worktree):
        self.calls += 1
        ok = "HAVE_MM_PREFETCH 0" in (Path(worktree) / CONFIG).read_text()
        return BuildResult(ok, "packaging" if ok else "install", [] if ok else [ERROR], 90.0)


class Adapter(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.worktree = root / "work"
        source = self.worktree / "input/src/crc32c"
        source.mkdir(parents=True)
        shutil.copy(FIXTURES / "crc32c_config.h", source)
        (source / "crc32c_prefetch.h").write_text("#include <xmmintrin.h>\n")
        self.log = root / "initial-build.log"
        self.log.write_text("   dh_auto_install\ncrc32c/crc32c_prefetch.h:18:10: fatal error: "
                            "xmmintrin.h: No such file or directory\n   18 | #include <xmmintrin.h>\n")

    def run_with(self, script, config=None, build=None, budget=None):
        context = CaseContext("rcran", self.worktree, "", {"target_arch": "aarch64"})
        return run_agent(context, self.log, script, config=config or AgentConfig(),
                         build_feedback=build, budget=budget)

    def test_first_turn_is_system_plus_the_evidence(self):
        script = Script(reply(action="stop", reason="test"))
        result, adapter = self.run_with(script)
        self.assertEqual(result.termination_reason, "model_stop")
        system, user = script.sent[0]
        self.assertEqual((system["role"], user["role"]), ("system", "user"))
        self.assertIn("real file: input/src/crc32c/crc32c_prefetch.h", user["content"])
        self.assertIn("0 of 5 edit attempts used (5 left)", user["content"])
        self.assertEqual(user["content"].count("Decide what to do next"), 1)
        self.assertEqual(adapter.turns[0]["action"], "stop")

    def test_tool_results_reach_the_next_turn(self):
        script = Script(
            reply(action="tool", requests=[{"tool": "read_file", "args": {
                "path": CONFIG, "start_line": 14, "end_line": 16}}], why="look"),
            reply(action="stop", reason="done"))
        self.run_with(script)
        self.assertIn("=== Results of your tool requests ===", script.user(1))
        self.assertIn("#define HAVE_MM_PREFETCH 1", script.user(1))

    def test_malformed_reply_is_explained(self):
        # No JSON even after the follow-up: the next turn explains.
        script = Script("no json here", "still none", reply(action="stop", reason="done"))
        result, _ = self.run_with(script)
        self.assertEqual(result.malformed_count, 1)
        self.assertIn("Your last reply could not be used", script.user(2))

    def test_revises_after_a_failed_build(self):
        wrong = "#define HAVE_MM_PREFETCH 1  // tried\n"
        script = Script(edit("#define HAVE_MM_PREFETCH 1\n", wrong, "wrong guess"),
                        edit(wrong, "#define HAVE_MM_PREFETCH 0\n"))
        result, adapter = self.run_with(script, build=FakeBuild())
        self.assertEqual(result.termination_reason, "build_succeeded")
        second = script.user(1)
        self.assertIn("=== Attempt 1 ===", second)
        self.assertIn("real file: input/src/crc32c/crc32c_prefetch.h", second)
        self.assertIn("Earlier attempts on this case", second)  # history on
        self.assertIn("1 of 5 edit attempts used (4 left), 1 of 3 builds used (2 left)", second)

    def test_unchanged_first_error_stops_the_loop(self):
        script = Script(edit("#define HAVE_MM_PREFETCH 1\n", "#define HAVE_MM_PREFETCH 1 // a\n"),
                        edit("// a\n", "// b\n"), reply(action="stop", reason="done"))
        result, _ = self.run_with(script, build=FakeBuild())
        # The loop itself stops on a repeated leading diagnostic.
        self.assertEqual(result.termination_reason, "repeated_diagnostic")

    def test_history_off_still_shows_the_latest_result(self):
        wrong = "#define HAVE_MM_PREFETCH 1  // tried\n"
        script = Script(edit("#define HAVE_MM_PREFETCH 1\n", wrong, "wrong guess"),
                        reply(action="stop", reason="done"))
        self.run_with(script, config=AgentConfig(use_history=False), build=FakeBuild())
        second = script.user(1)
        self.assertNotIn("Earlier attempts on this case", second)
        self.assertNotIn("wrong guess", second)
        self.assertIn("=== Attempt 1 ===", second)
        self.assertIn("real file: input/src/crc32c/crc32c_prefetch.h", second)

    def test_reasoning_only_reply_gets_a_follow_up_in_the_same_turn(self):
        script = Script("<reasoning>The config header decides this. Let's view it.</reasoning>",
                        reply(action="stop", reason="done"))
        result, adapter = self.run_with(script)
        self.assertEqual((result.termination_reason, result.malformed_count), ("model_stop", 0))
        follow = script.sent[1]
        self.assertEqual([m["role"] for m in follow], ["system", "user", "assistant", "user"])
        self.assertIn("Let's view it", follow[2]["content"])
        self.assertIn("only the JSON object", follow[3]["content"])
        self.assertEqual(adapter.turns[0]["follow_up"], True)
        self.assertEqual(adapter.turns[0]["usage"], {"input_tokens": 200, "output_tokens": 20})
        self.assertEqual(len(adapter.replies), 2)

    def test_follow_up_happens_once_per_turn(self):
        script = Script("<reasoning>a</reasoning>", "<reasoning>b</reasoning>",
                        reply(action="stop", reason="done"))
        result, adapter = self.run_with(script)
        self.assertEqual(result.malformed_count, 1)
        self.assertEqual([t["follow_up"] for t in adapter.turns], [True, False])

    def test_reasoning_then_a_sentence_without_json_is_followed_up(self):
        # ectrans turn 3: "...</reasoning>Let's search."
        script = Script("<reasoning>Search for fiat.</reasoning>Let's search.",
                        reply(action="stop", reason="done"))
        result, adapter = self.run_with(script)
        self.assertEqual(result.malformed_count, 0)
        self.assertTrue(adapter.turns[0]["follow_up"])

    def test_wrong_json_is_explained_not_followed_up(self):
        script = Script(reply(action="tool", requests=[{"tool": "delete_all", "args": {}}]),
                        reply(action="stop", reason="done"))
        result, adapter = self.run_with(script)
        self.assertEqual(result.malformed_count, 1)
        self.assertFalse(adapter.turns[0]["follow_up"])
        self.assertIn("unknown tool", script.user(1))

    def test_repeated_request_is_shown_once_with_a_note(self):
        search = {"tool": "search", "args": {"regex": "fiat", "path_glob": "input/**/*.cmake"}}
        script = Script(reply(action="tool", requests=[search]),
                        reply(action="tool", requests=[search]),
                        reply(action="stop", reason="done"))
        self.run_with(script)
        third = script.user(2)
        self.assertEqual(third.count('Request 1: search'), 1)
        self.assertNotIn("Request 2:", third)
        self.assertIn("1 of your requests repeated an earlier one and is shown once", third)

    def test_an_edit_keeps_reads_of_files_it_did_not_change(self):
        read = lambda path: {"tool": "read_file", "args": {"path": path, "start_line": 1,
                                                           "end_line": 3}}
        script = Script(
            reply(action="tool", requests=[read(CONFIG), read("input/src/crc32c/crc32c_prefetch.h"),
                                           {"tool": "search", "args": {"regex": "x",
                                                                       "path_glob": "input/**"}}]),
            edit("#define HAVE_MM_PREFETCH 1\n", "#define HAVE_MM_PREFETCH 0\n"),
            reply(action="stop", reason="done"))
        self.run_with(script)
        after = script.user(2).split("=== Results of your tool requests ===")[1]
        # Only the read of the unedited file survives the edit.
        self.assertIn('Request 1: read_file {"path": "input/src/crc32c/crc32c_prefetch.h"', after)
        self.assertNotIn(f'"path": "{CONFIG}"', after)   # edited file: dropped
        self.assertNotIn("Request 2:", after)              # text search: dropped

    def test_rejected_edit_reason_is_passed_on(self):
        script = Script(edit("text that is not in the file\n", "x\n"),
                        reply(action="stop", reason="done"))
        self.run_with(script)
        self.assertIn("Rejected:", script.user(1))
        self.assertIn("old_text_not_found", script.user(1))

    def test_unknown_token_use_is_never_zero(self):
        script = Script(reply(action="tool", requests=[{"tool": "list_dir",
                                                        "args": {"path": "input"}}]),
                        reply(action="stop", reason="done"), usage=(None, None))
        self.run_with(script)
        self.assertIn("token use unknown", script.user(1))

    def test_tool_request_with_tools_off_is_malformed(self):
        script = Script(reply(action="tool", requests=[{"tool": "list_dir",
                                                        "args": {"path": "input"}}]),
                        reply(action="stop", reason="done"))
        result, _ = self.run_with(script, config=AgentConfig(use_tools=False))
        self.assertEqual(result.malformed_count, 1)
        self.assertNotIn("find_files", script.sent[0][0]["content"])

    def test_without_build_feedback_edits_are_kept_untested(self):
        script = Script(edit("#define HAVE_MM_PREFETCH 1\n", "#define HAVE_MM_PREFETCH 0\n"),
                        reply(action="stop", reason="complete"))
        result, _ = self.run_with(script)
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertIn("No build was run for this attempt", script.user(1))
        self.assertIn("HAVE_MM_PREFETCH 0", (self.worktree / CONFIG).read_text())


class Completion(unittest.TestCase):
    def client(self, opener):
        return ModelClient("test-key", opener=opener, sleep=lambda s: None)

    def test_reply_usage_and_served_model(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        body = {"model": "openai.gpt-oss-120b-1:0", "usage": {"prompt_tokens": 7,
                                                              "completion_tokens": 3},
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
        client = self.client(lambda request, timeout: Response(json.dumps(body).encode()))
        self.assertEqual(client_completion(client)([]),
                         ("hi", "stop", {"input_tokens": 7, "output_tokens": 3}))
        self.assertEqual(client.served_model, "openai.gpt-oss-120b-1:0")

    def test_failures_raise_runtime_error_for_the_loop(self):
        def broken(request, timeout):
            raise OSError("network down")
        with self.assertRaisesRegex(RuntimeError, "model call failed"):
            client_completion(self.client(broken))([])

    def test_no_key_sends_nothing(self):
        calls = []
        client = ModelClient(None, opener=lambda *a, **k: calls.append(a))
        with self.assertRaisesRegex(RuntimeError, "no model API key"):
            client_completion(client)([])
        self.assertEqual(calls, [])


class Main(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        (self.workspace / "input").mkdir()
        (self.workspace / "input/task.json").write_text(json.dumps(
            {"case_id": "rcran", "target_arch": "aarch64"}))
        (self.workspace / "input/initial-build.log").write_text(
            "crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: No such file\n")
        source = self.workspace / "work/repo/input/src/crc32c"
        source.mkdir(parents=True)
        shutil.copy(FIXTURES / "crc32c_config.h", source)

    def run_main(self, env, script=None):
        environ = {"BB_WORKSPACE": str(self.workspace), **env}
        with patch.dict(os.environ, environ, clear=False), \
                patch.object(agent_main, "client_completion", lambda client: script), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for name in [k for k in os.environ if k.startswith("BB_AGENT_")]:
                if name not in env:
                    os.environ.pop(name)
            code = agent_main.main()
        return code, self.workspace / "output"

    def test_repairs_and_records_the_run(self):
        script = Script(edit("#define HAVE_MM_PREFETCH 1\n", "#define HAVE_MM_PREFETCH 0\n"),
                        reply(action="stop", reason="complete"))
        code, out = self.run_main({"BB_MODEL_API_KEY": "test-key"}, script)
        result = json.loads((out / "agent-result.json").read_text())
        self.assertEqual((code, result["status"]), (0, "completed"))
        self.assertEqual(result["termination_reason"], "model_stop")
        self.assertEqual(result["attempts"][0]["applied"], 1)
        self.assertEqual(result["usage"], {"input_tokens": 200, "output_tokens": 20})
        self.assertTrue(all(result["config"].values()))
        self.assertIn("HAVE_MM_PREFETCH 0",
                      (self.workspace / "work/repo" / CONFIG).read_text())

    def test_no_key_completes_without_a_request(self):
        code, out = self.run_main({"BB_MODEL_API_KEY": ""}, Script())
        result = json.loads((out / "agent-result.json").read_text())
        self.assertEqual((code, result["status"]), (0, "completed"))
        self.assertNotIn("attempts", result)

    def test_mistyped_switch_is_an_agent_error(self):
        code, out = self.run_main({"BB_MODEL_API_KEY": "k", "BB_AGENT_USE_TOOLS": "maybe"},
                                  Script())
        self.assertEqual(code, 1)
        self.assertIn("BB_AGENT_USE_TOOLS", (out / "agent-error.json").read_text())


class HarnessFullMode(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.bundle = root / "bundle"
        case = self.bundle / "case"
        (case / "input").mkdir(parents=True)
        (case / "config").mkdir()
        (self.bundle / "run.sh").write_text("")
        shutil.copy(FIXTURES / "rcran-manifest.json", case / "manifest.json")
        (case / "input" / DSC).write_text("original dsc")
        self.log = root / "initial-build.log"
        self.log.write_text("crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: "
                            "No such file or directory\n")
        self.runs = root / "runs"

    def test_scripted_revise_run_is_repaired_and_recorded(self):
        with patch.dict(os.environ, {}, clear=False), redirect_stderr(io.StringIO()):
            for name in [k for k in os.environ if k.startswith("BB_AGENT_")]:
                os.environ.pop(name)
            row = run_case(self.bundle, "full-1", self.runs, REVISE, log=self.log,
                           agent_dir=AGENT, runner=FakeHost())
        self.assertEqual(row["mode"], "full")
        self.assertTrue(row["repaired"])
        self.assertEqual(row["termination_reason"], "build_succeeded")
        self.assertEqual(row["agent_termination"], "build_succeeded")
        self.assertEqual([b["kind"] for b in row["builds"]], ["build_failed", "succeeded"])
        self.assertEqual([t["action"] for t in row["turns"]], ["tool", "edit", "edit"])
        self.assertEqual(len((self.runs / "full-1/replies.jsonl").read_text().splitlines()), 3)
        self.assertEqual(row["fix_type"], "source")
        saved = json.loads((self.runs / "full-1/record.json").read_text())
        self.assertEqual(saved["attempts"][0]["build"]["succeeded"], False)

    def test_load_agent_marks_the_full_agent(self):
        agent = load_agent(AGENT)
        self.assertTrue(agent.full)
        self.assertIsNone(agent.run_repair)


if __name__ == "__main__":
    unittest.main()
