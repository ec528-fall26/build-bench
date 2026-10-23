"""Request tracing uses fake completions: no model or network."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
))
from src.request_trace import trace_completion


class RequestTraceTests(unittest.TestCase):
    def test_disabled_returns_original_callback(self):
        def complete(messages):
            return "result"
        with patch.dict(os.environ, {"BB_MODEL_TRACE": ""}):
            self.assertIs(trace_completion(complete), complete)

    def test_records_exact_messages_and_follow_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.jsonl"
            received = []

            def complete(messages):
                received.append(deepcopy(messages))
                return ("reply", "stop", {"input_tokens": 1})

            initial = [
                {"role": "system", "content": "Instructions"},
                {"role": "user", "content": "Full source:\nα\n\tline\n"},
            ]
            follow_up = initial + [
                {"role": "assistant", "content": "Need JSON"},
                {"role": "user", "content": "Return JSON"},
            ]
            original = deepcopy(initial)
            with patch.dict(os.environ, {"BB_MODEL_TRACE": str(path)}):
                traced = trace_completion(complete)
                self.assertEqual(traced(initial)[0], "reply")
                traced(follow_up)

            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r["call"] for r in records], [1, 2])
            self.assertEqual([r["messages"] for r in records], received)
            self.assertEqual(initial, original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_trace_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.jsonl"
            path.write_text("existing evidence\n")
            called = []

            with patch.dict(os.environ, {"BB_MODEL_TRACE": str(path)}):
                traced = trace_completion(lambda messages: called.append(messages))
                with self.assertRaises(FileExistsError):
                    traced([])

            self.assertEqual(called, [])
            self.assertEqual(path.read_text(), "existing evidence\n")

    def test_records_request_even_when_completion_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.jsonl"

            def fail(messages):
                raise RuntimeError("fake failure")

            with patch.dict(os.environ, {"BB_MODEL_TRACE": str(path)}):
                traced = trace_completion(fail)
                with self.assertRaises(RuntimeError):
                    traced([{"role": "user", "content": "source"}])

            self.assertEqual(
                json.loads(path.read_text())["messages"],
                [{"role": "user", "content": "source"}],
            )

    def test_does_not_capture_environment_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.jsonl"
            with patch.dict(os.environ, {
                "BB_MODEL_TRACE": str(path),
                "BB_MODEL_API_KEY": "SECRET_TEST_SENTINEL",
            }):
                traced = trace_completion(lambda messages: None)
                traced([{"role": "user", "content": "ordinary source"}])

            self.assertNotIn("SECRET_TEST_SENTINEL", path.read_text())


if __name__ == "__main__":
    unittest.main()
