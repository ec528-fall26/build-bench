"""Tests for full-agent repair orchestration."""

from pathlib import Path
from unittest.mock import Mock, patch
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
sys.path.insert(0, str(AGENT))

from src.contracts import (
    AgentConfig,
    ModelTurn,
    ToolRequest,
    BuildResult,
    Diagnostic,
    Budget,
    ToolResult,
)
from src.loop import run_loop
from src.types import CaseContext, Edit
from src.edit_applier import apply_edits


class RepairLoop(unittest.TestCase):
    def test_stop_preserves_worktree_and_records_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            source_dir = worktree / "input"
            source_dir.mkdir(parents=True)

            source = source_dir / "example.c"
            original = b"int count = 1;\n"
            source.write_bytes(original)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example build failure.\n",
                task_metadata={"target_arch": "aarch64"},
            )

            calls = []

            def next_turn(request):
                calls.append(request)
                turn = ModelTurn(
                    action="stop",
                    tool_requests=[],
                    hypothesis="",
                    evidence=[],
                    edits=[],
                    reason="No supported repair identified.",
                )
                usage = {"input_tokens": 20, "output_tokens": 10}
                return turn, usage

            result = run_loop(context, next_turn=next_turn)


            self.assertEqual(len(calls), 1)
            self.assertEqual(result.termination_reason, "model_stop")
            self.assertEqual(
                result.message, "No supported repair identified."
            )
            self.assertEqual(result.attempts, [])
            self.assertEqual(
                result.usage,
                {"input_tokens": 20, "output_tokens": 10},
            )
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(
                sorted(p.relative_to(worktree).as_posix()
                       for p in worktree.rglob("*") if p.is_file()),
                ["input/example.c"],
            )
    def test_tool_result_reaches_next_model_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            source_dir = worktree / "input"
            source_dir.mkdir(parents=True)

            source = source_dir / "example.c"
            original = b"int count = 1;\n"
            source.write_bytes(original)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example build failure.\n",
                task_metadata={"target_arch": "aarch64"},
            )

            call_count = 0

            def next_turn(request):
                nonlocal call_count
                call_count += 1

                if call_count == 1:
                    return (
                        ModelTurn(
                            action="tool",
                            tool_requests=[
                                ToolRequest(
                                    tool="read_file",
                                    args={
                                        "path": "input/example.c",
                                        "start_line": 1,
                                        "end_line": 1,
                                    },
                                )
                            ],
                            hypothesis="",
                            evidence=[],
                            edits=[],
                            reason="Inspect the source.",
                        ),
                        {"input_tokens": 20, "output_tokens": 10},
                    )

                self.assertEqual(call_count, 2)
                self.assertEqual(len(request.tool_results), 1)

                tool_result = request.tool_results[0]
                self.assertEqual(tool_result.request.tool, "read_file")
                self.assertIn("int count = 1;", tool_result.text)
                self.assertFalse(tool_result.truncated)

                return (
                    ModelTurn(
                        action="stop",
                        tool_requests=[],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="Finished inspecting.",
                    ),
                    {"input_tokens": 30, "output_tokens": 5},
                )

            result = run_loop(context, next_turn=next_turn)

            self.assertEqual(call_count, 2)
            self.assertEqual(result.termination_reason, "model_stop")
            self.assertEqual(result.attempts, [])
            self.assertEqual(
                result.usage,
                {"input_tokens": 50, "output_tokens": 15},
            )
            self.assertEqual(source.read_bytes(), original)
    def test_repeated_tool_requests_stop_at_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            calls = []

            def next_turn(request):
                calls.append(request)
                return (
                    ModelTurn(
                        action="tool",
                        tool_requests=[
                            ToolRequest(
                                tool="list_dir",
                                args={"path": "input"},
                            )
                        ],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="Inspect the directory again.",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                max_turns=10,
                max_tool_calls=2,
            )

            self.assertEqual(result.termination_reason, "tool_budget")
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(calls[-1].tool_results), 2)
            self.assertEqual(
                result.usage,
                {"input_tokens": 30, "output_tokens": 15},
            )
            self.assertEqual(result.attempts, [])

    def test_malformed_reply_is_explained_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            call_count = 0
            explanation = "An edit reply must include an edits list."

            def next_turn(request):
                nonlocal call_count
                call_count += 1

                if call_count == 1:
                    return (
                        ModelTurn(
                            action="malformed",
                            tool_requests=[],
                            hypothesis="",
                            evidence=[],
                            edits=[],
                            reason=explanation,
                        ),
                        {"input_tokens": 20, "output_tokens": 10},
                    )

                self.assertEqual(call_count, 2)
                self.assertIn(explanation, request.feedback)

                return (
                    ModelTurn(
                        action="stop",
                        tool_requests=[],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="No supported repair identified.",
                    ),
                    {"input_tokens": 30, "output_tokens": 5},
                )

            result = run_loop(context, next_turn=next_turn)

            self.assertEqual(call_count, 2)
            self.assertEqual(result.termination_reason, "model_stop")
            self.assertEqual(result.malformed_count, 1)
            self.assertEqual(result.attempts, [])
            self.assertEqual(
                result.usage,
                {"input_tokens": 50, "output_tokens": 15},
            )
    def test_edit_changes_file_and_records_unvalidated_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            source_dir = worktree / "input"
            source_dir.mkdir(parents=True)

            source = source_dir / "example.c"
            source.write_text("int count = 1;\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            call_count = 0

            def next_turn(request):
                nonlocal call_count
                call_count += 1

                if call_count == 1:
                    return (
                        ModelTurn(
                            action="edit",
                            tool_requests=[],
                            hypothesis="The count should be two.",
                            evidence=["input/example.c:1"],
                            edits=[
                                Edit(
                                    path="input/example.c",
                                    old_text="int count = 1;\n",
                                    new_text="int count = 2;\n",
                                )
                            ],
                            reason="",
                        ),
                        {"input_tokens": 20, "output_tokens": 10},
                    )

                self.assertEqual(call_count, 2)
                self.assertEqual(
                    source.read_text(encoding="utf-8"),
                    "int count = 2;\n",
                )

                return (
                    ModelTurn(
                        action="stop",
                        tool_requests=[],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="Keep the candidate for validation.",
                    ),
                    {"input_tokens": 30, "output_tokens": 5},
                )

            result = run_loop(context, next_turn=next_turn)

            self.assertEqual(result.termination_reason, "model_stop")
            self.assertEqual(len(result.attempts), 1)

            attempt = result.attempts[0]
            self.assertEqual(attempt.number, 1)
            self.assertEqual(
                attempt.hypothesis, "The count should be two."
            )
            self.assertEqual(attempt.applied, 1)
            self.assertEqual(attempt.edits[0].path, "input/example.c")
            self.assertIsNone(attempt.build)
            self.assertEqual(
                attempt.tokens,
                {"input_tokens": 20, "output_tokens": 10},
            )
            self.assertEqual(
                result.usage,
                {"input_tokens": 50, "output_tokens": 15},
            )
            self.assertEqual(
                source.read_text(encoding="utf-8"),
                "int count = 2;\n",
            )
    def test_current_diff_sees_loop_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("int count = 1;\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            call_count = 0

            def next_turn(request):
                nonlocal call_count
                call_count += 1
                usage = {"input_tokens": 10, "output_tokens": 5}

                if call_count == 1:
                    return (
                        ModelTurn(
                            action="edit",
                            tool_requests=[],
                            hypothesis="The count should be two.",
                            evidence=["input/example.c:1"],
                            edits=[
                                Edit(
                                    path="input/example.c",
                                    old_text="int count = 1;\n",
                                    new_text="int count = 2;\n",
                                )
                            ],
                            reason="",
                        ),
                        usage,
                    )

                if call_count == 2:
                    return (
                        ModelTurn(
                            action="tool",
                            tool_requests=[
                                ToolRequest(tool="current_diff", args={})
                            ],
                            hypothesis="",
                            evidence=[],
                            edits=[],
                            reason="Review the change.",
                        ),
                        usage,
                    )

                self.assertEqual(call_count, 3)
                self.assertEqual(len(request.tool_results), 1)
                result = request.tool_results[0]
                self.assertEqual(result.request.tool, "current_diff")
                self.assertIn("-int count = 1;", result.text)
                self.assertIn("+int count = 2;", result.text)
                self.assertFalse(result.truncated)

                return (
                    ModelTurn(
                        action="stop",
                        tool_requests=[],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="Change reviewed.",
                    ),
                    usage,
                )

            result = run_loop(context, next_turn=next_turn)

            self.assertEqual(call_count, 3)
            self.assertEqual(result.termination_reason, "model_stop")
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.attempts[0].applied, 1)
            self.assertEqual(
                source.read_text(encoding="utf-8"),
                "int count = 2;\n",
            )
    def test_successful_build_stops_after_one_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("before\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            model_calls = []
            build_calls = []
            build_result = BuildResult(
                succeeded=True,
                stage_reached="packaging",
                diagnostics=[],
                duration_seconds=2.0,
            )

            class FakeBuildFeedback:
                def request_build(self, candidate_worktree):
                    build_calls.append(candidate_worktree)
                    # Verify the build receives the edited worktree.
                    actual = (
                        candidate_worktree / "input/example.c"
                    ).read_text(encoding="utf-8")
                    if actual != "after\n":
                        raise AssertionError("Build received unedited source.")
                    return build_result

            def next_turn(request):
                model_calls.append(request)
                self.assertEqual(len(model_calls), 1)
                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="Replace the failing source text.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit(
                                path="input/example.c",
                                old_text="before\n",
                                new_text="after\n",
                            )
                        ],
                        reason="",
                    ),
                    {"input_tokens": 20, "output_tokens": 10},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                build_feedback=FakeBuildFeedback(),
            )

            self.assertEqual(result.termination_reason, "build_succeeded")
            self.assertEqual(len(model_calls), 1)
            self.assertEqual(build_calls, [worktree])
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.attempts[0].build, build_result)
            self.assertEqual(result.attempts[0].applied, 1)
            self.assertEqual(
                source.read_text(encoding="utf-8"), "after\n"
            )
    def test_failed_build_feedback_drives_second_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("original\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Initial failure.\n",
                task_metadata={},
            )

            new_error = "error: replacement still uses an unsupported type"
            failed_build = BuildResult(
                succeeded=False,
                stage_reached="build",
                diagnostics=[
                    Diagnostic(
                        message=new_error,
                        stage="build",
                        log_path="example.c",
                        line=1,
                        resolved=["input/example.c"],
                        source_line="candidate one",
                        log_offset=0,
                    )
                ],
                duration_seconds=1.0,
            )
            successful_build = BuildResult(
                succeeded=True,
                stage_reached="packaging",
                diagnostics=[],
                duration_seconds=1.0,
            )

            build_calls = []

            class FakeBuildFeedback:
                def request_build(self, candidate_worktree):
                    contents = (
                        candidate_worktree / "input/example.c"
                    ).read_text(encoding="utf-8")
                    build_calls.append(contents)

                    if len(build_calls) == 1:
                        return failed_build
                    if len(build_calls) == 2:
                        return successful_build
                    raise AssertionError("Unexpected extra build.")

            model_calls = []

            def next_turn(request):
                model_calls.append(request)

                if len(model_calls) == 1:
                    old_text = "original\n"
                    new_text = "candidate one\n"
                    hypothesis = "Try the first replacement."
                    evidence = ["Initial failure."]
                else:
                    self.assertEqual(len(model_calls), 2)
                    self.assertIn(new_error, request.feedback)
                    old_text = "candidate one\n"
                    new_text = "candidate two\n"
                    hypothesis = "Revise the unsupported replacement."
                    evidence = [new_error]

                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis=hypothesis,
                        evidence=evidence,
                        edits=[
                            Edit(
                                path="input/example.c",
                                old_text=old_text,
                                new_text=new_text,
                            )
                        ],
                        reason="",
                    ),
                    {"input_tokens": 20, "output_tokens": 10},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                build_feedback=FakeBuildFeedback(),
            )

            self.assertEqual(result.termination_reason, "build_succeeded")
            self.assertEqual(len(model_calls), 2)
            self.assertEqual(
                build_calls, ["candidate one\n", "candidate two\n"]
            )
            self.assertEqual(
                [attempt.number for attempt in result.attempts], [1, 2]
            )
            self.assertEqual(
                [attempt.applied for attempt in result.attempts], [1, 1]
            )
            self.assertEqual(result.attempts[0].build, failed_build)
            self.assertEqual(result.attempts[1].build, successful_build)
            self.assertEqual(
                result.usage,
                {"input_tokens": 40, "output_tokens": 20},
            )
            self.assertEqual(
                source.read_text(encoding="utf-8"), "candidate two\n"
            )

    def test_malformed_replies_stop_at_turn_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            calls = []

            def next_turn(request):
                calls.append(request)
                return (
                    ModelTurn(
                        action="malformed",
                        tool_requests=[],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="Missing action field.",
                    ),
                    {"input_tokens": None, "output_tokens": 5},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                max_turns=2,
            )

            self.assertEqual(len(calls), 2)
            self.assertEqual(result.termination_reason, "turn_budget")
            self.assertEqual(result.malformed_count, 2)
            self.assertEqual(result.attempts, [])
            self.assertEqual(
                result.usage,
                {"input_tokens": None, "output_tokens": 10},
            )
    def test_history_flag_controls_model_view_not_run_record(self):
        for enabled in (True, False):
            with self.subTest(use_history=enabled):
                with tempfile.TemporaryDirectory() as tmp:
                    worktree = Path(tmp) / "repo"
                    (worktree / "input").mkdir(parents=True)
                    source = worktree / "input/example.c"
                    source.write_text("before\n", encoding="utf-8")

                    context = CaseContext(
                        case_id="test-case",
                        worktree=worktree,
                        log_tail="Example failure.\n",
                        task_metadata={},
                    )

                    calls = []

                    def next_turn(request):
                        calls.append(request)

                        if len(calls) == 1:
                            self.assertEqual(request.history, [])
                            return (
                                ModelTurn(
                                    action="edit",
                                    tool_requests=[],
                                    hypothesis="Try a replacement.",
                                    evidence=["input/example.c:1"],
                                    edits=[
                                        Edit(
                                            path="input/example.c",
                                            old_text="before\n",
                                            new_text="after\n",
                                        )
                                    ],
                                    reason="",
                                ),
                                {"input_tokens": 10, "output_tokens": 5},
                            )

                        self.assertEqual(len(calls), 2)

                        if enabled:
                            self.assertEqual(len(request.history), 1)
                            self.assertEqual(
                                request.history[0].hypothesis,
                                "Try a replacement.",
                            )
                            self.assertEqual(request.history[0].applied, 1)
                        else:
                            self.assertEqual(request.history, [])

                        return (
                            ModelTurn(
                                action="stop",
                                tool_requests=[],
                                hypothesis="",
                                evidence=[],
                                edits=[],
                                reason="Finished.",
                            ),
                            {"input_tokens": 10, "output_tokens": 5},
                        )

                    result = run_loop(
                        context,
                        next_turn=next_turn,
                        config=AgentConfig(use_history=enabled),
                    )

                    self.assertEqual(len(result.attempts), 1)
                    self.assertEqual(result.attempts[0].applied, 1)
                    self.assertEqual(
                        source.read_text(encoding="utf-8"), "after\n"
                    )
    def test_attempt_budget_stops_without_another_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("before\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            calls = []

            def next_turn(request):
                calls.append(request)
                self.assertEqual(len(calls), 1)

                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="Try a replacement.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit(
                                path="input/example.c",
                                old_text="before\n",
                                new_text="after\n",
                            )
                        ],
                        reason="",
                    ),
                    {"input_tokens": 20, "output_tokens": 10},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                budget=Budget(max_attempts=1),
            )

            self.assertEqual(result.termination_reason, "attempt_budget")
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.attempts[0].applied, 1)
            self.assertEqual(
                source.read_text(encoding="utf-8"), "after\n"
            )
    def test_disabled_build_feedback_never_calls_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("before\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            class ForbiddenBuildFeedback:
                def request_build(self, worktree):
                    raise AssertionError(
                        "Build adapter must not run when feedback is disabled."
                    )

            def next_turn(request):
                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="Try a replacement.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit(
                                path="input/example.c",
                                old_text="before\n",
                                new_text="after\n",
                            )
                        ],
                        reason="",
                    ),
                    {"input_tokens": 20, "output_tokens": 10},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                config=AgentConfig(use_build_feedback=False),
                budget=Budget(max_attempts=1),
                build_feedback=ForbiddenBuildFeedback(),
            )

            self.assertEqual(result.termination_reason, "attempt_budget")
            self.assertEqual(len(result.attempts), 1)
            self.assertIsNone(result.attempts[0].build)
            self.assertEqual(
                source.read_text(encoding="utf-8"), "after\n"
            )

    def test_build_budget_stops_after_one_failed_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("before\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            model_calls = []
            build_calls = []

            class FakeBuildFeedback:
                def request_build(self, candidate_worktree):
                    build_calls.append(candidate_worktree)
                    return BuildResult(
                        succeeded=False,
                        stage_reached="build",
                        diagnostics=[],
                        duration_seconds=1.0,
                    )

            def next_turn(request):
                model_calls.append(request)
                self.assertEqual(len(model_calls), 1)

                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="Try a replacement.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit(
                                path="input/example.c",
                                old_text="before\n",
                                new_text="after\n",
                            )
                        ],
                        reason="",
                    ),
                    {"input_tokens": 20, "output_tokens": 10},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                budget=Budget(max_builds=1),
                build_feedback=FakeBuildFeedback(),
            )

            self.assertEqual(result.termination_reason, "build_budget")
            self.assertEqual(len(model_calls), 1)
            self.assertEqual(build_calls, [worktree])
            self.assertEqual(len(result.attempts), 1)
            self.assertIsNotNone(result.attempts[0].build)
            self.assertFalse(result.attempts[0].build.succeeded)
            self.assertEqual(
                source.read_text(encoding="utf-8"), "after\n"
            )
    def test_disabled_tools_are_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            calls = []

            def next_turn(request):
                calls.append(request)

                if len(calls) == 1:
                    return (
                        ModelTurn(
                            action="tool",
                            tool_requests=[
                                ToolRequest(
                                    tool="list_dir",
                                    args={"path": "input"},
                                )
                            ],
                            hypothesis="",
                            evidence=[],
                            edits=[],
                            reason="Inspect the package.",
                        ),
                        {"input_tokens": 10, "output_tokens": 5},
                    )

                self.assertEqual(len(calls), 2)
                self.assertIn("disabled", request.feedback.lower())
                self.assertEqual(request.tool_results, [])

                return (
                    ModelTurn(
                        action="stop",
                        tool_requests=[],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="Cannot investigate further.",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                )

            with patch("src.loop.run_tool") as dispatch:
                result = run_loop(
                    context,
                    next_turn=next_turn,
                    config=AgentConfig(use_tools=False),
                )
                dispatch.assert_not_called()

            self.assertEqual(result.termination_reason, "model_stop")
            self.assertEqual(len(calls), 2)
            self.assertEqual(result.attempts, [])
            self.assertEqual(
                result.usage,
                {"input_tokens": 20, "output_tokens": 10},
            )
    def test_two_empty_edit_proposals_stop_the_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            original = b"unchanged\n"
            source.write_bytes(original)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            calls = []

            def next_turn(request):
                calls.append(request)
                self.assertLessEqual(len(calls), 2)

                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="No supported edit identified.",
                        evidence=[],
                        edits=[],
                        reason="",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                )

            with patch("src.loop.apply_edits") as apply:
                result = run_loop(context, next_turn=next_turn)
                apply.assert_not_called()

            self.assertEqual(result.termination_reason, "no_edits")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(result.attempts), 2)
            self.assertEqual(
                [attempt.applied for attempt in result.attempts],
                [0, 0],
            )
            self.assertTrue(
                all(attempt.build is None for attempt in result.attempts)
            )
            self.assertEqual(
                result.usage,
                {"input_tokens": 20, "output_tokens": 10},
            )
            self.assertEqual(source.read_bytes(), original)
    def test_token_budget_stops_before_another_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            calls = []

            def next_turn(request):
                calls.append(request)
                self.assertEqual(len(calls), 1)

                return (
                    ModelTurn(
                        action="malformed",
                        tool_requests=[],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="Missing required fields.",
                    ),
                    {"input_tokens": 20, "output_tokens": 10},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                budget=Budget(max_tokens=30),
            )

            self.assertEqual(result.termination_reason, "token_budget")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result.malformed_count, 1)
            self.assertEqual(result.attempts, [])
            self.assertEqual(
                result.usage,
                {"input_tokens": 20, "output_tokens": 10},
            )
    def test_time_reserve_stops_before_another_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            now = [0.0]
            calls = []

            def clock():
                return now[0]

            def next_turn(request):
                calls.append(request)
                self.assertEqual(len(calls), 1)

                # Simulate reaching the start of the cleanup reserve.
                now[0] = 80.0

                return (
                    ModelTurn(
                        action="malformed",
                        tool_requests=[],
                        hypothesis="",
                        evidence=[],
                        edits=[],
                        reason="Missing required fields.",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                budget=Budget(
                    max_seconds=100,
                    reserve_seconds=20,
                ),
                clock=clock,
            )

            self.assertEqual(result.termination_reason, "time_budget")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result.malformed_count, 1)
            self.assertEqual(result.attempts, [])
            self.assertEqual(
                result.usage,
                {"input_tokens": 10, "output_tokens": 5},
            )
    def test_late_model_reply_does_not_start_an_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("before\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            now = [0.0]

            def next_turn(request):
                now[0] = 80.0
                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="Try a replacement.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit(
                                path="input/example.c",
                                old_text="before\n",
                                new_text="after\n",
                            )
                        ],
                        reason="",
                    ),
                    {"input_tokens": 20, "output_tokens": 10},
                )

            with patch("src.loop.apply_edits") as apply:
                result = run_loop(
                    context,
                    next_turn=next_turn,
                    budget=Budget(
                        max_seconds=100,
                        reserve_seconds=20,
                    ),
                    clock=lambda: now[0],
                )
                apply.assert_not_called()

            self.assertEqual(result.termination_reason, "time_budget")
            self.assertEqual(result.attempts, [])
            self.assertEqual(
                result.usage,
                {"input_tokens": 20, "output_tokens": 10},
            )
            self.assertEqual(
                source.read_text(encoding="utf-8"), "before\n"
            )
    def test_worse_attempt_restores_previous_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("original\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Initial failure.\n",
                task_metadata={},
            )

            def failure(stage, message):
                return BuildResult(
                    succeeded=False,
                    stage_reached=stage,
                    diagnostics=[
                        Diagnostic(
                            message=message,
                            stage=stage,
                            log_path=None,
                            line=None,
                            resolved=[],
                            source_line=None,
                            log_offset=0,
                        )
                    ],
                    duration_seconds=1.0,
                )

            build_results = [
                failure("test", "Test assertion failed."),
                failure("build", "Compilation failed."),
            ]
            build_calls = []

            class FakeBuildFeedback:
                def request_build(self, candidate_worktree):
                    build_calls.append(
                        (candidate_worktree / "input/example.c").read_text(
                            encoding="utf-8"
                        )
                    )
                    return build_results[len(build_calls) - 1]

            model_calls = []

            def next_turn(request):
                model_calls.append(request)
                number = len(model_calls)
                self.assertLessEqual(number, 4)

                if number == 3:
                    self.assertIn("undone", request.feedback.lower())
                    return (
                        ModelTurn(
                            action="tool",
                            tool_requests=[
                                ToolRequest(
                                    tool="read_file",
                                    args={
                                        "path": "input/example.c",
                                        "start_line": 1,
                                        "end_line": 1,
                                    },
                                ),
                                ToolRequest(tool="current_diff", args={}),
                            ],
                            hypothesis="",
                            evidence=[],
                            edits=[],
                            reason="Inspect the restored candidate.",
                        ),
                        {"input_tokens": 10, "output_tokens": 5},
                    )

                if number == 4:
                    self.assertEqual(len(request.tool_results), 2)
                    contents = request.tool_results[0].text
                    diff = request.tool_results[1].text

                    self.assertIn("better candidate", contents)
                    self.assertNotIn("worse candidate", contents)
                    self.assertIn("-original", diff)
                    self.assertIn("+better candidate", diff)
                    self.assertNotIn("worse candidate", diff)

                    return (
                        ModelTurn(
                            action="stop",
                            tool_requests=[],
                            hypothesis="",
                            evidence=[],
                            edits=[],
                            reason="Retain the restored candidate.",
                        ),
                        {"input_tokens": 10, "output_tokens": 5},
                    )

                old_text, new_text = (
                    ("original\n", "better candidate\n")
                    if number == 1
                    else ("better candidate\n", "worse candidate\n")
                )

                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis=f"Candidate {number}.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit(
                                path="input/example.c",
                                old_text=old_text,
                                new_text=new_text,
                            )
                        ],
                        reason="",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                budget=Budget(max_attempts=3),
                build_feedback=FakeBuildFeedback(),
            )

            self.assertEqual(result.termination_reason, "model_stop")
            self.assertEqual(len(model_calls), 4)
            self.assertEqual(
                build_calls,
                ["better candidate\n", "worse candidate\n"],
            )
            self.assertEqual(len(result.attempts), 2)
            self.assertEqual(
                result.attempts[0].build.stage_reached, "test"
            )
            self.assertEqual(
                result.attempts[1].build.stage_reached, "build"
            )
            self.assertEqual(
                source.read_text(encoding="utf-8"),
                "better candidate\n",
            )
            self.assertEqual(result.kept_attempts, [1])
            self.assertEqual(
                result.current_build,
                result.attempts[0].build,
            )
            self.assertNotEqual(
                result.current_build,
                result.attempts[1].build,
            )

    def test_repeated_diagnostic_stops_after_restoring_better_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_text("original\n", encoding="utf-8")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Initial failure.\n",
                task_metadata={},
            )

            def diagnostic(message):
                return Diagnostic(
                    message=message,
                    stage="build",
                    log_path="example.c",
                    line=1,
                    resolved=["input/example.c"],
                    source_line=None,
                    log_offset=0,
                )

            leading = "error: unsupported type"
            build_results = [
                BuildResult(
                    succeeded=False,
                    stage_reached="build",
                    diagnostics=[diagnostic(leading)],
                    duration_seconds=1.0,
                ),
                BuildResult(
                    succeeded=False,
                    stage_reached="build",
                    diagnostics=[
                        diagnostic(leading),
                        diagnostic("error: additional failure"),
                    ],
                    duration_seconds=1.0,
                ),
            ]
            build_calls = []

            class FakeBuildFeedback:
                def request_build(self, candidate_worktree):
                    build_calls.append(candidate_worktree)
                    return build_results[len(build_calls) - 1]

            model_calls = []

            def next_turn(request):
                model_calls.append(request)
                number = len(model_calls)
                self.assertLessEqual(number, 2)

                before, after = (
                    ("original\n", "candidate one\n")
                    if number == 1
                    else ("candidate one\n", "candidate two\n")
                )

                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis=f"Try candidate {number}.",
                        evidence=["input/example.c:1"],
                        edits=[Edit("input/example.c", before, after)],
                        reason="",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                build_feedback=FakeBuildFeedback(),
            )

            self.assertEqual(
                result.termination_reason, "repeated_diagnostic"
            )
            self.assertEqual(len(model_calls), 2)
            self.assertEqual(len(build_calls), 2)
            self.assertEqual(len(result.attempts), 2)
            self.assertEqual(
                len(result.attempts[1].build.diagnostics), 2
            )
            self.assertEqual(
                source.read_text(encoding="utf-8"),
                "candidate one\n",
            )
    def test_case_policy_prevents_forbidden_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_bytes(b"before\n")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            def next_turn(request):
                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="Try a replacement.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit("input/example.c", "before\n", "after\n")
                        ],
                        reason="",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                )

            policy = {
                "allowed_paths": ["input/*"],
                "forbidden_paths": ["input/example.c"],
            }

            with patch("src.loop.apply_edits") as apply:
                result = run_loop(
                    context,
                    next_turn=next_turn,
                    budget=Budget(max_attempts=1),
                    patch_policy=policy,
                )
                apply.assert_not_called()

            self.assertEqual(result.termination_reason, "attempt_budget")
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.attempts[0].applied, 0)
            self.assertIsNone(result.attempts[0].build)
            self.assertEqual(source.read_bytes(), b"before\n")
    def test_build_adapter_error_preserves_attempt_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_bytes(b"before\n")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            class BrokenBuildFeedback:
                def request_build(self, candidate_worktree):
                    raise RuntimeError("Validation service unavailable.")

            def next_turn(request):
                return (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="Try a replacement.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit("input/example.c", "before\n", "after\n")
                        ],
                        reason="",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                )

            result = run_loop(
                context,
                next_turn=next_turn,
                build_feedback=BrokenBuildFeedback(),
            )

            self.assertEqual(result.termination_reason, "build_error")
            self.assertIn("Validation service unavailable", result.message)
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.attempts[0].applied, 1)
            self.assertIsNone(result.attempts[0].build)
            self.assertEqual(
                result.usage,
                {"input_tokens": 10, "output_tokens": 5},
            )
            self.assertEqual(source.read_bytes(), b"after\n")

    def test_model_error_preserves_previous_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_bytes(b"before\n")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            responses = [
                (
                    ModelTurn(
                        action="edit",
                        tool_requests=[],
                        hypothesis="Try a replacement.",
                        evidence=["input/example.c:1"],
                        edits=[
                            Edit("input/example.c", "before\n", "after\n")
                        ],
                        reason="",
                    ),
                    {"input_tokens": 10, "output_tokens": 5},
                ),
                RuntimeError("Model endpoint unavailable."),
            ]

            model = Mock(side_effect=responses)
            result = run_loop(context, next_turn=model)


            self.assertEqual(model.call_count, 2)
            self.assertEqual(result.termination_reason, "model_error")
            self.assertIn("Model endpoint unavailable", result.message)
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.attempts[0].applied, 1)
            self.assertEqual(
                result.attempts[0].tokens,
                {"input_tokens": 10, "output_tokens": 5},
            )
            self.assertEqual(
                result.usage,
                {"input_tokens": None, "output_tokens": None},
            )
            self.assertEqual(source.read_bytes(), b"after\n")

    def test_deadline_between_tools_prevents_second_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )

            now = [0.0]
            request = ToolRequest(
                tool="list_dir",
                args={"path": "input"},
            )

            def dispatch(tool_request, worktree, **kwargs):
                now[0] = 80.0
                return ToolResult(
                    request=tool_request,
                    text="Directory inspected.",
                    truncated=False,
                )

            model = Mock(return_value=(
                ModelTurn(
                    action="tool",
                    tool_requests=[request, request],
                    hypothesis="",
                    evidence=[],
                    edits=[],
                    reason="Inspect twice.",
                ),
                {"input_tokens": 10, "output_tokens": 5},
            ))

            with patch(
                "src.loop.run_tool", side_effect=dispatch
            ) as tools:
                result = run_loop(
                    context,
                    next_turn=model,
                    budget=Budget(
                        max_seconds=100,
                        reserve_seconds=20,
                    ),
                    clock=lambda: now[0],
                )

            self.assertEqual(tools.call_count, 1)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result.termination_reason, "time_budget")
            self.assertEqual(result.attempts, [])
    def test_deadline_after_edit_prevents_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "repo"
            (worktree / "input").mkdir(parents=True)
            source = worktree / "input/example.c"
            source.write_bytes(b"before\n")

            context = CaseContext(
                case_id="test-case",
                worktree=worktree,
                log_tail="Example failure.\n",
                task_metadata={},
            )
            now = [0.0]

            def timed_edit(*args, **kwargs):
                applied = apply_edits(*args, **kwargs)
                now[0] = 80.0
                return applied

            model = Mock(return_value=(
                ModelTurn(
                    action="edit",
                    tool_requests=[],
                    hypothesis="Try a replacement.",
                    evidence=["input/example.c:1"],
                    edits=[
                        Edit("input/example.c", "before\n", "after\n")
                    ],
                    reason="",
                ),
                {"input_tokens": 10, "output_tokens": 5},
            ))
            feedback = Mock()
            feedback.request_build.side_effect = AssertionError(
                "A build must not start after the working deadline."
            )

            with patch("src.loop.apply_edits", side_effect=timed_edit):
                result = run_loop(
                    context,
                    next_turn=model,
                    build_feedback=feedback,
                    budget=Budget(
                        max_seconds=100,
                        reserve_seconds=20,
                    ),
                    clock=lambda: now[0],
                )

            feedback.request_build.assert_not_called()
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result.termination_reason, "time_budget")
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.attempts[0].applied, 1)
            self.assertIsNone(result.attempts[0].build)
            self.assertEqual(source.read_bytes(), b"after\n")

    def test_known_token_usage_survives_missing_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree = Path(directory)
            (worktree / "input").mkdir()

            context = CaseContext(
                case_id="token-accounting",
                worktree=worktree,
                log_tail="Initial build failure.",
                task_metadata={},
            )

            malformed = ModelTurn(
                action="malformed",
                tool_requests=[],
                hypothesis="",
                evidence=[],
                edits=[],
                reason="Missing action.",
            )

            next_turn = Mock(
                side_effect=[
                    (
                        malformed,
                        {"input_tokens": 20, "output_tokens": 5},
                    ),
                    (
                        malformed,
                        {"input_tokens": None, "output_tokens": 5},
                    ),
                ]
            )

            result = run_loop(
                context,
                next_turn=next_turn,
                budget=Budget(max_tokens=30),
            )

            self.assertEqual(next_turn.call_count, 2)
            self.assertEqual(result.termination_reason, "token_budget")
            self.assertEqual(
                result.usage,
                {"input_tokens": None, "output_tokens": 10},
            )
            self.assertEqual(
                result.known_usage,
                {"input_tokens": 20, "output_tokens": 10},
            )
    def test_build_feedback_survives_tool_turn_without_history(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree = Path(directory)
            source = worktree / "input" / "example.c"
            source.parent.mkdir()
            source.write_text("original\n", encoding="utf-8")

            context = CaseContext(
                case_id="persistent-feedback",
                worktree=worktree,
                log_tail="Initial build failure.",
                task_metadata={},
            )

            diagnostic = Diagnostic(
                message="error: replacement uses an unsupported type",
                stage="build",
                log_path="example.c",
                line=1,
                resolved=["input/example.c"],
                source_line="replacement",
                log_offset=0,
            )
            failed_build = BuildResult(
                succeeded=False,
                stage_reached="build",
                diagnostics=[diagnostic],
                duration_seconds=1.0,
            )
            adapter = Mock()
            adapter.request_build.return_value = failed_build

            replies = [
                ModelTurn(
                    action="edit",
                    tool_requests=[],
                    hypothesis="Replace the unsupported expression.",
                    evidence=["Initial build failure."],
                    edits=[
                        Edit(
                            path="input/example.c",
                            old_text="original",
                            new_text="replacement",
                        )
                    ],
                    reason="",
                ),
                ModelTurn(
                    action="tool",
                    tool_requests=[
                        ToolRequest(
                            tool="read_file",
                            args={
                                "path": "input/example.c",
                                "start_line": 1,
                                "end_line": 1,
                            },
                        )
                    ],
                    hypothesis="",
                    evidence=[],
                    edits=[],
                    reason="Inspect the changed source.",
                ),
                ModelTurn(
                    action="stop",
                    tool_requests=[],
                    hypothesis="",
                    evidence=[],
                    edits=[],
                    reason="Inspection complete.",
                ),
            ]
            requests = []

            def next_turn(request):
                requests.append(request)
                self.assertLessEqual(len(requests), len(replies))
                return replies[len(requests) - 1], {
                    "input_tokens": 10,
                    "output_tokens": 5,
                }

            result = run_loop(
                context,
                next_turn=next_turn,
                config=AgentConfig(use_history=False),
                build_feedback=adapter,
            )

            self.assertEqual(result.termination_reason, "model_stop")
            self.assertEqual(len(requests), 3)
            self.assertEqual(len(result.attempts), 1)
            adapter.request_build.assert_called_once_with(worktree)

            for request in requests:
                self.assertEqual(request.history, [])

            self.assertIn(diagnostic.message, requests[1].feedback)
            self.assertIn(diagnostic.message, requests[2].feedback)
            self.assertEqual(len(requests[2].tool_results), 1)
            self.assertIn(
                "replacement",
                requests[2].tool_results[0].text,
            )

if __name__ == "__main__":
    unittest.main()
