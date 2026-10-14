"""Tests for the full agent's model-response parser."""

from pathlib import Path
import sys
import unittest
import json

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
sys.path.insert(0, str(AGENT))

from src.contracts import ModelTurn
from src.model_turns import parse_model_turn


class ModelTurnParsing(unittest.TestCase):
    def test_stop_reply(self):
        turn = parse_model_turn(
            '{"action": "stop", "reason": "No further supported repair."}'
        )

        self.assertIsInstance(turn, ModelTurn)
        self.assertEqual(turn.action, "stop")
        self.assertEqual(turn.reason, "No further supported repair.")
        self.assertEqual(turn.tool_requests, [])
        self.assertEqual(turn.edits, [])

    def test_invalid_reply_becomes_malformed(self):
        turn = parse_model_turn("This is not a structured response.")

        self.assertEqual(turn.action, "malformed")
        self.assertTrue(turn.reason)
        self.assertEqual(turn.edits, [])

    def test_unknown_action_becomes_malformed(self):
        turn = parse_model_turn('{"action": "invented_action"}')

        self.assertEqual(turn.action, "malformed")
        self.assertTrue(turn.reason)

    def test_multiple_tool_requests(self):
        reply = {
            "action": "tool",
            "requests": [
                {
                    "tool": "read_file",
                    "args": {
                        "path": "input/src/example.c",
                        "start_line": 1,
                        "end_line": 40,
                    },
                },
                {
                    "tool": "search",
                    "args": {
                        "regex": "xmmintrin",
                        "path_glob": "input/src/**",
                    },
                },
            ],
            "why": "Inspect the include and its other uses.",
        }

        turn = parse_model_turn(json.dumps(reply))

        self.assertEqual(turn.action, "tool")
        self.assertEqual(len(turn.tool_requests), 2)
        self.assertEqual(turn.tool_requests[0].tool, "read_file")
        self.assertEqual(
            turn.tool_requests[0].args["path"],
            "input/src/example.c",
        )
        self.assertEqual(turn.tool_requests[1].tool, "search")
        self.assertEqual(turn.reason, reply["why"])
        self.assertEqual(turn.edits, [])

    def test_edit_reply(self):
        reply = {
            "action": "edit",
            "hypothesis": "The architecture guard is missing.",
            "evidence": ["input/src/example.c:18"],
            "edits": [
                {
                    "path": "input/src/example.c",
                    "old_text": "before\n",
                    "new_text": "after\n",
                }
            ],
        }

        turn = parse_model_turn(json.dumps(reply))

        self.assertEqual(turn.action, "edit")
        self.assertEqual(turn.hypothesis, reply["hypothesis"])
        self.assertEqual(turn.evidence, reply["evidence"])
        self.assertEqual(len(turn.edits), 1)
        self.assertEqual(turn.edits[0].path, "input/src/example.c")
        self.assertEqual(turn.edits[0].old_text, "before\n")
        self.assertEqual(turn.edits[0].new_text, "after\n")

    def test_empty_edit_list_is_valid(self):
        reply = {
            "action": "edit",
            "hypothesis": "No supported change identified.",
            "evidence": [],
            "edits": [],
        }

        turn = parse_model_turn(json.dumps(reply))

        self.assertEqual(turn.action, "edit")
        self.assertEqual(turn.edits, [])

    def test_unknown_tool_rejects_entire_turn(self):
        reply = {
            "action": "tool",
            "requests": [
                {"tool": "list_dir", "args": {"path": "input"}},
                {"tool": "invented_tool", "args": {}},
            ],
            "why": "Inspect the package.",
        }

        turn = parse_model_turn(json.dumps(reply))

        self.assertEqual(turn.action, "malformed")
        self.assertEqual(turn.tool_requests, [])

    def test_missing_edit_field_is_malformed(self):
        reply = {
            "action": "edit",
            "hypothesis": "Try a repair.",
            "evidence": [],
            "edits": [
                {"path": "input/src/example.c", "old_text": "before"}
            ],
        }

        turn = parse_model_turn(json.dumps(reply))

        self.assertEqual(turn.action, "malformed")
        self.assertEqual(turn.edits, [])

    def test_last_action_outside_reasoning_wins(self):
        reply = (
            '<reasoning>{"action":"stop","reason":"Private draft"}</reasoning>'
            '\n{"action":"stop","reason":"Earlier draft"}'
            '\nFinal answer:\n```json\n'
            '{"action":"stop","reason":"Final decision"}'
            '\n```'
        )

        turn = parse_model_turn(reply)

        self.assertEqual(turn.action, "stop")
        self.assertEqual(turn.reason, "Final decision")


if __name__ == "__main__":
    unittest.main()
