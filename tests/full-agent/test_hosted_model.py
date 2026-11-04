"""Hosted model access: the platform-injected settings from the rc.5 model-access guide.

No network: the HTTP opener is a fake that records each request.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
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
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"
sys.path.insert(0, str(AGENT))

from src import main as agent_main  # noqa: E402
from src import model_client  # noqa: E402
from src.model_adapter import client_completion  # noqa: E402
from src.model_client import ModelClient, ModelConfigError, hosted_settings  # noqa: E402

BASE = "https://models.example/v1"
ALIAS = "organizer-model"
TOKEN = "task-token-123"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def reply(content, model=ALIAS):
    return Response(json.dumps({
        "model": model, "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
    }).encode())


class Recorder:
    """A fake urlopen: records requests and answers from a list (or raises)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Settings(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.token_file = Path(temp.name) / "token"
        self.token_file.write_text(TOKEN + "\n")
        self.env = {"BB_MODEL_BASE_URL": BASE + "/", "BB_MODEL_NAME": ALIAS,
                    "BB_MODEL_TOKEN_FILE": str(self.token_file)}

    def test_not_hosted_without_injected_settings(self):
        self.assertIsNone(hosted_settings({"BB_MODEL_API_KEY": "k",
                                           "BB_MODEL_BASE_URL": BASE}))

    def test_reads_alias_and_credential_file(self):
        self.assertEqual(hosted_settings(self.env), (BASE, ALIAS, TOKEN))

    def test_unusable_settings_are_errors_without_the_credential(self):
        bad = [
            {**self.env, "BB_MODEL_BASE_URL": ""},
            {**self.env, "BB_MODEL_BASE_URL": "https://models.example/api"},
            {**self.env, "BB_MODEL_BASE_URL": "https://user:pw@models.example/v1"},
            {**self.env, "BB_MODEL_NAME": " "},
            {**self.env, "BB_MODEL_TOKEN_FILE": str(self.token_file) + ".missing"},
        ]
        for env in bad:
            with self.subTest(env=env), self.assertRaises(ModelConfigError) as caught:
                hosted_settings(env)
            self.assertNotIn(TOKEN, str(caught.exception))
        self.token_file.write_text("two words")
        with self.assertRaises(ModelConfigError):
            hosted_settings(self.env)

    def test_from_env_makes_a_hosted_client_without_retries(self):
        with patch.dict(os.environ, self.env):
            client = ModelClient.from_env()
        self.assertEqual((client.hosted, client.model, client.api_key, client.max_attempts),
                         (True, ALIAS, TOKEN, 1))
        self.assertEqual(client.name, f"openai-compatible:{ALIAS}")


class Requests(unittest.TestCase):
    def client(self, opener, hosted=True):
        if hosted:
            return ModelClient(TOKEN, BASE, opener=opener, max_attempts=1, model=ALIAS,
                               hosted=True, sleep=lambda s: None)
        return ModelClient("dev-key", opener=opener, sleep=lambda s: None)

    def test_hosted_request_sends_only_documented_fields(self):
        opener = Recorder(reply("hi"))
        client = self.client(opener)
        self.assertEqual(client_completion(client)([{"role": "user", "content": "x"}])[0], "hi")
        request, _ = opener.requests[0]
        self.assertEqual(request.full_url, BASE + "/chat/completions")
        self.assertEqual(json.loads(request.data), {
            "model": ALIAS, "messages": [{"role": "user", "content": "x"}],
            "max_completion_tokens": model_client.MAX_COMPLETION_TOKENS,
            "store": False, "n": 1})
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertEqual(client.successful_calls, 1)

    def test_development_request_is_unchanged(self):
        opener = Recorder(reply("hi", model=model_client.MODEL_ID))
        client_completion(self.client(opener, hosted=False))([])
        body = json.loads(opener.requests[0][0].data)
        self.assertEqual((body["model"], body["temperature"]), (model_client.MODEL_ID, 0))
        self.assertNotIn("store", body)

    def test_hosted_errors_are_not_retried(self):
        error = urllib.error.HTTPError(BASE, 429, "Too Many Requests", {},
                                       io.BytesIO(b"limit reached"))
        opener = Recorder(error, reply("never"))
        client = self.client(opener)
        with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            client_completion(client)([])
        self.assertEqual((len(opener.requests), client.successful_calls), (1, 0))

    def test_request_timeout_never_passes_the_deadline(self):
        opener = Recorder(reply("hi"))
        client = self.client(opener)
        client.deadline = agent_main.monotonic() + 100
        client_completion(client)([])
        self.assertLessEqual(opener.requests[0][1], 100)

    def test_no_request_when_too_little_time_is_left(self):
        opener = Recorder()
        client = self.client(opener)
        client.deadline = agent_main.monotonic() + 5
        with self.assertRaisesRegex(RuntimeError, "no time left"):
            client_completion(client)([])
        self.assertEqual(opener.requests, [])


class HostedMain(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "workspace"
        (self.workspace / "input").mkdir(parents=True)
        (self.workspace / "input/task.json").write_text(json.dumps(
            {"schema_version": "0.1", "case_id": "hello-demo"}))
        (self.workspace / "input/initial-build.log").write_text(
            "intentional Build-Bench demo failure\nerror: Bad exit status from %build\n")
        spec = self.workspace / "work/repo/input/buildbench-hello.spec"
        spec.parent.mkdir(parents=True)
        spec.write_text("%build\nexit 1\n\n%install\n")
        self.token_file = root / "token"
        self.token_file.write_text(TOKEN)
        self.env = {"BB_WORKSPACE": str(self.workspace), "BB_MODEL_BASE_URL": BASE,
                    "BB_MODEL_NAME": ALIAS, "BB_MODEL_TOKEN_FILE": str(self.token_file),
                    "BB_MODEL_API_KEY": ""}

    def run_main(self, opener, **env):
        class Opener:
            open = staticmethod(opener)
        with patch.dict(os.environ, {**self.env, **env}), \
                patch.object(model_client.urllib.request, "build_opener",
                             lambda *handlers: Opener()), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            for name in [k for k in os.environ if k.startswith("BB_AGENT_")]:
                os.environ.pop(name)
            code = agent_main.main()
        result = json.loads((self.workspace / "output/agent-result.json").read_text())
        return code, result, err.getvalue()

    def test_edit_then_stop_completes_with_the_injected_alias(self):
        edit = json.dumps({"action": "edit", "hypothesis": "the build step exits 1",
                           "evidence": ["log"], "edits": [{
                               "path": "input/buildbench-hello.spec",
                               "old_text": "exit 1\n", "new_text": ""}]})
        stop = json.dumps({"action": "stop", "reason": "done"})
        opener = Recorder(reply(edit), reply(stop))
        code, result, _ = self.run_main(opener)
        self.assertEqual((code, result["status"], result["model"]),
                         (0, "completed", f"openai-compatible:{ALIAS}"))
        self.assertNotIn("exit 1", (self.workspace / "work/repo/input/buildbench-hello.spec")
                         .read_text())
        self.assertTrue(all(timeout <= agent_main.HOSTED_SECONDS
                            for _, timeout in opener.requests))

    def test_invalid_injected_settings_exit_nonzero(self):
        code, result, err = self.run_main(Recorder(), BB_MODEL_BASE_URL="ftp://x/v1")
        self.assertEqual((code, result["status"], result["failure_stage"]),
                         (1, "no_fix", "model_initialization"))
        self.assertNotIn(TOKEN, err + json.dumps(result))

    def test_no_successful_model_call_exits_nonzero(self):
        error = urllib.error.HTTPError(BASE, 401, "Unauthorized", {}, io.BytesIO(b"bad token"))
        code, result, err = self.run_main(Recorder(error))
        self.assertEqual((code, result["status"], result["failure_stage"]),
                         (1, "no_fix", "model_repair"))
        self.assertIn("HTTP 401", result["message"])
        self.assertNotIn(TOKEN, err + json.dumps(result))


if __name__ == "__main__":
    unittest.main()


class DevelopmentOverrides(unittest.TestCase):
    """Demo 2 feedback: the region was hard-coded with no way to change it."""

    def test_region_and_model_come_from_the_environment(self):
        env = {"BB_MODEL_API_KEY": "k", "BB_MODEL_ID": "openai.gpt-oss-20b-1:0",
               "BB_MODEL_BASE_URL": "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1"}
        cleared = {name: "" for name in ("BB_MODEL_NAME", "BB_MODEL_TOKEN_FILE")}
        with patch.dict(os.environ, {**cleared, **env}):
            client = ModelClient.from_env()
        self.assertEqual((client.hosted, client.model, client.base_url),
                         (False, "openai.gpt-oss-20b-1:0", env["BB_MODEL_BASE_URL"]))

    def test_defaults_without_overrides(self):
        cleared = {name: "" for name in ("BB_MODEL_NAME", "BB_MODEL_TOKEN_FILE",
                                         "BB_MODEL_ID", "BB_MODEL_BASE_URL")}
        with patch.dict(os.environ, {**cleared, "BB_MODEL_API_KEY": "k"}):
            client = ModelClient.from_env()
        self.assertIn("us-east-1", client.base_url)
        self.assertEqual(client.model, "openai.gpt-oss-120b-1:0")
