"""Repair orchestration, built incrementally and tested offline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
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
from .edit_repair import prune_batch, retarget_path, simulate, static_warnings
from .path_feedback import missing_prefix_feedback, uncapturable_feedback
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
    # What the controller changed or dropped in the latest batch before
    # applying it (edit_repair): shown with the attempt's result.
    notes: list[str] = field(default_factory=list)

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
# rename files, so listings and name searches stay true, and the failure log
# is never edited.
_EDIT_PROOF = {"list_dir", "find_files", "patch_series", "read_log", "search_log"}
# Rejections about where an edit applies, as opposed to what it writes.
_ANCHOR_REASONS = {"old_text_not_found", "old_text_ambiguous", "path_outside_allowed_prefix",
                   "file_not_in_snapshot", "file_not_capturable"}


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
    log_path: Path | None = None,
    max_turns: int = 60,
    max_tool_calls: int = 120,
    clock: Callable[[], float] = monotonic,
) -> LoopResult:
    """Run the historical repair policy through explicit orchestration stages."""
    return _RepairSession(
        context, next_turn, config, budget, build_feedback, patch_policy,
        review_edits, initial_diagnostics, log_path, max_turns, max_tool_calls, clock,
    ).run()


_NEXT_TURN = object()


class _RepairSession:
    """One repair run: explicit state and ordered, independently named stages."""

    def __init__(
        self, context, next_turn, config, budget, build_feedback, patch_policy,
        review_edits, initial_diagnostics, log_path, max_turns, max_tool_calls, clock,
    ):
        self.context = context
        self.next_turn = next_turn
        self.config = config
        self.budget = budget
        self.build_feedback = build_feedback
        self.patch_policy = patch_policy
        self.review_edits = review_edits
        self.initial_diagnostics = initial_diagnostics
        self.log_path = log_path
        self.max_turns = max_turns
        self.max_tool_calls = max_tool_calls
        self.clock = clock
        self._validate_options()

        self.work_deadline = (
            self.clock() + self.budget.max_seconds - self.budget.reserve_seconds
        )
        # Initialize per run state
        self.build_calls = 0
        self.tracker = DiffTracker(self.context.worktree)
        self.tool_results = []
        self.tool_calls = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.known_usage = {"input_tokens": 0, "output_tokens": 0}
        self.feedback = ""
        self.latest_attempt_feedback = ""
        self.batch_notes: list[str] = []
        self.edit_validation_feedback = ""
        self.pending_turn = None
        self.pending_index = None
        self.review_turn = None
        self.review_key = None
        self.proposal_review = ""
        self.review_events = []
        self.correction_responses = 0
        self.max_correction_responses = 12
        # (path, old_text) of corrections already rejected for the pending item: a
        # repeat cannot succeed either (libunwind sent one ten times, 3 November).
        self.rejected_texts: set[tuple[str, str]] = set()
        # The item being corrected keeps an identity across corrections (a
        # correction replaces it in its slot), so its budget follows the item.
        self.slot_ids = iter(range(1, 1_000_000))
        self.pending_slot = None
        self.pending_position = 0           # that item's number in the model's list
        self.pending_notes: list[str] = []  # prune notes of the batch being corrected
        self.review_notes: list[str] = []   # prune notes of a batch awaiting approval
        self.malformed_count = 0
        self.empty_edit_streak = 0
        self.attempts: list[Attempt] = []
        self.best_build: BuildResult | None = None
        self.kept_attempts: list[int] = []

    def finish(self, reason: str, message: str) -> LoopResult:
        return LoopResult(
            termination_reason=reason,
            message=message,
            attempts=deepcopy(self.attempts),
            usage=dict(self.usage),
            malformed_count=self.malformed_count,
            known_usage=dict(self.known_usage),
            current_build=deepcopy(self.best_build),
            kept_attempts=list(self.kept_attempts),
            review_events=deepcopy(self.review_events),
        )

    def run(self):
        """Advance one model turn at a time until a stage returns a final result."""
        for _ in range(self.max_turns):
            outcome = self._check_budget()
            if outcome is not None:
                return outcome
            reply = self._request_turn()
            if reply is _NEXT_TURN:
                continue
            if isinstance(reply, LoopResult):
                return reply
            turn, reported_usage = reply
            outcome = self._dispatch(turn, reported_usage)
            if isinstance(outcome, LoopResult):
                return outcome
        return self.finish("turn_budget", "The model-turn limit was reached.")

    def _dispatch(self, turn, reported_usage):
        """Route investigation, review, and edit actions without changing their order."""
        if turn.action == "tool":
            return self._handle_tools(turn)
        reviewed = self._resolve_review(turn)
        if reviewed is _NEXT_TURN:
            return reviewed
        turn, approved_key = reviewed
        if turn.action != "edit":
            raise NotImplementedError(
                f"Loop handling for action {turn.action!r} is not implemented yet."
            )
        merged = self._merge_edit(turn, reported_usage)
        if merged is _NEXT_TURN or isinstance(merged, LoopResult):
            return merged
        turn, repeated = merged
        prepared = self._prepare_and_apply(turn, approved_key, repeated, reported_usage)
        if prepared is _NEXT_TURN or isinstance(prepared, LoopResult):
            return prepared
        turn, snapshot, applied = prepared
        return self._validate_candidate(turn, snapshot, applied, reported_usage)

    def _check_budget(self):
        """Check pre-turn limits in their original precedence order."""
        if self.clock() >= self.work_deadline:
            return self.finish(
                "time_budget",
                "The time reserved for graceful shutdown was reached.",
            )

        if len(self.attempts) >= self.budget.max_attempts:
            return self.finish(
                "attempt_budget",
                "The repair-attempt limit was reached.",
            )
        if (
            self.config.use_build_feedback
            and self.build_feedback is not None
            and self.build_calls >= self.budget.max_builds
        ):
            return self.finish(
                "build_budget",
                "The build-request limit was reached.",
            )
        known_tokens = sum(self.known_usage.values())

        if known_tokens >= self.budget.max_tokens:
            return self.finish(
                "token_budget",
                "The reported token usage reached the limit.",
            )

    def _make_request(self):
        """Snapshot only the evidence and history enabled for this run."""
        request = LoopRequest(
            context=self.context,
            tool_results=list(self.tool_results),
            feedback="\n\n".join(
                message
                for message in (self.latest_attempt_feedback, self.feedback)
                if message
            ),
            history=deepcopy(self.attempts) if self.config.use_history else [],
            current_build=deepcopy(self.best_build),
            kept_attempts=list(self.kept_attempts) if self.config.use_history else [],
            latest_attempt=deepcopy(self.attempts[-1]) if self.attempts else None,
            edit_validation_feedback=self.edit_validation_feedback,
            proposal_review=self.proposal_review,
            notes=list(self.batch_notes),
        )
        return request

    def _request_turn(self):
        """Call the model, account for usage, and handle malformed/stop replies."""
        request = self._make_request()
        try:
            turn, reported_usage = self.next_turn(request)
        except (OSError, RuntimeError, ValueError) as exc:
            unknown_usage = {
                "input_tokens": None,
                "output_tokens": None,
            }
            self.usage = _add_usage(self.usage, unknown_usage)
            return self.finish(
                "model_error",
                f"Model request failed: {type(exc).__name__}: {exc}",
            )

        if self.pending_turn is not None:
            self.correction_responses += 1

        reported_usage = _normalize_usage(reported_usage)

        for name, count in reported_usage.items():
            if count is not None:
                self.known_usage[name] += count

        self.usage = _add_usage(self.usage, reported_usage)

        self.feedback = ""

        if turn.action == "malformed":
            self.malformed_count += 1
            self.feedback = (
                f"Your previous reply was malformed: {turn.reason}\n"
                "Please resend a valid JSON action object."
            )
            return _NEXT_TURN

        if turn.action == "stop":
            return self.finish("model_stop", turn.reason)

        if self.clock() >= self.work_deadline:
            return self.finish(
                "time_budget",
                "The time reserved for graceful shutdown was reached.",
            )
        return turn, reported_usage

    def _handle_tools(self, turn):
        """Preserve pending edits while executing bounded read-only investigation."""
        if self.pending_turn is not None and not self.config.use_tools:
            self.edit_validation_feedback += "\nReturn one correction edit, an empty edit list to remove it, or stop."
            return _NEXT_TURN
        # During a correction the pending batch stays pending: reading the
        # file is how the model finds the text it failed to copy.
        if not self.config.use_tools:
            self.feedback = (
                "Investigation tools are disabled for this run. "
                "Use the evidence already provided to propose an edit "
                "or return a stop action."
            )
            return _NEXT_TURN

        for tool_request in turn.tool_requests:
            if self.clock() >= self.work_deadline:
                return self.finish(
                    "time_budget",
                    "No time remains to start another tool.",
                )

            if self.tool_calls >= self.max_tool_calls:
                return self.finish(
                    "tool_budget",
                    "The tool-call limit was reached.",
                )

            self.tool_calls += 1
            result = run_tool(
                tool_request,
                self.context.worktree,
                diff_tracker=self.tracker,
                log_path=self.log_path,
            )
            self.tool_results.append(result)

        return _NEXT_TURN

    def _resolve_review(self, turn):
        """Approve only the pending batch, or replace it with a revision."""
        approved_key = None
        if turn.action == "approve":
            if self.review_turn is None:
                self.feedback = "No pending diff is available to approve. Propose an edit or stop."
                return _NEXT_TURN
            approved_key = self.review_key
            self.review_events.append({"event": "approved", "signature": self.review_key, "reason": turn.reason})
            turn = deepcopy(self.review_turn)
            self.review_turn = None
            self.proposal_review = ""
        elif turn.action == "edit" and self.review_turn is not None:
            self.review_events.append({"event": "revised", "signature": self.review_key})
            self.review_turn = None
            self.proposal_review = ""
        return turn, approved_key

    def _merge_edit(self, turn, reported_usage):
        """Merge a single correction and account for explicitly empty proposals."""
        repeated = False   # the correction repeats an already rejected old_text
        if self.pending_turn is not None:
            if len(turn.edits) > 1:
                self.edit_validation_feedback += "\nYour correction contained multiple edits. Return at most ONE."
                return _NEXT_TURN
            merged = deepcopy(self.pending_turn)
            merged.edits[self.pending_index:self.pending_index + 1] = deepcopy(turn.edits)
            turn = merged
            if not turn.edits:
                self._clear_correction()
                self.edit_validation_feedback = ""
                self.feedback = "The pending batch was explicitly emptied; no files changed."
                return _NEXT_TURN
            for item in turn.edits[self.pending_index:self.pending_index + len(merged.edits)
                                   - len(self.pending_turn.edits) + 1]:
                item._slot = self.pending_slot
                item._position = self.pending_position
                repeated = (item.path, item.old_text) in self.rejected_texts
        for position, item in enumerate(turn.edits, start=1):
            if getattr(item, "_position", None) is None:
                item._position = position  # as the model listed it, for notes
        if not turn.edits:
            self.empty_edit_streak += 1
            self.batch_notes = []
            self.attempts.append(
                Attempt(
                    number=len(self.attempts) + 1,
                    hypothesis=turn.hypothesis,
                    edits=[],
                    applied=0,
                    build=None,
                    tokens=_normalize_usage(reported_usage),
                )
            )

            if self.empty_edit_streak >= 2:
                return self.finish(
                    "no_edits",
                    "Two consecutive edit proposals contained no edits.",
                )

            self.feedback = (
                "Your edit proposal contained no edits. "
                "Investigate further, propose a supported change, "
                "or return a stop action."
            )
            return _NEXT_TURN
        return turn, repeated

    def _prepare_and_apply(self, turn, approved_key, repeated, reported_usage):
        """Validate, review, and capture a batch before allowing any file writes."""
        self.empty_edit_streak = 0
        if approved_key is not None:
            notes = list(self.review_notes)
        elif self.pending_turn is not None:
            notes = list(self.pending_notes)
        else:
            notes = []
        self.review_notes = []

        try:
            # Validate; mechanically repair what is unambiguous; and when
            # the model cannot fix one item, drop that item alone and
            # apply the rest (without builds an unapplied batch is a
            # certain failure: rust-subversion, 26 edits, none written).
            turn, snapshot, reason, key = self._repair_batch(turn, notes, repeated)
            if turn.edits and self.edit_validation_feedback:
                if notes:
                    self.edit_validation_feedback += "\n\nBefore this check: " + " ".join(notes)
                self.pending_turn = deepcopy(turn)
                self.pending_notes = list(notes)
                if reason in _ANCHOR_REASONS:
                    # Only a rejected anchor makes a repeat hopeless; a
                    # new_text problem can be fixed with the same old_text.
                    self.rejected_texts.add(key)
                return _NEXT_TURN
            self._clear_correction()
            if not turn.edits:
                # Nothing left that can be applied: the batch is over, and
                # it costs an attempt so a run cannot loop on empty batches.
                self.attempts.append(Attempt(
                    number=len(self.attempts) + 1, hypothesis=turn.hypothesis,
                    edits=[], applied=0, build=None,
                    tokens=_normalize_usage(reported_usage),
                ))
                self.edit_validation_feedback = ""
                self.batch_notes = notes + ["No edit of that batch could be applied, so no "
                                       "file changed. Propose a new batch or stop."]
                self.feedback = " ".join(self.batch_notes)
                return _NEXT_TURN
            warnings = self._review_batch(turn, snapshot, approved_key, notes)
            if warnings is _NEXT_TURN:
                return _NEXT_TURN
            # Capture every affected file before applying any edits.
            for edit in turn.edits:
                self.tracker.capture_before_edit(edit.path)
        except (OSError, ValueError) as exc:
            self.feedback = (
                f"The edit batch was not applied because its files "
                f"could not be captured: {exc}"
            )
            self.batch_notes = []
            self.attempts.append(Attempt(
                number=len(self.attempts) + 1, hypothesis=turn.hypothesis,
                edits=[], applied=0, build=None,
                tokens=_normalize_usage(reported_usage),
            ))
            self._clear_correction()
            self.edit_validation_feedback = (
                "=== Edit batch validation failed ===\n"
                "NO edits were written and NO build was requested.\n"
                + self.feedback
            )
            return _NEXT_TURN
        return self._apply_batch(turn, snapshot, notes, warnings)

    def _validate_candidate(self, turn, snapshot, applied, reported_usage):
        """Record the attempt, optionally build, select a candidate, and report it."""
        build = None
        build_error = None
        # Record the attempt before requesting a build.
        attempt = Attempt(
            number=len(self.attempts) + 1,
            hypothesis=turn.hypothesis,
            edits=list(turn.edits),
            applied=applied,
            build=None,
            tokens=_normalize_usage(reported_usage),
        )
        self.attempts.append(attempt)

        # Drop only the tool results the edit may have made outdated.
        self.tool_results[:] = _still_valid(self.tool_results, {e.path for e in turn.edits})

        expired_before_build = self.clock() >= self.work_deadline

        if (
            not expired_before_build
            and applied > 0
            and self.config.use_build_feedback
            and self.build_feedback is not None
        ):
            self.build_calls += 1
            try:
                build = self.build_feedback.request_build(self.context.worktree)
            except (OSError, RuntimeError, ValueError) as exc:
                build_error = f"{type(exc).__name__}: {exc}"
            else:
                attempt.build = build

        restored = self._select_candidate(attempt, snapshot, applied, build)
        if isinstance(restored, LoopResult):
            return restored

        if expired_before_build:
            return self.finish(
                "time_budget",
                "The working deadline was reached; no build was "
                "started and candidate selection has completed.",
            )

        if build_error is not None:
            return self.finish(
                "build_error",
                f"Build feedback failed: {build_error}",
            )

        outcome = self._describe_build(build, restored)
        if outcome is not None:
            return outcome
        outcome = self._check_repeated_diagnostic(applied, build)
        if outcome is not None:
            return outcome

        self.latest_attempt_feedback = self.feedback
        self.feedback = ""

        return _NEXT_TURN

    def _repair_batch(self, turn, notes, repeated):
        """Repair unambiguous anchors; drop only items whose corrections are exhausted."""
        snapshot = None
        reason = ""
        key = None
        while True:
            turn, snapshot = self._check_batch(turn, notes)
            if not turn.edits or not self.edit_validation_feedback:
                break
            failing = turn.edits[self.pending_index]
            key = (failing.path, failing.old_text)
            reason = re.search(r"Reason: (\S+)", self.edit_validation_feedback)
            reason = reason.group(1) if reason else ""
            if reason == "old_text_not_found" and not getattr(failing, "_retargeted", False):
                target = retarget_path(self.context.worktree, failing, patch_policy=self.patch_policy)
                if target is not None:
                    notes.append(
                        f"Moved edit {getattr(failing, '_position', self.pending_index + 1)} "
                        f"from {failing.path} to {target}: its old_text is not in the "
                        "file it named and occurs in that one file only.")
                    failing.path = target
                    failing._retargeted = True
                    self.edit_validation_feedback = ""
                    continue
            slot = getattr(failing, "_slot", None)
            if slot is None:
                slot = next(self.slot_ids)
                failing._slot = slot
            if slot != self.pending_slot:
                # A different item: it gets corrections of its own, but
                # not a whole new budget after one item has used it up.
                if self.pending_slot is not None:
                    self.correction_responses = min(
                        self.correction_responses, self.max_correction_responses - 3)
                self.pending_slot = slot
                self.pending_position = getattr(failing, "_position", self.pending_index + 1)
                self.rejected_texts.clear()
                repeated = False
            if repeated:
                why = "its correction repeated an old_text that was already rejected"
            elif self.correction_responses >= self.max_correction_responses:
                why = "the correction budget was spent on it"
            else:
                break
            preview = failing.old_text[:60].replace("\n", "\\n")
            notes.append(
                f"Dropped edit {self.pending_position} ({failing.path}, old_text starting "
                f"{preview!r}) because {why}{' (' + reason + ')' if reason else ''}; "
                "the rest of the batch continued."
            )
            del turn.edits[self.pending_index]
            repeated = False
            self.rejected_texts.clear()
            self.edit_validation_feedback = ""
        return turn, snapshot, reason, key

    def _check_batch(self, turn, notes):
        """Check paths, capture source, prune edits, then validate remaining anchors."""
        snapshot = None
        self.pending_index, self.edit_validation_feedback = missing_prefix_feedback(
            self.context.worktree, turn.edits, patch_policy=self.patch_policy
        )
        if not self.edit_validation_feedback:
            self.pending_index, self.edit_validation_feedback = uncapturable_feedback(
                self.context.worktree, turn.edits, patch_policy=self.patch_policy
            )
        if not self.edit_validation_feedback:
            snapshot = CandidateSnapshot.capture(
                self.context.worktree,
                [edit.path for edit in turn.edits],
                patch_policy=self.patch_policy,
            )
            kept, more, _ = prune_batch(snapshot, turn.edits)
            notes.extend(more)
            turn = deepcopy(turn)
            turn.edits = kept
            if turn.edits:
                self.pending_index, self.edit_validation_feedback = focused_feedback(
                    snapshot, turn.edits, turn.hypothesis
                )
        return turn, snapshot

    def _review_batch(self, turn, snapshot, approved_key, notes):
        """Simulate warnings and request approval for the exact pending signature."""
        originals = {saved.path: saved.content.decode("utf-8")
                     for saved in snapshot.files}
        warnings = []
        for path, text in simulate(snapshot, turn.edits).items():
            warnings.extend(static_warnings(path, originals.get(path, ""), text))
        if self.review_edits:
            current_key = signature(snapshot, turn)
            if approved_key != current_key:
                self.proposal_review, counts = render_review(
                    snapshot, turn,
                    self.best_build.diagnostics if self.best_build is not None else (self.initial_diagnostics or []),
                    patch_policy=self.patch_policy,
                    historical=bool(self.attempts) and self.best_build is None,
                )
                self.review_turn = deepcopy(turn)
                self.review_key = current_key
                self.review_notes = notes
                self.review_events.append({"event": "proposed", "signature": current_key,
                                      "counts": counts, "feedback": self.proposal_review})
                return _NEXT_TURN
        return warnings

    def _apply_batch(self, turn, snapshot, notes, warnings):
        """Write the batch; roll back partial application when review is enabled."""
        if self.clock() >= self.work_deadline:
            return self.finish(
                "time_budget",
                "No time remains to apply the edit batch.",
            )

        applied = apply_edits(
            self.context.worktree,
            turn.edits,
            patch_policy=self.patch_policy,
        )
        if self.review_edits and applied != len(turn.edits):
            try:
                snapshot.restore()
            except (OSError, ValueError, RuntimeError) as exc:
                return self.finish("restoration_error", f"Review batch restore failed: {exc}")
            return self.finish("edit_application_error",
                          "Reviewed batch was not fully applied; restored files and skipped build.")
        self.feedback = (
            f"Accepted {applied} of {len(turn.edits)} proposed "
            "edit operations."
        )
        self.batch_notes = notes + [f"Warning (the edit was applied): {w}" for w in warnings]
        if self.batch_notes:
            self.feedback += " " + " ".join(self.batch_notes)
        return turn, snapshot, applied

    def _select_candidate(self, attempt, snapshot, applied, build):
        """Retain an improved candidate or restore the previous one."""
        restored = False

        if applied > 0:
            should_restore = (
                self.best_build is not None
                and (
                    build is None
                    or _build_rank(build) < _build_rank(self.best_build)
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
                    return self.finish(
                        "restoration_error",
                        f"Could not fully restore the retained candidate: {exc}",
                    )
                restored = True
            else:
                self.kept_attempts.append(attempt.number)
                self.best_build = deepcopy(build)
        return restored

    def _describe_build(self, build, restored):
        """Render feedback without presenting an unbuilt candidate as validated."""
        if build is None:
            self.feedback += (
                "\nNo build result is available for this attempt; "
                "the candidate has not been validated."
            )
        elif build.succeeded:
            return self.finish(
                "build_succeeded",
                "The build-feedback adapter reported success.",
            )
        else:
            messages = "\n".join(
                diagnostic.message
                for diagnostic in build.diagnostics
            )
            self.feedback += (
                f"\nBuild failed. Reported stage: {build.stage_reached}."
                f"\nDiagnostics:\n{messages or 'No diagnostics returned.'}"
            )

        if restored:
            self.feedback += (
                "\nThis attempt was undone. The worktree now contains "
                "the previously retained candidate. The diagnostics above "
                "describe the discarded attempt, not the restored files."
            )

    def _check_repeated_diagnostic(self, applied, build):
        """Stop only when consecutive changed attempts repeat without progress."""
        if (
            applied > 0
            and build is not None
            and not build.succeeded
            and build.diagnostics
            and len(self.attempts) >= 2
        ):
            previous = self.attempts[-2]
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
                    return self.finish(
                        "repeated_diagnostic",
                        "Two consecutive changed attempts reported "
                        "the same leading diagnostic without improvement "
                        "in the build-ranking heuristic.",
                    )

    def _validate_options(self):
        # Provisional local safeguards, not official competition limits.
        """Reject invalid limits before allocating per-run resources."""
        if type(self.review_edits) is not bool:
            raise ValueError("review_edits must be a boolean")
        if type(self.max_turns) is not int or self.max_turns < 1:
            raise ValueError("max_turns must be a positive integer.")
        if type(self.max_tool_calls) is not int or self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be a non-negative integer.")
        if self.config is None:
            self.config = AgentConfig()
        if self.budget is None:
            self.budget = Budget()
        if type(self.budget.max_attempts) is not int or self.budget.max_attempts < 0:
            raise ValueError("max_attempts must be a non-negative integer.")
        if type(self.budget.max_builds) is not int or self.budget.max_builds < 0:
            raise ValueError("max_builds must be a non-negative integer.")
        if type(self.budget.max_tokens) is not int or self.budget.max_tokens < 0:
            raise ValueError("max_tokens must be a non-negative integer.")
        if type(self.budget.max_seconds) is not int or self.budget.max_seconds <= 0:
            raise ValueError("max_seconds must be a positive integer.")
        if (
            type(self.budget.reserve_seconds) is not int
            or not 0 <= self.budget.reserve_seconds <= self.budget.max_seconds
        ):
            raise ValueError(
                "reserve_seconds must be between zero and max_seconds."
            )

    def _clear_correction(self):
        """Release correction state without erasing separately managed feedback."""
        self.pending_turn = None
        self.pending_index = None
        self.pending_slot = None
        self.pending_notes = []
        self.correction_responses = 0
        self.rejected_texts.clear()
