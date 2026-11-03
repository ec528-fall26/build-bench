"""Repair orchestration, built incrementally and tested offline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from time import monotonic

from .candidates import CandidateSnapshot
from .contracts import (
    AgentConfig,
    Attempt,
    Budget,
    BuildFeedback,
    BuildResult,
    ModelTurn,
    ToolResult,
)
from .diff_tracker import DiffTracker
from .edit_applier import apply_edits
from .edit_preflight import focused_feedback
from .path_feedback import missing_prefix_feedback
from .tools import run_tool
from .types import CaseContext
from .proposal_review import render_review, signature


@dataclass
class LoopRequest:
    context: CaseContext
    tool_results: list[ToolResult] = field(default_factory=list)
    feedback: str = ""
    history: list[Attempt] = field(default_factory=list)
    current_build: BuildResult | None = None
    kept_attempts: list[int] = field(default_factory=list)
    # The most recent attempt, given even with history off: what the last edit
    # did is current feedback, not history.
    latest_attempt: Attempt | None = None
    edit_validation_feedback: str = ""
    proposal_review: str = ""

@dataclass
class LoopResult:
    termination_reason: str
    message: str
    attempts: list[Attempt]
    usage: dict[str, int | None]
    malformed_count: int = 0
    known_usage: dict[str, int] = field(default_factory=dict)
    current_build: BuildResult | None = None
    kept_attempts: list[int] = field(default_factory=list)
    review_events: list[dict] = field(default_factory=list)


def _normalize_usage(usage: dict) -> dict[str, int | None]:
    """Keep valid reported counts; missing or invalid counts stay unknown."""
    result = {}
    for name in ("input_tokens", "output_tokens"):
        value = usage.get(name)
        result[name] = (
            value if type(value) is int and value >= 0 else None
        )
    return result


def _add_usage(total: dict, reported: dict) -> dict[str, int | None]:
    """A total stays unknown if any contributing count is unknown."""
    counts = _normalize_usage(reported)
    return {
        name: (
            total[name] + counts[name]
            if total[name] is not None and counts[name] is not None
            else None
        )
        for name in ("input_tokens", "output_tokens")
    }

_STAGE_RANK = {
    "unknown": -1,
    "configure": 0,
    "build": 1,
    "test": 2,
    "install": 3,
    "packaging": 4,
}


def _build_rank(build: BuildResult) -> tuple[int, int, int]:
    """Heuristic for candidate selection, not proof of correctness."""
    return (
        int(build.succeeded),
        _STAGE_RANK.get(build.stage_reached, -1),
        -len(build.diagnostics),
    )

# Tools whose output an edit cannot change: edits never create, delete or
# rename files, so listings and name searches stay true.
_EDIT_PROOF = {"list_dir", "find_files", "patch_series"}


def _still_valid(results: list[ToolResult], edited: set[str]) -> list[ToolResult]:
    """Tool results still true after an edit to ``edited``.

    A file read stays valid unless that file was edited. Text searches and
    the current diff may change with any edit, so they are dropped. Keeping
    the rest spares the model re-reading files it already saw (the first
    ectrans run re-read unchanged files after its edit).
    """
    kept = []
    for result in results:
        tool = result.request.tool
        if tool in _EDIT_PROOF:
            kept.append(result)
        elif tool == "read_file" and result.request.args.get("path") not in edited:
            kept.append(result)
    return kept


def run_loop(
    context: CaseContext,
    *,
    next_turn: Callable[
        [LoopRequest],
        tuple[ModelTurn, dict[str, int | None]],
    ],
    config: AgentConfig | None = None,
    budget: Budget | None = None,
    build_feedback: BuildFeedback | None = None,
    patch_policy: Mapping[str, list[str] | tuple[str, ...]] | None = None,
    review_edits: bool = False,
    initial_diagnostics=None,
    max_turns: int = 20,
    max_tool_calls: int = 30,
    clock: Callable[[], float] = monotonic,
) -> LoopResult:
    # Provisional local safeguards, not official competition limits.
    if type(review_edits) is not bool:
        raise ValueError("review_edits must be a boolean")
    if type(max_turns) is not int or max_turns < 1:
        raise ValueError("max_turns must be a positive integer.")
    if type(max_tool_calls) is not int or max_tool_calls < 0:
        raise ValueError("max_tool_calls must be a non-negative integer.")
    if config is None:
        config = AgentConfig()
    if budget is None:
        budget = Budget()
    if type(budget.max_attempts) is not int or budget.max_attempts < 0:
        raise ValueError("max_attempts must be a non-negative integer.")
    if type(budget.max_builds) is not int or budget.max_builds < 0:
        raise ValueError("max_builds must be a non-negative integer.")
    if type(budget.max_tokens) is not int or budget.max_tokens < 0:
        raise ValueError("max_tokens must be a non-negative integer.")
    if type(budget.max_seconds) is not int or budget.max_seconds <= 0:
        raise ValueError("max_seconds must be a positive integer.")
    if (
        type(budget.reserve_seconds) is not int
        or not 0 <= budget.reserve_seconds <= budget.max_seconds
    ):
        raise ValueError(
            "reserve_seconds must be between zero and max_seconds."
        )

    work_deadline = (
        clock() + budget.max_seconds - budget.reserve_seconds
    )
    # Initialize per run state
    build_calls = 0
    tracker = DiffTracker(context.worktree)
    tool_results = []
    tool_calls = 0
    usage = {"input_tokens": 0, "output_tokens": 0}
    known_usage = {"input_tokens": 0, "output_tokens": 0}
    feedback = ""
    latest_attempt_feedback = ""
    edit_validation_feedback = ""
    pending_turn = None
    pending_index = None
    review_turn = None
    review_key = None
    proposal_review = ""
    review_events = []
    correction_responses = 0
    max_correction_responses = 12
    malformed_count = 0
    empty_edit_streak = 0
    attempts: list[Attempt] = []
    best_build: BuildResult | None = None
    kept_attempts: list[int] = []


    def finish(reason: str, message: str) -> LoopResult:
        return LoopResult(
            termination_reason=reason,
            message=message,
            attempts=deepcopy(attempts),
            usage=dict(usage),
            malformed_count=malformed_count,
            known_usage=dict(known_usage),
            current_build=deepcopy(best_build),
            kept_attempts=list(kept_attempts),
            review_events=deepcopy(review_events),
        )

    for _ in range(max_turns):
        if pending_turn is not None and correction_responses >= max_correction_responses:
            return finish(
                "edit_correction_budget",
                "Twelve correction responses did not produce a valid batch; "
                "the pending batch was not applied.",
            )
        if clock() >= work_deadline:
            return finish(
                "time_budget",
                "The time reserved for graceful shutdown was reached.",
            )

        if len(attempts) >= budget.max_attempts:
            return finish(
                "attempt_budget",
                "The repair-attempt limit was reached.",
            )
        if (
            config.use_build_feedback
            and build_feedback is not None
            and build_calls >= budget.max_builds
        ):
            return finish(
                "build_budget",
                "The build-request limit was reached.",
            )
        known_tokens = sum(known_usage.values())

        if known_tokens >= budget.max_tokens:
            return finish(
                "token_budget",
                "The reported token usage reached the limit.",
            )

        request = LoopRequest(
            context=context,
            tool_results=list(tool_results),
            feedback="\n\n".join(
                message
                for message in (latest_attempt_feedback, feedback)
                if message
            ),
            history=deepcopy(attempts) if config.use_history else [],
            current_build=deepcopy(best_build),
            kept_attempts=list(kept_attempts) if config.use_history else [],
            latest_attempt=deepcopy(attempts[-1]) if attempts else None,
            edit_validation_feedback=edit_validation_feedback,
            proposal_review=proposal_review,
        )


        try:
            turn, reported_usage = next_turn(request)
        except (OSError, RuntimeError, ValueError) as exc:
            unknown_usage = {
                "input_tokens": None,
                "output_tokens": None,
            }
            usage = _add_usage(usage, unknown_usage)
            return finish(
                "model_error",
                f"Model request failed: {type(exc).__name__}: {exc}",
            )

        if pending_turn is not None:
            correction_responses += 1

        reported_usage = _normalize_usage(reported_usage)

        for name, count in reported_usage.items():
            if count is not None:
                known_usage[name] += count

        usage = _add_usage(usage, reported_usage)

        feedback = ""

        if turn.action == "malformed":
            malformed_count += 1
            feedback = (
                f"Your previous reply was malformed: {turn.reason}\n"
                "Please resend a valid JSON action object."
            )
            continue

        if turn.action == "stop":
            return finish("model_stop", turn.reason)

        if clock() >= work_deadline:
            return finish(
                "time_budget",
                "The time reserved for graceful shutdown was reached.",
            )

        if turn.action == "tool":
            if pending_turn is not None:
                edit_validation_feedback += "\nReturn one correction edit, an empty edit list to remove it, or stop."
                continue
            if not config.use_tools:
                feedback = (
                    "Investigation tools are disabled for this run. "
                    "Use the evidence already provided to propose an edit "
                    "or return a stop action."
                )
                continue

            for tool_request in turn.tool_requests:
                if clock() >= work_deadline:
                    return finish(
                        "time_budget",
                        "No time remains to start another tool.",
                    )

                if tool_calls >= max_tool_calls:
                    return finish(
                        "tool_budget",
                        "The tool-call limit was reached.",
                    )

                tool_calls += 1
                result = run_tool(
                    tool_request,
                    context.worktree,
                    diff_tracker=tracker,
                )
                tool_results.append(result)

            continue

        approved_key = None
        if turn.action == "approve":
            if review_turn is None:
                feedback = "No pending diff is available to approve. Propose an edit or stop."
                continue
            approved_key = review_key
            review_events.append({"event": "approved", "signature": review_key, "reason": turn.reason})
            turn = deepcopy(review_turn)
            review_turn = None
            proposal_review = ""
        elif turn.action == "edit" and review_turn is not None:
            review_events.append({"event": "revised", "signature": review_key})
            review_turn = None
            proposal_review = ""

        if turn.action == "edit":
            if pending_turn is not None:
                if len(turn.edits) > 1:
                    edit_validation_feedback += "\nYour correction contained multiple edits. Return at most ONE."
                    continue
                merged = deepcopy(pending_turn)
                merged.edits[pending_index:pending_index + 1] = deepcopy(turn.edits)
                turn = merged
                if not turn.edits:
                    pending_turn = None
                    pending_index = None
                    correction_responses = 0
                    edit_validation_feedback = ""
                    feedback = "The pending batch was explicitly emptied; no files changed."
                    continue
            if not turn.edits:
                empty_edit_streak += 1
                attempts.append(
                    Attempt(
                        number=len(attempts) + 1,
                        hypothesis=turn.hypothesis,
                        edits=[],
                        applied=0,
                        build=None,
                        tokens=_normalize_usage(reported_usage),
                    )
                )

                if empty_edit_streak >= 2:
                    return finish(
                        "no_edits",
                        "Two consecutive edit proposals contained no edits.",
                    )

                feedback = (
                    "Your edit proposal contained no edits. "
                    "Investigate further, propose a supported change, "
                    "or return a stop action."
                )
                continue

            empty_edit_streak = 0
            applied = 0
            build = None
            snapshot = None
            build_error = None

            try:
                pending_index, edit_validation_feedback = missing_prefix_feedback(
                    context.worktree, turn.edits, patch_policy=patch_policy
                )
                if edit_validation_feedback:
                    pending_turn = deepcopy(turn)
                    continue
                snapshot = CandidateSnapshot.capture(
                    context.worktree,
                    [edit.path for edit in turn.edits],
                    patch_policy=patch_policy,
                )
                pending_index, edit_validation_feedback = focused_feedback(
                    snapshot, turn.edits, turn.hypothesis
                )
                if edit_validation_feedback:
                    pending_turn = deepcopy(turn)
                    continue
                pending_turn = None
                correction_responses = 0
                if review_edits:
                    current_key = signature(snapshot, turn)
                    if approved_key != current_key:
                        proposal_review, counts = render_review(
                            snapshot, turn,
                            best_build.diagnostics if best_build is not None else (initial_diagnostics or []),
                            patch_policy=patch_policy,
                            historical=bool(attempts) and best_build is None,
                        )
                        review_turn = deepcopy(turn)
                        review_key = current_key
                        review_events.append({"event": "proposed", "signature": current_key,
                                              "counts": counts, "feedback": proposal_review})
                        continue
                # Capture every affected file before applying any edits.
                for edit in turn.edits:
                    tracker.capture_before_edit(edit.path)
            except (OSError, ValueError) as exc:
                feedback = (
                    f"The edit batch was not applied because its files "
                    f"could not be captured: {exc}"
                )
                pending_turn = None
                pending_index = None
                correction_responses = 0
                edit_validation_feedback = (
                    "=== Edit batch validation failed ===\n"
                    "NO edits were written and NO build was requested.\n"
                    + feedback
                )
                continue
            else:
                if clock() >= work_deadline:
                    return finish(
                        "time_budget",
                        "No time remains to apply the edit batch.",
                    )

                applied = apply_edits(
                    context.worktree,
                    turn.edits,
                    patch_policy=patch_policy,
                )
                if review_edits and applied != len(turn.edits):
                    try:
                        snapshot.restore()
                    except (OSError, ValueError, RuntimeError) as exc:
                        return finish("restoration_error", f"Review batch restore failed: {exc}")
                    return finish("edit_application_error",
                                  "Reviewed batch was not fully applied; restored files and skipped build.")
                feedback = (
                    f"Accepted {applied} of {len(turn.edits)} proposed "
                    "edit operations."
                )

            # Record the attempt before requesting a build.
            attempt = Attempt(
                number=len(attempts) + 1,
                hypothesis=turn.hypothesis,
                edits=list(turn.edits),
                applied=applied,
                build=None,
                tokens=_normalize_usage(reported_usage),
            )
            attempts.append(attempt)

            # Drop only the tool results the edit may have made outdated.
            tool_results[:] = _still_valid(tool_results, {e.path for e in turn.edits})

            expired_before_build = clock() >= work_deadline

            if (
                not expired_before_build
                and applied > 0
                and config.use_build_feedback
                and build_feedback is not None
            ):
                build_calls += 1
                try:
                    build = build_feedback.request_build(context.worktree)
                except (OSError, RuntimeError, ValueError) as exc:
                    build_error = f"{type(exc).__name__}: {exc}"
                else:
                    attempt.build = build

            restored = False

            if applied > 0:
                should_restore = (
                    best_build is not None
                    and (
                        build is None
                        or _build_rank(build) < _build_rank(best_build)
                    )
                )

                if should_restore:
                    try:
                        if snapshot is None:
                            raise RuntimeError(
                                "Missing pre-edit snapshot."
                            )
                        snapshot.restore()
                    except (OSError, ValueError, RuntimeError) as exc:
                        return finish(
                            "restoration_error",
                            f"Could not fully restore the retained candidate: {exc}",
                        )
                    restored = True
                else:
                    kept_attempts.append(attempt.number)
                    best_build = deepcopy(build)

            if expired_before_build:
                return finish(
                    "time_budget",
                    "The working deadline was reached; no build was "
                    "started and candidate selection has completed.",
                )

            if build_error is not None:
                return finish(
                    "build_error",
                    f"Build feedback failed: {build_error}",
                )

            if build is None:
                feedback += (
                    "\nNo build result is available for this attempt; "
                    "the candidate has not been validated."
                )
            elif build.succeeded:
                return finish(
                    "build_succeeded",
                    "The build-feedback adapter reported success.",
                )
            else:
                messages = "\n".join(
                    diagnostic.message
                    for diagnostic in build.diagnostics
                )
                feedback += (
                    f"\nBuild failed. Reported stage: {build.stage_reached}."
                    f"\nDiagnostics:\n{messages or 'No diagnostics returned.'}"
                )

            if restored:
                feedback += (
                    "\nThis attempt was undone. The worktree now contains "
                    "the previously retained candidate. The diagnostics above "
                    "describe the discarded attempt, not the restored files."
                )
            if (
                applied > 0
                and build is not None
                and not build.succeeded
                and build.diagnostics
                and len(attempts) >= 2
            ):
                previous = attempts[-2]
                previous_build = previous.build

                if (
                    previous.applied > 0
                    and previous_build is not None
                    and not previous_build.succeeded
                    and previous_build.diagnostics
                ):
                    current_error = build.diagnostics[0]
                    previous_error = previous_build.diagnostics[0]

                    current_key = (
                        current_error.message,
                        current_error.log_path,
                        current_error.line,
                    )
                    previous_key = (
                        previous_error.message,
                        previous_error.log_path,
                        previous_error.line,
                    )

                    if (
                        current_key == previous_key
                        and _build_rank(build) <= _build_rank(previous_build)
                    ):
                        return finish(
                            "repeated_diagnostic",
                            "Two consecutive changed attempts reported "
                            "the same leading diagnostic without improvement "
                            "in the build-ranking heuristic.",
                        )

            latest_attempt_feedback = feedback
            feedback = ""

            continue

        raise NotImplementedError(
            f"Loop handling for action {turn.action!r} is not implemented yet."
        )

    return finish(
        "turn_budget",
        "The model-turn limit was reached.",
    )