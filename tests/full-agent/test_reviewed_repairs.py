"""Offline end-to-end repair review and explicit replacement operations."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

AGENT = Path(__file__).resolve().parents[2] / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
sys.path.insert(0, str(AGENT))
from src.types import CaseContext, Edit
from src.contracts import AgentConfig, Budget, BuildResult, Diagnostic, ModelTurn, ToolRequest
from src.candidates import CandidateSnapshot
from src.edit_applier import apply_edits
from src.edit_preflight import validate_batch
from src.loop import run_loop
from src.model_adapter import ModelAdapter
from src.proposal_review import render_review
from src.reply import parse_turn


def turn(action, edits=None, reason="reviewed", tools=None):
    return ModelTurn(action, tools or [], "consistent source fix", [], edits or [], reason)


def diagnostic(message, path="input/b.c"):
    return Diagnostic(message, "test", path, 1, [path], None, 0, "expected portable type")


class ReviewedRepairs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "input").mkdir()
        self.a = self.root / "input/a.c"
        self.b = self.root / "input/b.c"
        self.a.write_text("oldtype oldtype\n")
        self.b.write_text("oldtype\n")
        self.context = CaseContext("test", self.root, "", {})
        self.edit = Edit("input/a.c", "oldtype", "newtype", True)

    def snapshot(self, edits):
        return CandidateSnapshot.capture(self.root, [e.path for e in edits])

    def loop(self, actions, builder=None, **kwargs):
        self.requests = []
        iterator = iter(actions)
        def next_turn(request):
            self.requests.append(request)
            value = next(iterator)
            if callable(value):
                value = value(request)
            return value, {"input_tokens": 2, "output_tokens": 1}
        return run_loop(self.context, next_turn=next_turn, review_edits=True,
                        build_feedback=builder, **kwargs)

    def test_all_is_explicit_and_counts_are_measured(self):
        self.assertIn("old_text_ambiguous", validate_batch(self.snapshot([self.edit]),
                      [Edit("input/a.c", "oldtype", "newtype")]))
        self.assertEqual(validate_batch(self.snapshot([self.edit]), [self.edit]), "")
        text, counts = render_review(self.snapshot([self.edit]), turn("edit", [self.edit]), [])
        self.assertEqual(counts[0]["matches"], 2)
        self.assertIn("input/b.c:1", text)
        self.assertIn("-oldtype oldtype", text)
        self.assertEqual(self.a.read_text(), "oldtype oldtype\n")
        self.assertEqual(apply_edits(self.root, [self.edit]), 1)
        self.assertEqual(self.a.read_text(), "newtype newtype\n")

    def test_left_to_right_nonoverlap_and_sequential_edits(self):
        self.a.write_text("aaa\n")
        edits = [Edit("input/a.c", "aa", "b", True), Edit("input/a.c", "ba", "done")]
        self.assertEqual(validate_batch(self.snapshot(edits), edits), "")
        self.assertEqual(apply_edits(self.root, edits), 2)
        self.assertEqual(self.a.read_text(), "done\n")

    def test_parser_rejects_nonboolean_mode(self):
        for mode in ("true", 1, None):
            reply = json.dumps({"action": "edit", "hypothesis": "test", "edits": [
                {"path": "input/a.c", "old_text": "x", "new_text": "y", "replace_all": mode}]})
            self.assertEqual(parse_turn(reply).action, "malformed")

    def test_parser_keeps_explicit_mode(self):
        reply = json.dumps({"action": "edit", "hypothesis": "test", "edits": [
            {"path": "input/a.c", "old_text": "x", "new_text": "y", "replace_all": True}]})
        self.assertTrue(parse_turn(reply).edits[0].replace_all)

    def test_approval_only_in_review_and_no_hidden_edits(self):
        reply = '{"action":"approve","reason":"consistent"}'
        self.assertEqual(parse_turn(reply).action, "malformed")
        self.assertEqual(parse_turn(reply, allow_review=True).action, "approve")
        self.assertEqual(parse_turn('{"action":"approve","reason":"x","edits":[{}]}',
                                   allow_review=True).action, "malformed")

    def test_missing_binary_and_policy_checks_preserved(self):
        for edit, reason in [
            (Edit("input/a.c", "missing", "new", True), "old_text_not_found"),
            (Edit("input/a.c", "oldtype", "\0", True), "binary_replacement"),
        ]:
            self.assertIn(reason, validate_batch(self.snapshot([edit]), [edit]))
        rules = self.root / "input/debian/rules"
        rules.parent.mkdir()
        rules.write_text("override_dh_auto_test:\n\tdh_auto_test\n")
        edit = Edit("input/debian/rules", "dh_auto_test\n", "true\n", True)
        self.assertIn("test_invocation_removed", validate_batch(self.snapshot([edit]), [edit]))

    def test_coverage_does_not_read_symlinks_or_forbidden_files(self):
        (self.root / "input/link.c").symlink_to(self.b)
        policy = {"allowed_paths": ["input/**"], "forbidden_paths": ["input/b.c"]}
        text, _ = render_review(self.snapshot([self.edit]), turn("edit", [self.edit]), [], patch_policy=policy)
        self.assertNotIn("input/link.c:1", text)
        self.assertNotIn("input/b.c:1", text)

    def test_no_write_or_build_before_approval(self):
        def stop(request):
            self.assertIn("SIMULATED proposed diff", request.proposal_review)
            self.assertEqual(self.a.read_text(), "oldtype oldtype\n")
            return turn("stop")
        builder = NS(request_build=lambda root: self.fail("must not build"))
        result = self.loop([turn("edit", [self.edit]), stop], builder)
        self.assertEqual(result.attempts, [])
        self.assertEqual(self.a.read_text(), "oldtype oldtype\n")

    def test_revision_replaces_whole_batch(self):
        revised = Edit("input/b.c", "oldtype", "newtype", True)
        result = self.loop([turn("edit", [self.edit]), turn("edit", [revised]),
                            turn("approve"), turn("stop")])
        self.assertEqual(self.a.read_text(), "oldtype oldtype\n")
        self.assertEqual(self.b.read_text(), "newtype\n")
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual([e["event"] for e in result.review_events],
                         ["proposed", "revised", "proposed", "approved"])

    def test_failure_review_revision_then_success(self):
        failed = BuildResult(False, "test", [diagnostic("remaining error")], 1)
        success = BuildResult(True, "packaging", [], 1)
        builds = []
        def build(root):
            builds.append(self.a.read_text())
            return failed if len(builds) == 1 else success
        def next_edit(request):
            self.assertEqual(request.current_build, failed)
            return turn("edit", [Edit("input/b.c", "oldtype", "newtype", True)])
        def approve_second(request):
            self.assertIn("remaining error", request.proposal_review)
            self.assertIn("expected portable type", request.proposal_review)
            self.assertNotIn("initial error", request.proposal_review)
            return turn("approve")
        result = self.loop([turn("edit", [self.edit]), turn("approve"), next_edit,
                            approve_second], NS(request_build=build),
                           initial_diagnostics=[diagnostic("initial error")])
        self.assertEqual(len(builds), 2)
        self.assertTrue(result.current_build.succeeded)
        self.assertEqual([a.applied for a in result.attempts], [1, 1])
        self.assertEqual(result.usage, {"input_tokens": 8, "output_tokens": 4})

    def test_rollback_review_uses_retained_source_and_errors(self):
        retained = BuildResult(False, "test", [diagnostic("retained error")], 1)
        worse = BuildResult(False, "configure", [diagnostic("discarded error")], 1)
        results = iter([retained, worse])
        def stop(request):
            self.assertIn("retained error", request.proposal_review)
            self.assertNotIn("discarded error", request.proposal_review)
            self.assertEqual(self.a.read_text(), "newtype newtype\n")
            return turn("stop")
        self.loop([
            turn("edit", [self.edit]), turn("approve"),
            turn("edit", [Edit("input/a.c", "newtype", "worse", True)]), turn("approve"),
            turn("edit", [Edit("input/b.c", "oldtype", "newtype", True)]), stop,
        ], NS(request_build=lambda root: next(results)))

    def test_changed_source_requires_new_review(self):
        def approve_after_external_change(request):
            self.a.write_text("oldtype oldtype oldtype\n")
            return turn("approve")
        def stop(request):
            self.assertIn('"matches": 3', request.proposal_review)
            self.assertEqual(self.a.read_text(), "oldtype oldtype oldtype\n")
            return turn("stop")
        result = self.loop([turn("edit", [self.edit]), approve_after_external_change, stop])
        self.assertEqual(result.attempts, [])

    def test_partial_application_restores_and_does_not_build(self):
        def partial(root, edits, **kwargs):
            self.a.write_text("partial\n")
            return 0
        with patch("src.loop.apply_edits", side_effect=partial):
            result = self.loop([turn("edit", [self.edit]), turn("approve")],
                NS(request_build=lambda root: self.fail("must not build")))
        self.assertEqual(result.termination_reason, "edit_application_error")
        self.assertEqual(self.a.read_text(), "oldtype oldtype\n")

    def test_budget_exit_during_review_leaves_source_unchanged(self):
        result = self.loop([turn("edit", [self.edit])], max_turns=1)
        self.assertEqual(result.termination_reason, "turn_budget")
        self.assertEqual(self.a.read_text(), "oldtype oldtype\n")

    def test_tools_during_review_keep_pending_proposal(self):
        def approve(request):
            self.assertTrue(request.proposal_review)
            self.assertTrue(request.tool_results)
            return turn("approve")
        self.loop([turn("edit", [self.edit]),
                   turn("tool", tools=[ToolRequest("read_file", {
                       "path": "input/b.c", "start_line": 1, "end_line": 1})]),
                   approve, turn("stop")])
        self.assertEqual(self.a.read_text(), "newtype newtype\n")

    def test_model_adapter_receives_diff_and_accepts_approval(self):
        sent = []
        def complete(messages):
            sent.append(messages)
            if len(sent) == 1:
                return json.dumps({"action": "edit", "hypothesis": "fix", "edits": [
                    {"path": "input/a.c", "old_text": "oldtype", "new_text": "newtype", "replace_all": True}
                ]}), "stop", {"input_tokens": 3, "output_tokens": 2}
            self.assertIn("SIMULATED proposed diff", str(messages))
            self.assertIn("initial error", str(messages))
            return '{"action":"approve","reason":"consistent repair"}', "stop", {"input_tokens": 3, "output_tokens": 2}
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "shared-cause", "BB_REVIEW_EDITS": "1"}):
            from src.loop import LoopRequest
            adapter = ModelAdapter(complete, self.context, config=AgentConfig(), budget=Budget(),
                diagnostics=[diagnostic("initial error")], log_path=None)
            self.addCleanup(adapter.close)
            result = run_loop(self.context, next_turn=adapter, review_edits=True,
                initial_diagnostics=[diagnostic("initial error")],
                build_feedback=NS(request_build=lambda root: BuildResult(True, "packaging", [], 1)))
        self.assertTrue(result.current_build.succeeded)
        self.assertEqual(adapter.turns[-1]["action"], "approve")
        self.assertTrue(adapter.turns[-1]["proposal_review"])


    def test_invalid_batch_correction_then_review_preserves_replace_all(self):
        edits = [self.edit, Edit("input/b.c", "missing", "newtype", True)]
        def correct(request):
            self.assertIn("Focused edit correction", request.edit_validation_feedback)
            self.assertFalse(request.proposal_review)
            self.assertEqual(self.a.read_text(), "oldtype oldtype\n")
            return turn("edit", [Edit("input/b.c", "oldtype", "newtype", True)])
        def approve(request):
            self.assertIn('"matches": 2', request.proposal_review)
            self.assertIn('"matches": 1', request.proposal_review)
            return turn("approve")
        result = self.loop([turn("edit", edits), correct, approve, turn("stop")])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].applied, 2)
        self.assertEqual(self.b.read_text(), "newtype\n")

    def test_main_enables_review_and_records_evidence(self):
        from src.main import run_agent, result_record
        log = self.root / "initial.log"
        log.write_text("src/a.c:1: error: wrong type\n")
        responses = iter([
            {"action": "edit", "hypothesis": "fix", "edits": [
                {"path": "input/a.c", "old_text": "oldtype", "new_text": "newtype", "replace_all": True}]},
            {"action": "approve", "reason": "checked actual errors and scope"},
        ])
        calls = []
        def complete(messages):
            calls.append(messages)
            return json.dumps(next(responses)), "stop", {"input_tokens": 2, "output_tokens": 1}
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "shared-cause", "BB_REVIEW_EDITS": "1"}):
            result, adapter = run_agent(self.context, log, complete,
                build_feedback=NS(request_build=lambda root: BuildResult(True, "packaging", [], 1)))
        record = result_record(result, adapter, AgentConfig())
        self.assertTrue(result.current_build.succeeded)
        self.assertIn("wrong type", str(calls[1]))
        self.assertEqual([e["event"] for e in record["review_events"]], ["proposed", "approved"])

    def test_main_rejects_mistyped_review_setting(self):
        from src.main import run_agent
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "default", "BB_REVIEW_EDITS": "yes"}):
            with self.assertRaisesRegex(ValueError, "BB_REVIEW_EDITS"):
                run_agent(self.context, self.root / "missing.log", lambda messages: self.fail("model called"))

    def test_coverage_reports_incomplete_scan(self):
        with patch("src.proposal_review.time.monotonic", side_effect=[0, 10]):
            text, _ = render_review(self.snapshot([self.edit]), turn("edit", [self.edit]), [])
        self.assertIn("Coverage scan incomplete", text)


if __name__ == "__main__":
    unittest.main()
