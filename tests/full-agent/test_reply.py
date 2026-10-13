"""Part E: turning a model reply into a ModelTurn. No model or network needed.

The three well-formed examples are the ones in docs/full-agent.md, "Model reply
format". The rest pins what is tolerated and what is reported back to the model
as malformed.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
                      "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"))
from src.contracts import ModelTurn, ToolRequest  # noqa: E402
from src.reply import extract_action_json, parse_turn  # noqa: E402
from src.tools import TOOLS  # noqa: E402
from src.types import Edit  # noqa: E402


TOOL = {"action": "tool",
        "requests": [
            {"tool": "read_file", "args": {"path": "input/src/crc32c/crc32c_prefetch.h",
                                           "start_line": 1, "end_line": 40}},
            {"tool": "search", "args": {"regex": "xmmintrin", "path_glob": "input/src/**"}}],
        "why": "check how the include is guarded and where else it appears"}
EDIT = {"action": "edit",
        "hypothesis": "crc32c_prefetch.h includes the x86-only xmmintrin.h unconditionally",
        "evidence": ["log line 1995", "input/src/crc32c/crc32c_prefetch.h:18"],
        "edits": [{"path": "input/src/a.h", "old_text": "#include <x.h>\n", "new_text": ""}]}
STOP = {"action": "stop",
        "reason": "the failure needs a dependency that is not packaged for arm64"}


def reply(data, before="", after=""):
    return before + json.dumps(data) + after


class WellFormed(unittest.TestCase):
    def test_tool_turn_with_several_requests(self):
        turn = parse_turn(reply(TOOL))
        self.assertEqual(turn.action, "tool")
        self.assertEqual(turn.tool_requests, [
            ToolRequest("read_file", {"path": "input/src/crc32c/crc32c_prefetch.h",
                                      "start_line": 1, "end_line": 40}),
            ToolRequest("search", {"regex": "xmmintrin", "path_glob": "input/src/**"})])
        self.assertEqual(turn.reason, TOOL["why"])  # kept for the run record
        self.assertEqual((turn.edits, turn.hypothesis, turn.evidence), ([], "", []))

    def test_edit_turn(self):
        turn = parse_turn(reply(EDIT))
        self.assertEqual(turn, ModelTurn(
            "edit", [], EDIT["hypothesis"], EDIT["evidence"],
            [Edit("input/src/a.h", "#include <x.h>\n", "")], ""))

    def test_stop_turn(self):
        turn = parse_turn(reply(STOP))
        self.assertEqual((turn.action, turn.reason), ("stop", STOP["reason"]))

    def test_an_edit_turn_may_propose_no_edits(self):
        # Part C's stop rule counts these; they are not malformed.
        turn = parse_turn(reply({**EDIT, "edits": []}))
        self.assertEqual((turn.action, turn.edits), ("edit", []))

    def test_every_tool_name_is_accepted(self):
        for name in TOOLS:
            with self.subTest(tool=name):
                turn = parse_turn(reply({"action": "tool", "requests": [{"tool": name, "args": {}}]}))
                self.assertEqual(turn.tool_requests, [ToolRequest(name, {})])


class Tolerated(unittest.TestCase):
    def test_prose_around_the_json(self):
        turn = parse_turn(reply(STOP, before="I looked at the log.\n\n", after="\n\nThat is all."))
        self.assertEqual(turn.action, "stop")

    def test_code_fence(self):
        turn = parse_turn(reply(EDIT, before="```json\n", after="\n```"))
        self.assertEqual(turn.action, "edit")

    def test_the_last_action_object_wins_over_a_draft(self):
        turn = parse_turn(reply(TOOL, before="First idea: ") + reply(STOP, before="\nBetter: "))
        self.assertEqual(turn.action, "stop")

    def test_reasoning_blocks_are_ignored_whatever_the_tag(self):
        for tag in ("reasoning", "think", "THINKING"):
            with self.subTest(tag=tag):
                text = f"<{tag}>draft {json.dumps(STOP)} trailing</{tag}>\n" + reply(EDIT)
                self.assertEqual(parse_turn(text).action, "edit")
                only_draft = f"<{tag}>{json.dumps(STOP)}</{tag}> no answer followed"
                self.assertEqual(parse_turn(only_draft).action, "malformed")

    def test_an_action_key_inside_the_reply_is_not_the_reply(self):
        data = {"action": "tool",
                "requests": [{"tool": "search", "args": {"regex": "x", "path_glob": "input/**",
                                                         "note": {"action": "stop"}}}]}
        self.assertEqual(parse_turn(reply(data)).action, "tool")
        text = json.dumps({"action": "edit", "hypothesis": "h", "edits": [
            {"path": "input/a.json", "old_text": '{"action": "stop"}', "new_text": "{}"}]})
        self.assertEqual(parse_turn(text).action, "edit")

    def test_literal_newlines_and_tabs_inside_strings_are_kept(self):
        text = ('{"action": "edit", "hypothesis": "h", "edits": [{"path": "input/debian/rules", '
                '"old_text": "build:\n\tmake", "new_text": "build:\n\tmake all"}]}')
        with self.assertRaises(ValueError):
            json.loads(text)  # strict JSON rejects it
        edit = parse_turn(text).edits[0]
        self.assertEqual((edit.old_text, edit.new_text), ("build:\n\tmake", "build:\n\tmake all"))

    def test_single_objects_where_lists_were_asked_for(self):
        turn = parse_turn(reply({"action": "tool", "requests": TOOL["requests"][0]}))
        self.assertEqual(len(turn.tool_requests), 1)
        turn = parse_turn(reply({**EDIT, "edits": EDIT["edits"][0], "evidence": "log line 7"}))
        self.assertEqual((len(turn.edits), turn.evidence), (1, ["log line 7"]))

    def test_missing_args_and_evidence_default_to_empty(self):
        turn = parse_turn(reply({"action": "tool", "requests": [{"tool": "patch_series"}]}))
        self.assertEqual(turn.tool_requests, [ToolRequest("patch_series", {})])
        without = {k: v for k, v in EDIT.items() if k != "evidence"}
        self.assertEqual(parse_turn(reply(without)).evidence, [])

    def test_line_numbers_sent_as_text_become_integers(self):
        data = {"action": "tool", "requests": [{"tool": "read_file", "args": {
            "path": "input/a.c", "start_line": "10", "end_line": 40.0}}]}
        args = parse_turn(reply(data)).tool_requests[0].args
        self.assertEqual((args["start_line"], args["end_line"]), (10, 40))
        self.assertIs(type(args["end_line"]), int)

    def test_action_spelling_and_the_baseline_field_name(self):
        data = {"action": " Edit ", "rationale": "the flag is hard-coded", "edits": EDIT["edits"]}
        turn = parse_turn(reply(data))
        self.assertEqual((turn.action, turn.hypothesis), ("edit", "the flag is hard-coded"))

    def test_the_baseline_format_without_an_action(self):
        turn = parse_turn(reply({"edits": EDIT["edits"], "rationale": "guard the include"}))
        self.assertEqual((turn.action, turn.hypothesis, len(turn.edits)),
                         ("edit", "guard the include", 1))
        turn = parse_turn(reply({"edits": [], "rationale": "no supported repair"}))
        self.assertEqual((turn.action, turn.reason), ("stop", "no supported repair"))

    def test_an_action_object_wins_over_a_baseline_one(self):
        text = reply(STOP) + "\n" + reply({"edits": EDIT["edits"], "rationale": "r"})
        self.assertEqual(parse_turn(text).action, "stop")


class Malformed(unittest.TestCase):
    def reason(self, text, **kwargs):
        turn = parse_turn(text, **kwargs)
        self.assertEqual(turn.action, "malformed", text)
        self.assertEqual((turn.tool_requests, turn.edits), ([], []))
        self.assertTrue(turn.reason)
        return turn.reason

    def test_no_json_at_all(self):
        self.assertIn('"action"', self.reason("I think the header is missing."))
        self.assertIn("no text", self.reason("   \n"))

    def test_json_without_an_action(self):
        self.reason('{"tool": "read_file", "args": {}}')

    def test_unknown_action_and_unknown_tool(self):
        self.assertIn("tool, edit, stop", self.reason(reply({"action": "build"})))
        reason = self.reason(reply({"action": "tool", "requests": [{"tool": "run_shell", "args": {}}]}))
        self.assertIn("run_shell", reason)
        for name in TOOLS:
            self.assertIn(name, reason)

    def test_tool_turn_shape(self):
        self.reason(reply({"action": "tool"}))
        self.reason(reply({"action": "tool", "requests": []}))
        self.reason(reply({"action": "tool", "requests": ["read_file"]}))
        self.assertIn("args", self.reason(reply(
            {"action": "tool", "requests": [{"tool": "list_dir", "args": "input"}]})))

    def test_edit_turn_shape(self):
        without = {k: v for k, v in EDIT.items() if k != "edits"}
        self.assertIn("edits", self.reason(reply(without)))
        for broken in ({"path": "input/a", "new_text": "x"},
                       {"path": "input/a", "old_text": "", "new_text": "x"},
                       {"path": " ", "old_text": "a", "new_text": "x"},
                       {"path": "input/a", "old_text": "a", "new_text": None},
                       "input/a"):
            with self.subTest(edit=broken):
                self.assertIn("edit 2", self.reason(reply(
                    {**EDIT, "edits": [EDIT["edits"][0], broken]})))

    def test_an_edit_without_a_hypothesis(self):
        for hypothesis in ("", "   ", None, 7):
            with self.subTest(hypothesis=hypothesis):
                self.assertIn("hypothesis", self.reason(reply({**EDIT, "hypothesis": hypothesis})))

    def test_tool_request_when_tools_are_switched_off(self):
        self.assertIn("not available", self.reason(reply(TOOL), allow_tools=False))
        self.assertEqual(parse_turn(reply(EDIT), allow_tools=False).action, "edit")
        self.assertNotIn("tool,", self.reason(reply({"action": "x"}), allow_tools=False))

    def test_a_reply_cut_off_at_the_output_limit(self):
        cut = reply(EDIT)[:-25]
        self.assertIn("cut off", self.reason(cut, finish_reason="length"))
        self.assertNotIn("cut off", self.reason(cut, finish_reason="stop"))
        # A complete object is still used, whatever followed it.
        self.assertEqual(parse_turn(reply(STOP, after=" and then"), finish_reason="length").action,
                         "stop")

    def test_never_raises(self):
        for junk in (None, 7, b"{}", [], {"action": "stop"}, "{", "}{", '{"action": {"a": 1}}',
                     '{"action": "edit", "edits": 3}', '{"action": "stop", "reason": 5}',
                     '{"action": "tool", "requests": [{"tool": ["x"]}]}'):
            with self.subTest(reply=junk):
                self.assertIsInstance(parse_turn(junk), ModelTurn)


class Extraction(unittest.TestCase):
    def test_raises_when_nothing_matches(self):
        with self.assertRaises(ValueError):
            extract_action_json('{"other": 1} [1, 2]')

    def test_an_outer_wrapper_is_looked_inside(self):
        self.assertEqual(extract_action_json('{"reply": {"action": "stop", "reason": "r"}}'),
                         {"action": "stop", "reason": "r"})


if __name__ == "__main__":
    unittest.main()
