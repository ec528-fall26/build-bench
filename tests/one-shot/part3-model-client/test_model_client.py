"""Part 3 checks: one model call, robust reply parsing, plain-text prompt.

No network: a fake opener stands in for the HTTP endpoint and a fake sleep for
retry backoff. The reply shapes include the ones that broke the first Bedrock
version (prose around the JSON) and reasoning-model output.

Run from the repository root:  python3 -m unittest discover -s tests/one-shot/part3-model-client
"""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error


AGENT = Path(__file__).resolve().parents[3] / "buildbench-starter-kit-0.1.0-rc.2/agents/one-shot"
sys.path.insert(0, str(AGENT))

from src import model_client as mc  # noqa: E402
from src.types import CaseContext  # noqa: E402


KEY = "test-key-123"
FIX = {"edits": [{"path": "input/src/crc32c/crc32c_config.h",
                  "old_text": "#define HAVE_MM_PREFETCH 1\n",
                  "new_text": "#if defined(__x86_64__)\n#define HAVE_MM_PREFETCH 1\n"
                              "#else\n#define HAVE_MM_PREFETCH 0\n#endif\n"}],
       "rationale": "Guard the x86-only prefetch header."}
FIX_JSON = json.dumps(FIX, indent=2)


def completion(content, finish="stop", usage=None, model="openai.gpt-oss-120b-1:0"):
    return {"model": model,
            "choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300} if usage is None else usage}


def http_error(code, body=b"error body"):
    return urllib.error.HTTPError("https://example/chat/completions", code, "error", {}, io.BytesIO(body))


class FakeOpener:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worktree = self.root / "work"
        (self.worktree / "input").mkdir(parents=True)
        self.sleeps = []

    def write(self, rel, data):
        path = self.worktree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def context(self, tail="fatal error: xmmintrin.h: No such file or directory\n", arch="aarch64"):
        return CaseContext("case-1", self.worktree, tail, {"target_arch": arch})

    def client(self, *outcomes, key=KEY):
        opener = FakeOpener(*outcomes)
        return mc.ModelClient(key, "https://example/v1", opener=opener,
                              sleep=self.sleeps.append), opener

    def propose(self, *outcomes, **kwargs):
        client, opener = self.client(*outcomes, **kwargs)
        return client.propose(self.context()), opener


class ReplyParsing(Case):
    """Every shape a chat model commonly returns must yield the edit."""

    SHAPES = {
        "bare JSON": FIX_JSON,
        "fenced JSON": f"```json\n{FIX_JSON}\n```",
        "sentence, then fenced JSON": f"Here is the repair:\n\n```json\n{FIX_JSON}\n```",
        "fenced JSON, then explanation": f"```json\n{FIX_JSON}\n```\n\nThis guards the include.",
        "bare JSON, then explanation": f"{FIX_JSON}\n\nThe header is x86-only.",
        "fence tag with trailing space": f"```json \n{FIX_JSON}\n```",
        "reasoning block, then answer": f"<reasoning>The log shows {{xmmintrin}}; maybe edit "
                                        f"{{\"edits\": []}} first.</reasoning>\n{FIX_JSON}",
        "draft object, then final object": f'Draft: {{"edits": [], "rationale": "unsure"}}\n'
                                           f"Final:\n{FIX_JSON}",
    }

    def test_all_reply_shapes(self):
        for label, content in self.SHAPES.items():
            with self.subTest(label):
                plan, _ = self.propose(completion(content))
                self.assertEqual(len(plan.edits), 1, plan.rationale)
                self.assertEqual(plan.edits[0].old_text, FIX["edits"][0]["old_text"])
                self.assertEqual(plan.rationale, FIX["rationale"])

    def test_no_json_gives_an_empty_plan(self):
        plan, _ = self.propose(completion("I could not find a fix."))
        self.assertEqual(plan.edits, [])
        self.assertIn("Malformed model reply", plan.rationale)

    def test_wrong_shape_gives_an_empty_plan(self):
        plan, _ = self.propose(completion(json.dumps({"edits": "oops"})))
        self.assertEqual(plan.edits, [])
        self.assertIn("'edits' must be a list", plan.rationale)

    def test_cut_off_reply_gives_an_empty_plan(self):
        plan, _ = self.propose(completion(FIX_JSON[:80], finish="length"))
        self.assertEqual(plan.edits, [])
        self.assertIn("cut off", plan.rationale)

    def test_good_and_bad_edits_are_both_returned(self):
        # Part 4 judges edits one by one; Part 3 must not drop the good one.
        bad = {"path": "input/missing.c", "old_text": "not there", "new_text": "x"}
        reply = json.dumps({"edits": FIX["edits"] + [bad], "rationale": "two edits"})
        plan, _ = self.propose(completion(reply))
        self.assertEqual([e.path for e in plan.edits], [FIX["edits"][0]["path"], "input/missing.c"])

    def test_token_usage_is_recorded_and_missing_usage_is_unknown(self):
        plan, _ = self.propose(completion(FIX_JSON))
        self.assertEqual(plan.usage, {"input_tokens": 1200, "output_tokens": 300})
        plan, _ = self.propose(completion(FIX_JSON, usage={}))
        self.assertEqual(plan.usage, {"input_tokens": None, "output_tokens": None})


class ServedModel(Case):
    """The server's own report of which model answered, recorded per call."""

    def test_recorded_from_the_reply(self):
        client, _ = self.client(completion(FIX_JSON))
        client.propose(self.context())
        self.assertEqual(client.served_model, "openai.gpt-oss-120b-1:0")

    def test_reset_when_a_later_call_fails(self):
        client, _ = self.client(completion(FIX_JSON), http_error(400))
        client.propose(self.context())
        client.propose(self.context())
        self.assertIsNone(client.served_model)

    def test_absent_without_a_key_or_a_model_field(self):
        client, _ = self.client(key=None)
        client.propose(self.context())
        self.assertIsNone(client.served_model)
        reply = completion(FIX_JSON)
        del reply["model"]
        client, _ = self.client(reply)
        client.propose(self.context())
        self.assertIsNone(client.served_model)


class Request(Case):
    def test_one_request_in_openai_compatible_format(self):
        _, opener = self.propose(completion(FIX_JSON))
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://example/v1/chat/completions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "openai.gpt-oss-120b-1:0")
        self.assertEqual(body["max_completion_tokens"], mc.MAX_COMPLETION_TOKENS)
        self.assertEqual(body["temperature"], 0)
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])

    def test_single_model_constant(self):
        self.assertEqual(mc.MODEL_ID, "openai.gpt-oss-120b-1:0")
        self.assertEqual(mc.ModelClient(KEY).name, "openai-compatible:openai.gpt-oss-120b-1:0")

    def test_throttling_is_retried(self):
        plan, opener = self.propose(http_error(429), completion(FIX_JSON))
        self.assertEqual(len(plan.edits), 1)
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(self.sleeps, [2])

    def test_dropped_connection_is_retried(self):
        plan, opener = self.propose(ConnectionResetError("reset"), completion(FIX_JSON))
        self.assertEqual(len(plan.edits), 1)
        self.assertEqual(len(opener.requests), 2)

    def test_retries_stop_after_three_attempts(self):
        plan, opener = self.propose(http_error(503), http_error(503), http_error(503))
        self.assertEqual(plan.edits, [])
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(self.sleeps, [2, 4])
        self.assertIn("HTTP 503", plan.rationale)

    def test_client_error_is_not_retried_and_keeps_its_message(self):
        body = f'{{"message": "The provided model identifier is invalid. key={KEY}"}}'.encode()
        plan, opener = self.propose(http_error(400, body))
        self.assertEqual(len(opener.requests), 1)
        self.assertIn("HTTP 400", plan.rationale)
        self.assertIn("model identifier is invalid", plan.rationale)
        self.assertNotIn(KEY, plan.rationale)

    def test_no_network_gives_an_empty_plan(self):
        err = urllib.error.URLError("Name or service not known")
        plan, opener = self.propose(err, err, err)
        self.assertEqual(plan.edits, [])
        self.assertIn("Name or service not known", plan.rationale)

    def test_missing_key_sends_nothing(self):
        plan, opener = self.propose(completion(FIX_JSON), key=None)
        self.assertEqual(opener.requests, [])
        self.assertIn("BB_MODEL_API_KEY", plan.rationale)

    def test_configuration_comes_from_the_environment(self):
        with patch.dict(os.environ, {"BB_MODEL_API_KEY": KEY, "BB_MODEL_BASE_URL": "https://other/v1"}):
            client = mc.ModelClient.from_env()
        self.assertEqual((client.api_key, client.base_url), (KEY, "https://other/v1"))
        with patch.dict(os.environ, {}, clear=True):
            client = mc.ModelClient.from_env()
        self.assertEqual((client.api_key, client.base_url), (None, mc.DEFAULT_BASE_URL))


class Prompt(Case):
    def user_message(self, **context):
        return mc.build_messages(self.context(**context))[1]["content"]

    def test_fixed_file_list(self):
        self.assertEqual(mc.SOURCE_FILES, (
            "input/debian/rules", "input/debian/control", "input/debian/patches/series",
            "input/CMakeLists.txt", "input/configure.ac", "input/Makefile",
            "input/setup.py", "input/Cargo.toml", "input/meson.build"))

    def test_files_are_plain_text_with_real_tabs(self):
        self.write("input/debian/rules", "%:\n\tdh $@\n")
        text = self.user_message()
        self.assertIn("=== input/debian/rules (10 bytes) ===\n%:\n\tdh $@\n", text)
        self.assertNotIn("\\t", text)

    def test_missing_files_are_labelled_unavailable(self):
        text = self.user_message()
        for path in mc.SOURCE_FILES:
            self.assertIn(f"=== {path}: unavailable (missing) ===", text)

    def test_large_file_is_cut_at_a_line_boundary(self):
        line = "x" * 99 + "\n"
        self.write("input/configure.ac", line * 100)  # 10,000 bytes
        text = self.user_message()
        kept = (mc.FILE_LIMIT // 100) * 100
        self.assertIn(f"=== input/configure.ac (truncated: first {kept:,} of 10,000 bytes) ===\n", text)
        block = text.split("=== input/configure.ac")[1].split("===\n", 1)[1].split("=== ")[0]
        self.assertTrue(block.endswith("x\n"))
        self.assertEqual(len(block), kept)

    def test_unreadable_files_are_unavailable(self):
        self.write("input/Makefile", b"\xff\xfe binary")
        outside = self.root / "secret.txt"
        outside.write_text("secret\n")
        (self.worktree / "input" / "debian").mkdir()
        os.symlink(outside, self.worktree / "input" / "debian" / "rules")
        (self.worktree / "input" / "setup.py").mkdir()
        text = self.user_message()
        self.assertIn("=== input/Makefile: unavailable (not UTF-8 text) ===", text)
        self.assertIn("=== input/debian/rules: unavailable (symlink) ===", text)
        self.assertIn("=== input/setup.py: unavailable (not a regular file) ===", text)
        self.assertNotIn("secret", text)

    def test_total_file_budget(self):
        block = ("y" * 99 + "\n") * 75  # 7,500 bytes each
        for path in mc.SOURCE_FILES[:6]:
            self.write(path, block)
        text = self.user_message()
        for path in mc.SOURCE_FILES[:4]:
            self.assertIn(f"=== {path} (7,500 bytes) ===", text)
        self.assertIn("=== input/configure.ac (truncated: first 2,700 of 7,500 bytes) ===", text)
        self.assertIn("=== input/Makefile: unavailable (prompt budget reached) ===", text)

    def test_log_and_case_details_are_included(self):
        text = self.user_message(tail="line one\nfatal error: xmmintrin.h\n", arch="aarch64")
        self.assertIn("Case: case-1\nTarget architecture: aarch64\n", text)
        self.assertIn("=== Build log: last lines ===\nline one\nfatal error: xmmintrin.h\n"
                      "=== End of build log ===", text)

    def test_unknown_architecture(self):
        self.assertIn("Target architecture: unknown", self.user_message(arch=None))


if __name__ == "__main__":
    unittest.main()
