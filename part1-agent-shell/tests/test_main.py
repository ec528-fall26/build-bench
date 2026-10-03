"""main(): the entry point the platform runs, wired to run_repair and Part 3.

The runner accepts only status "completed" in agent-result.json, so main() must
write it whether or not a repair was found. No network: the model is either a
recording stand-in or the real client with no API key, which sends nothing.

Run from the repository root:  python3 -m unittest discover -s part1-agent-shell/tests
"""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


AGENT = Path(__file__).resolve().parents[2] / "buildbench-starter-kit-0.1.0-rc.2/agents/one-shot"
sys.path.insert(0, str(AGENT))

from src import main as agent_main  # noqa: E402
from src.model_client import ModelClient  # noqa: E402
from src.types import Edit, EditPlan  # noqa: E402


class RecordingModel:
    name = "recording"

    def __init__(self, edits):
        self.edits = edits
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return EditPlan(self.edits, "guard the include", {"input_tokens": 10, "output_tokens": 5})


class MainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        (self.workspace / "input").mkdir()
        (self.workspace / "input" / "task.json").write_text(json.dumps(
            {"schema_version": "0.1", "case_id": "case-1",
             "worktree": "/workspace/work/repo",
             "initial_build_log": "/workspace/input/initial-build.log"}))
        (self.workspace / "input" / "initial-build.log").write_text(
            "fatal error: xmmintrin.h: No such file or directory\n")
        self.source = self.workspace / "work" / "repo" / "input" / "a.c"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("#include <xmmintrin.h>\n")

    def run_main(self, env=None):
        env = {"BB_WORKSPACE": str(self.workspace), **(env or {})}
        with patch.dict(os.environ, env, clear=True), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = agent_main.main()
        result = json.loads((self.workspace / "output" / "agent-result.json").read_text())
        return code, result

    def test_repair_is_applied_and_reported(self):
        model = RecordingModel([Edit("input/a.c", "#include <xmmintrin.h>\n",
                                     "#if defined(__x86_64__)\n#include <xmmintrin.h>\n#endif\n")])
        with patch.object(ModelClient, "from_env", return_value=model):
            code, result = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["edits_proposed"], result["edits_applied"]), (1, 1))
        self.assertEqual(result["model"], "recording")
        self.assertIn("#if defined(__x86_64__)", self.source.read_text())
        context = model.contexts[0]
        self.assertEqual(context.case_id, "case-1")
        self.assertIn("xmmintrin.h", context.log_tail)

    def test_no_api_key_still_completes_without_a_repair(self):
        code, result = self.run_main()  # environment cleared: BB_MODEL_API_KEY unset
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["edits_proposed"], result["edits_applied"]), (0, 0))
        self.assertIn("BB_MODEL_API_KEY", result["message"])
        self.assertEqual(self.source.read_text(), "#include <xmmintrin.h>\n")

    def test_missing_input_is_an_agent_error(self):
        (self.workspace / "input" / "initial-build.log").unlink()
        with patch.dict(os.environ, {"BB_WORKSPACE": str(self.workspace)}, clear=True), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = agent_main.main()
        self.assertEqual(code, 1)
        error = json.loads((self.workspace / "output" / "agent-error.json").read_text())
        self.assertEqual(error["status"], "agent_error")


if __name__ == "__main__":
    unittest.main()
