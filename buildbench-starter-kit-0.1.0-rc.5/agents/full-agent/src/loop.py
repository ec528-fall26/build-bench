"""Repair orchestration, built incrementally and tested offline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import json
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
from .edit_repair import expand_macros, prune_batch, retarget_path, simulate, static_warnings
from .path_feedback import missing_prefix_feedback, uncapturable_feedback
from . import arch_model, patterns, verify
from .tools import run_tool
from .types import CaseContext
from .proposal_review import render_review, signature, edit_identity
from . import ffi_analysis, rules_guard, syntax_guard, output_verification, completion


OWN_EDITS_CHARS = 6000
# Controller completion of a partial mechanical repair (libbpp-core, 9 Nov live run: 11 of 12
# symbols hand-tagged, then stop). Symbols only; applied through the normal edit gates.
COMPLETE_MECHANICAL_REPAIRS = True
UNFIXED_STOP = (
    "Controller: you replied stop, but the latest static prediction still reports a construct "
    "NOT fixed, and a build with it fails the same way. The unresolved verdict(s), in full:\n{lines}\n"
    "Address them in your next edit. If you are certain the prediction is wrong, reply stop again "
    "and it will be accepted.")
EMPTY_STOP = (
    "You stopped without changing any file. An unchanged package always fails: the "
    "platform rejects a run with no change under input/, so stopping now cannot be "
    "better than your best-supported repair. Using what you have read, propose the "
    "edit most likely to fix the failure (investigate further first if a specific "
    "file is still needed). Stop again only if you are certain that no edit under "
    "input/ can repair this build.")
# Loop control (campaign 2026-11-09, M5). Evidence: rust-subversion spent 23
# turns after an all-fixed prediction, libbpp-qt issued 11 identical requests,
# and replies with no action wasted 91 of 543 turns.
ALL_FIXED_TOOL_TURNS = 2     # tool turns still allowed once every verdict is ok
ALL_FIXED_REFUSALS = 3       # refused tool turns after which the run ends
ALL_FIXED_STOP = (
    "Controller verdict: every construct the errors came from is predicted fixed. "
    "Required next action: stop. Call a tool only if you can name a specific error "
    "line this edit does not address.")
STUB_PREFIX = "Identical to your request at turn"
INVESTIGATION_NUDGE_AT = 8
INVESTIGATION_FINAL_AT = 12
INVESTIGATION_NUDGE = (
    "You have used 8 investigation turns without proposing an edit. State the most "
    "probable cause and the edit, or stop and name the evidence that is missing.")
INVESTIGATION_FINAL = (
    "You have used {n} investigation turns without proposing an edit. Your next reply "
    "must be an edit or a stop.")
MAX_CACHED_REQUESTS = 400
# Distinct tool results the adapter shows (model_adapter.MAX_TOOL_RESULTS is this value). A repeat
# of a request whose result has scrolled out of that window must run again, not be stubbed.
SHOWN_TOOL_RESULTS = 12


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
    # Without builds (no-build control): the model's own edits so far as a diff
    # against the original package, and a one-off message from the controller.
    own_edits: str = ""
    controller_note: str = ""
    output_verification: list = field(default_factory=list)
    output_verification_feedback: str = ""
    verification_format_feedback: str = ""
    # verify.render's static prediction for the latest unbuilt batch, or "".
    prediction: str = ""
    # That prediction in one word ("fixed", "not_fixed", "unknown", or "" when
    # there is none) and the reason for it, for the attempt's VERDICT line.
    prediction_status: str = ""
    prediction_reason: str = ""
    # Cumulative tool turns the loop did not count (an all-stub turn, or one
    # refused after an all-fixed verdict); the adapter keeps them out of its
    # own investigation counters.
    uncounted_tool_turns: int = 0

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

# What one stage hands back to the turn loop: a LoopResult ends the run; None
# means "ask the model again".
_CONTINUE = None


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
    """Run the repair loop for one case until it stops; see ``_Loop``."""
    return _Loop(
        context, next_turn=next_turn, config=config, budget=budget,
        build_feedback=build_feedback, patch_policy=patch_policy,
        review_edits=review_edits, initial_diagnostics=initial_diagnostics,
        log_path=log_path, max_turns=max_turns, max_tool_calls=max_tool_calls,
        clock=clock,
    ).run()


def _validate_limits(budget: Budget, review_edits, max_turns, max_tool_calls) -> None:
    # Provisional local safeguards, not official competition limits.
    if type(review_edits) is not bool:
        raise ValueError("review_edits must be a boolean")
    if type(max_turns) is not int or max_turns < 1:
        raise ValueError("max_turns must be a positive integer.")
    if type(max_tool_calls) is not int or max_tool_calls < 0:
        raise ValueError("max_tool_calls must be a non-negative integer.")
    if type(budget.max_attempts) is not int or budget.max_attempts < 0:
        raise ValueError("max_attempts must be a non-negative integer.")
    if type(budget.max_builds) is not int or budget.max_builds < 0:
        raise ValueError("max_builds must be a non-negative integer.")
    if type(budget.max_tokens) is not int or budget.max_tokens < 0:
        raise ValueError("max_tokens must be a non-negative integer.")
    if type(budget.max_seconds) is not int or budget.max_seconds <= 0:
        raise ValueError("max_seconds must be a positive integer.")
    if (type(budget.reserve_seconds) is not int
            or not 0 <= budget.reserve_seconds <= budget.max_seconds):
        raise ValueError("reserve_seconds must be between zero and max_seconds.")


class _Loop:
    """One case's repair loop: a model turn is requested, parsed and acted on
    until a budget runs out or the model stops.

    Each stage of a turn is one method, in the order ``run`` calls them:

      _exhausted         the attempt, build, token and time budgets
      _request           what the model is shown this turn
      _ask_model         one model call, usage accounting
      _tool_turn         read-only tools, bounded by a call budget
      _resolve_review    an approve or revise reply to a reviewed batch
      _edit_turn         an edit batch, in six steps:
        _merge_correction    a focused correction replaces one pending item
        _validate            preflight; mechanical repair; one item at a
                             time back to the model (edit_repair, path_feedback)
        _review              optional proposed-batch review
        _apply               write the batch, record the attempt
        _build               build feedback, if any, and candidate retention
        _attempt_feedback    what the model is told, and the repeated-
                             diagnostic stop

    A stage returns a LoopResult to end the run or _CONTINUE to go on to the
    next model turn. The run's state is the instance; ``finish`` snapshots it.
    """

    MAX_CORRECTIONS = 12   # per batch; a later item always keeps at least 3
    CORRECTION_TOOL_TURNS = 2   # tool turns allowed while one item awaits correction

    def __init__(self, context: CaseContext, *, next_turn, config, budget, build_feedback,
                 patch_policy, review_edits, initial_diagnostics, log_path, max_turns,
                 max_tool_calls, clock):
        self.config = config if config is not None else AgentConfig()
        self.budget = budget if budget is not None else Budget()
        _validate_limits(self.budget, review_edits, max_turns, max_tool_calls)
        self.context = context
        self.next_turn = next_turn
        self.build_feedback = build_feedback
        self.patch_policy = patch_policy
        self.ffi = ffi_analysis.analyze(context.worktree) if ffi_analysis.enabled() else None
        if self.ffi is not None and self.config.use_build_feedback and build_feedback is not None:
            raise ValueError('BB_FFI_ANALYSIS requires BB_AGENT_USE_BUILD_FEEDBACK=0')
        self.review_edits = review_edits or self.ffi is not None
        self.output_gate = output_verification.enabled()
        if self.output_gate and not self.review_edits:
            raise ValueError("Output verification requires BB_REVIEW_EDITS=1")
        self.verification_pending = False
        self.verification_failures = 0
        self.verification_format_failures = 0
        self.verification_format_feedback = ""
        self.verified_key = None
        self.output_verification_feedback = ""
        self.initial_diagnostics = initial_diagnostics
        self.log_path = log_path
        self.max_turns = max_turns
        self.max_tool_calls = max_tool_calls
        self.clock = clock
        self.work_deadline = clock() + self.budget.max_seconds - self.budget.reserve_seconds

        # Accounting
        self.build_calls = 0
        self.tool_calls = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.known_usage = {"input_tokens": 0, "output_tokens": 0}
        self.malformed_count = 0
        self.empty_edit_streak = 0
        self.empty_stop_refused = False
        self.unfixed_stop_refused = False
        self.controller_note = ""
        # Edits the syntax guard sent back once; resent unchanged, they apply.
        self.syntax_allowed: set[tuple] = set()
        self.syntax_seconds_left = syntax_guard.RUN_BUDGET
        # What the model is told next turn
        self.tracker = DiffTracker(context.worktree)
        self.tool_results: list[ToolResult] = []
        self.feedback = ""
        self.latest_attempt_feedback = ""
        self.batch_notes: list[str] = []
        self.prediction = ""                 # verify.render for the latest unbuilt batch
        self.prediction_status = ""          # "fixed" | "not_fixed" | "unknown" | ""
        self.prediction_reason = ""
        # Loop control
        self.model_turns = 0
        self.all_fixed = False               # every verdict of the latest prediction is ok
        self.fixed_tool_turns = 0
        self.fixed_refusals = 0
        self.files_read: set[str] = set()
        self.tool_cache: dict[tuple, tuple[int, int]] = {}   # request -> (turn, edit serial)
        self.edit_serial = 0                 # bumped whenever the worktree may have changed
        self.path_serial: dict[str, int] = {}
        self.investigation_turns = 0         # counted tool turns since the last applied edit
        self.uncounted_tool_turns = 0
        self.edit_validation_feedback = ""
        self.proposal_review = ""
        # The target architecture, for the static prediction of an unbuilt batch.
        metadata = getattr(context, "task_metadata", None) or {}
        self.target = arch_model.lookup(str(metadata.get("target_arch") or ""))
        self.recipe_hint = ""   # the best verified recipe's invocation, from the pristine tree
        try:
            self.recipe_hint = completion.stop_hint(
                context.worktree, patterns._log_lines(log_path), str(metadata.get("target_arch") or ""))
        except Exception:
            self.recipe_hint = ""
        if self.target is None:
            pair = arch_model.pair_from_case_id(getattr(context, "case_id", None))
            self.target = arch_model.lookup(pair[1]) if pair else None
        # The batch being corrected one item at a time
        self.pending_turn = None
        self.pending_index = None
        self.pending_notes: list[str] = []   # prune notes of that batch
        self.correction_responses = 0
        self.correction_tool_turns = 0       # rust-subversion spent 20 turns searching mid-correction
        # (path, old_text) of corrections already rejected for the pending item:
        # a repeat cannot succeed either (libunwind sent one ten times, 3 November).
        self.rejected_texts: set[tuple[str, str]] = set()
        # The item being corrected keeps an identity across corrections (a
        # correction replaces it in its slot), so its budget follows the item.
        self.slot_ids = iter(range(1, 1_000_000))
        self.pending_slot = None
        self.pending_position = 0          # that item's number in the model's list
        # A batch awaiting the model's approval (review mode)
        self.review_turn = None
        self.review_repeat_count = 0
        self.review_key = None
        self.review_notes: list[str] = []
        self.review_events: list[dict] = []
        # Attempts and the retained candidate
        self.attempts: list[Attempt] = []
        self.best_build: BuildResult | None = None
        self.kept_attempts: list[int] = []
        # Per-turn working state
        self.turn: ModelTurn | None = None
        self.reported_usage: dict = {}

    # --- run -----------------------------------------------------------------

    def run(self) -> LoopResult:
        for _ in range(self.max_turns):
            result = self._exhausted() or self._ask_model()
            if result is not None:
                return result
            action = self.turn.action
            if (self.verification_pending and
                    sum(self.known_usage.values()) >= self.budget.max_tokens):
                return self.finish("token_budget", "Output verification exhausted the token budget; no pending edit applied.")
            if action == "malformed":
                self._malformed()
                continue
            if action == "stop":
                if self._refuse_empty_stop() or self._refuse_unfixed_stop():
                    continue
                return self.finish("model_stop", self.turn.reason)
            if self.clock() >= self.work_deadline:
                return self.finish("time_budget", "The time reserved for graceful shutdown was reached.")
            if action == "tool":
                result = self._tool_turn()
            else:
                approved_key = self._resolve_review()
                if approved_key is False:
                    continue
                if self.turn.action != "edit":
                    raise NotImplementedError(
                        f"Loop handling for action {self.turn.action!r} is not implemented yet.")
                result = self._edit_turn(approved_key)
            if result is not None:
                return result
        return self.finish("turn_budget", "The model-turn limit was reached.")

    def finish(self, reason: str, message: str) -> LoopResult:
        if self.ffi is not None:
            self.review_events.append({'event': 'ffi_final', 'analysis_mode': ffi_analysis.METHOD,
                'coverage': self.ffi.report(ffi_analysis.index_sources(self.context.worktree),
                                            'final worktree; not build validated')})
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

    def _out_of_time(self) -> bool:
        return self.clock() >= self.work_deadline

    def _record_attempt(self, applied: int, edits: list) -> Attempt:
        attempt = Attempt(
            number=len(self.attempts) + 1, hypothesis=self.turn.hypothesis,
            edits=edits, applied=applied, build=None,
            tokens=_normalize_usage(self.reported_usage),
        )
        self.attempts.append(attempt)
        return attempt

    def _clear_pending(self) -> None:
        self.pending_turn = None
        self.pending_index = None
        self.pending_slot = None
        self.pending_notes = []
        self.correction_responses = 0
        self.correction_tool_turns = 0
        self.rejected_texts.clear()

    # --- without builds (no-build control) ----------------------------------

    def _no_build_control(self) -> bool:
        return (getattr(self.config, "use_no_build_control", False)
                and not (self.config.use_build_feedback and self.build_feedback is not None))

    def _own_edits(self) -> str:
        """The model's own edits so far, as a diff against the original files.

        Every turn is rebuilt from the evidence, and the model's earlier
        reasoning is not in it, so without builds it read its own edit as part
        of the package, saw the original error still in the original log and
        concluded the edit had failed (libmbim, 6 November: "they set
        GTKDOCIZE ... but still error", eight stacked attempts).
        """
        if not self._no_build_control() or not any(a.applied for a in self.attempts):
            return ""
        try:
            diff, truncated = self.tracker.current_diff(max_chars=OWN_EDITS_CHARS)
        except (OSError, ValueError):
            return ""
        if not diff.strip():
            return ""
        return diff + ("\n[diff truncated]" if truncated else "")

    def _refuse_empty_stop(self) -> bool:
        """Once per run, send back a stop that would submit nothing.

        An unchanged worktree always fails (the platform marks it invalid:
        "Agent made no allowed input change"); 8 of 111 baseline runs stopped
        with nothing, most saying the evidence was insufficient.
        """
        if (not self._no_build_control() or self.empty_stop_refused
                or any(a.applied for a in self.attempts)):
            return False
        self.empty_stop_refused = True
        self.controller_note = EMPTY_STOP
        return True

    def _refuse_unfixed_stop(self) -> bool:
        """Once per run, send back a stop while the latest prediction holds a NOT-fixed
        verdict (libbpp-core: "1 of 12 still untagged", then "no further edits needed").
        The message quotes the verdict lines whole and repeats the recipe invocation when
        the log shows a verified recipe. The empty-stop bounce is separate; each fires at
        most once, and a second stop is accepted."""
        if self.unfixed_stop_refused:
            return False
        lines = [line for line in (self.prediction or "").split("\n") if line.startswith("- [NOT fixed]")]
        if not lines:
            return False
        self.unfixed_stop_refused = True
        text = UNFIXED_STOP.format(lines="\n".join(lines))
        if self.recipe_hint:
            text += "\n" + self.recipe_hint
        self.controller_note = "\n".join(part for part in (self.controller_note, text) if part)
        self.review_events.append({"event": "unfixed_stop_refused", "verdicts": len(lines)})
        return True

    def _complete_symbols(self, snapshot, applied: int) -> list[str]:
        """After an applied batch: tag the symbols the model left untagged, through the
        normal gates, when the symbols-tag recipe matches this case. Returns notes for
        the model; never raises, touches only the symbols file the log names."""
        if (not COMPLETE_MECHANICAL_REPAIRS or not applied
                or "still listed without an (arch=...) or (optional) tag" not in (self.prediction or "")):
            return []
        saved_turn, saved_index = self.turn, self.pending_index
        try:
            log_lines = patterns._log_lines(self.log_path)
            edits, names = completion.symbols_completion(
                self.context.worktree, log_lines,
                str((getattr(self.context, "task_metadata", None) or {}).get("target_arch", "")),
                {edit.path for edit in self.turn.edits})
            if not edits:
                return []
            self.turn = deepcopy(self.turn)
            self.turn.edits = edits
            gated, feedback = self._preflight_batch(None, [])
            if feedback or gated is None or len(self.turn.edits) != len(edits):
                return []
            if apply_edits(self.context.worktree, self.turn.edits,
                           patch_policy=self.patch_policy) != len(edits):
                return []
            note = (f"Controller completed the symbols-tag recipe: tagged {len(names)} remaining "
                    f"symbol(s): {', '.join(names)}")
            self.review_events.append({"event": "controller_completed", "recipe": completion.RECIPE,
                                       "symbols": list(names)})
            return [note]
        except Exception:
            return []
        finally:
            self.turn, self.pending_index = saved_turn, saved_index

    # --- before the model is asked ------------------------------------------

    def _exhausted(self) -> LoopResult | None:
        if self.verification_format_failures >= 2:
            return self.finish("output_verification_format_failed",
                               "Two malformed verification responses; no approval inferred.")
        if self.verification_failures >= 3:
            return self.finish("output_verification_stalled",
                               "Three output checks failed; the pending batch was not applied.")
        if self.review_repeat_count >= 3:
            return self.finish("review_stalled",
                               "The same pending patch was resubmitted three times without revision; "
                               "it was not approved or applied.")
        if self._out_of_time():
            return self.finish("time_budget", "The time reserved for graceful shutdown was reached.")
        if len(self.attempts) >= self.budget.max_attempts:
            return self.finish("attempt_budget", "The repair-attempt limit was reached.")
        if (self.config.use_build_feedback and self.build_feedback is not None
                and self.build_calls >= self.budget.max_builds):
            return self.finish("build_budget", "The build-request limit was reached.")
        if sum(self.known_usage.values()) >= self.budget.max_tokens:
            return self.finish("token_budget", "The reported token usage reached the limit.")
        return _CONTINUE

    def _request(self) -> LoopRequest:
        config = self.config
        return LoopRequest(
            context=self.context,
            tool_results=list(self.tool_results),
            feedback="\n\n".join(m for m in (self.latest_attempt_feedback, self.feedback) if m),
            history=deepcopy(self.attempts) if config.use_history else [],
            current_build=deepcopy(self.best_build),
            kept_attempts=list(self.kept_attempts) if config.use_history else [],
            latest_attempt=deepcopy(self.attempts[-1]) if self.attempts else None,
            edit_validation_feedback=self.edit_validation_feedback,
            proposal_review=self.proposal_review,
            notes=list(self.batch_notes),
            own_edits=self._own_edits(),
            controller_note=self.controller_note,
            output_verification_feedback=self.output_verification_feedback,
            verification_format_feedback=self.verification_format_feedback,
            output_verification=(deepcopy(self.review_turn.edits)
                                 if self.verification_pending and self.review_turn else []),
            prediction=self.prediction,
            prediction_status=self.prediction_status,
            prediction_reason=self.prediction_reason,
            uncounted_tool_turns=self.uncounted_tool_turns,
        )

    def _note(self, text: str) -> None:
        """Add a one-off controller message for the next request."""
        self.controller_note = "\n".join(part for part in (self.controller_note, text) if part)

    def _ask_model(self) -> LoopResult | None:
        self.model_turns += 1
        try:
            request = self._request()
            self.controller_note = ""  # delivered with this request
            self.turn, reported = self.next_turn(request)
        except (OSError, RuntimeError, ValueError) as exc:
            self.usage = _add_usage(self.usage, {"input_tokens": None, "output_tokens": None})
            return self.finish("model_error", f"Model request failed: {type(exc).__name__}: {exc}")
        if self.pending_turn is not None:
            self.correction_responses += 1
        self.reported_usage = _normalize_usage(reported)
        for name, count in self.reported_usage.items():
            if count is not None:
                self.known_usage[name] += count
        self.usage = _add_usage(self.usage, self.reported_usage)
        self.feedback = ""
        return _CONTINUE

    # --- turns that are not edits -------------------------------------------

    def _malformed(self) -> None:
        self.malformed_count += 1
        self.feedback = (f"Your previous reply was malformed: {self.turn.reason}\n"
                         "Please resend a valid JSON action object.")

    def _tool_turn(self) -> LoopResult | None:
        if not self.config.use_tools:
            if self.pending_turn is not None:
                self.edit_validation_feedback += (
                    "\nReturn one correction edit, an empty edit list to remove it, or stop.")
            else:
                self.feedback = ("Investigation tools are disabled for this run. "
                                 "Use the evidence already provided to propose an edit "
                                 "or return a stop action.")
            return _CONTINUE
        # During a correction the pending batch stays pending: reading the
        # file is how the model finds the text it failed to copy, but two
        # turns of it are enough (rust-subversion, 3 November: 20 turns).
        if self.pending_turn is not None:
            self.correction_tool_turns += 1
            if self.correction_tool_turns > self.CORRECTION_TOOL_TURNS:
                self.edit_validation_feedback += (
                    f"\nYou have used {self.CORRECTION_TOOL_TURNS} tool turns on this correction; no more are "
                    "run. Return the corrected edit (its old_text copied from the text you were shown), "
                    "an empty edit list to drop the item, or stop.")
                return _CONTINUE
        requests = self.turn.tool_requests
        if self.all_fixed and self.pending_turn is None:
            requests = self._after_all_fixed(requests)
            if not requests:
                self.uncounted_tool_turns += 1
                self.fixed_refusals += 1
                self._note(ALL_FIXED_STOP)
                if self.fixed_refusals >= ALL_FIXED_REFUSALS:
                    return self.finish(
                        "model_stop", "Stopped by the controller: every construct was predicted "
                        "fixed and the model kept requesting tools.")
                return _CONTINUE
            self.fixed_tool_turns += 1
        ran = 0
        for tool_request in requests:
            if self._out_of_time():
                return self.finish("time_budget", "No time remains to start another tool.")
            earlier = self._repeat_of(tool_request)
            if earlier is not None:
                self.tool_results.append(ToolResult(
                    tool_request, f"{STUB_PREFIX} {earlier}; the result has not changed", False))
                continue
            if self.tool_calls >= self.max_tool_calls:
                return self.finish("tool_budget", "The tool-call limit was reached.")
            self.tool_calls += 1
            ran += 1
            self.tool_results.append(run_tool(
                tool_request, self.context.worktree,
                diff_tracker=self.tracker, log_path=self.log_path,
            ))
            self._remember(tool_request)
        if self.pending_turn is None:
            self._investigation(counted=ran > 0)
        return _CONTINUE

    @staticmethod
    def _request_key(tool_request) -> tuple:
        return (tool_request.tool, json.dumps(tool_request.args, sort_keys=True, default=str))

    def _touch(self, paths) -> None:
        """The worktree changed: cached results for these files, and every
        search, are no longer known to be true."""
        self.edit_serial += 1
        for path in paths:
            self.path_serial[path] = self.edit_serial

    def _remember(self, tool_request) -> None:
        if len(self.tool_cache) >= MAX_CACHED_REQUESTS:
            return
        self.tool_cache[self._request_key(tool_request)] = (self.model_turns, self.edit_serial)
        if tool_request.tool == "read_file":
            path = tool_request.args.get("path")
            if isinstance(path, str):
                self.files_read.add(path)

    def _repeat_of(self, tool_request) -> int | None:
        """The turn of an identical earlier request whose result is still true.

        libbpp-qt issued the same request 11 times. A listing or a log search
        cannot change; a file read changes only if that file was edited; any
        other result may change with any edit.
        """
        try:
            entry = self.tool_cache.get(self._request_key(tool_request))
        except (TypeError, ValueError):
            return None
        if entry is None:
            return None
        turn, serial = entry
        if not self._still_shown(tool_request):
            return None
        if tool_request.tool in _EDIT_PROOF:
            return turn
        if tool_request.tool == "read_file":
            path = tool_request.args.get("path")
            return turn if self.path_serial.get(path, 0) <= serial else None
        return turn if self.edit_serial == serial else None

    def _still_shown(self, tool_request) -> bool:
        """Whether the earlier result of this request is among the results the model is
        still shown; a stub pointing at a result it can no longer see would hide it for good."""
        key = self._request_key(tool_request)
        distinct: dict[tuple, bool] = {}
        for result in self.tool_results:
            if result.text.startswith(STUB_PREFIX):
                continue
            k = self._request_key(result.request)
            distinct.pop(k, None)
            distinct[k] = True
        return key in list(distinct)[-SHOWN_TOOL_RESULTS:]

    def _after_all_fixed(self, requests) -> list:
        """The requests still run once every verdict is ok: at most
        ALL_FIXED_TOOL_TURNS tool turns, each only to read a file not yet read."""
        if self.fixed_tool_turns >= ALL_FIXED_TOOL_TURNS:
            return []
        return [r for r in requests if r.tool == "read_file"
                and isinstance(r.args.get("path"), str) and r.args["path"] not in self.files_read]

    def _investigation(self, *, counted: bool) -> None:
        """Soft cap on investigating before the first edit: a nudge at 8 turns,
        a firmer message from 12. Nothing is forced."""
        if not counted:
            self.uncounted_tool_turns += 1
            return
        self.investigation_turns += 1
        if self.investigation_turns == INVESTIGATION_NUDGE_AT:
            self._note(INVESTIGATION_NUDGE)
        elif self.investigation_turns >= INVESTIGATION_FINAL_AT:
            self._note(INVESTIGATION_FINAL.format(n=self.investigation_turns))

    def _resolve_review(self):
        """Approve: the reviewed batch becomes this turn's edit. Returns the
        approved signature, None for an ordinary edit, or False to skip the
        turn (nothing to approve)."""
        turn = self.turn
        if self.verification_pending:
            return self._resolve_output_verification()
        if turn.action == "approve":
            if self.review_turn is None:
                self.feedback = "No pending diff is available to approve. Propose an edit or stop."
                return False
            if (self.output_gate and output_verification.applies(self.review_turn.edits)
                    and self.verified_key != self.review_key):
                self.verification_pending = True
                self.review_events.append({"event": "output_verification_requested",
                                           "signature": self.review_key,
                                           "approval_reason": turn.reason})
                return False
            self.review_repeat_count = 0
            approved_key = self.review_key
            self.review_events.append(
                {"event": "approved", "signature": self.review_key, "reason": turn.reason})
            self.turn = deepcopy(self.review_turn)
            self.review_turn = None
            self.proposal_review = ""
            self.output_verification_feedback = ""
            return approved_key
        if turn.action == "edit" and self.review_turn is not None:
            if edit_identity(turn) == edit_identity(self.review_turn):
                self.review_repeat_count += 1
                self.review_events.append({"event": "repeated_proposal",
                                           "signature": self.review_key,
                                           "count": self.review_repeat_count})
                self.controller_note = (
                    "This exact patch is already pending review; changing the hypothesis "
                    "does not revise it. No files were changed. Inspect evidence with tools, "
                    "approve explicitly only if supported, revise the actual edits, or stop. "
                    f"Unchanged resubmissions: {self.review_repeat_count}/3; at 3 the run "
                    "ends without applying this pending patch.")
                return False
            self.review_repeat_count = 0
            if self.verified_key is not None:
                # A supported assessment applies only to its exact batch/source.
                self.output_verification_feedback = ""
            self.verified_key = None
            self.review_events.append({"event": "revised", "signature": self.review_key})
            self.review_turn = None
            self.proposal_review = ""
        return None

    def _resolve_output_verification(self):
        """Verify before review; support is evidence, never implicit approval."""
        self.verification_pending = False
        checked = output_verification.assessment(self.turn.reason, "stop")
        supported = (self.turn.action == "output_verified"
                     and checked["verdict"] == "supported")
        self.review_events.append({"event": "output_verification",
                                   "signature": self.review_key, "assessment": checked})
        if checked["verdict"] == "invalid_response":
            self.verification_format_failures += 1
            self.verification_format_feedback = (
                "Your previous assessment could not be parsed. Return the complete required "
                "assessment JSON for the SAME pending batch. This is a format correction, "
                "not a request for a supported verdict. Parser feedback (data): "
                + self.turn.reason[:2000])
            self.verification_pending = True
            return False
        self.verification_format_feedback = ""
        if supported:
            self.verified_key = self.review_key
            self.output_verification_feedback = (
                "OUTPUT VERIFICATION SUPPORTS THE CURRENT PENDING BATCH. "
                f"Batch signature: {self.review_key}. "
                "No edit or build occurred. This independent static assessment is evidence, "
                "not build success or permission to apply. Review the exact displayed diff "
                "for correctness and unintended changes. Explicit approval is still required. "
                "If you revise it, the replacement batch must be verified again. "
                "Assessment (quoted data): " + self.turn.reason[:12000])
            return False
        self.verified_key = None
        self.verification_failures += 1
        self.output_verification_feedback = (
            "OUTPUT VERIFICATION WITHHELD APPLICATION. No edit or build occurred. "
            "This is a static model assessment, not build feedback. Inspect missing evidence "
            "with tools, revise the complete pending batch, or stop. Approval alone cannot "
            "bypass this check. This assessment concerns the previously checked batch "
            f"({self.review_key}); a revised batch is not yet verified. Keep its unresolved "
            "output requirement in view while correcting edits or gathering evidence. "
            "Assessment (quoted data): " + self.turn.reason[:12000] +
            f"\nFailed output checks: {self.verification_failures}/3.")
        return False

    # --- an edit batch -------------------------------------------------------

    def _edit_turn(self, approved_key) -> LoopResult | None:
        merged = self._merge_correction()
        if merged is not True:
            return merged
        if not self.turn.edits:
            return self._empty_proposal()
        self.empty_edit_streak = 0
        if approved_key is not None:
            notes = list(self.review_notes)
        elif self.pending_turn is not None:
            notes = list(self.pending_notes)
        else:
            notes = []
        self.review_notes = []
        try:
            snapshot = self._validate(notes)
            if snapshot is None:
                return _CONTINUE
            if self._review(snapshot, notes, approved_key):
                return _CONTINUE
            for edit in self.turn.edits:
                self.tracker.capture_before_edit(edit.path)
        except (OSError, ValueError) as exc:
            return self._capture_failed(exc)
        warnings = self._warnings(snapshot)
        applied = self._apply(snapshot, notes, warnings)
        if isinstance(applied, LoopResult):
            return applied
        return self._build(snapshot, applied)

    def _merge_correction(self):
        """Put a focused correction into the pending batch. Returns True to go
        on with the (merged) batch, or _CONTINUE to ask the model again."""
        self.repeated = False   # the correction repeats an already rejected old_text
        for position, item in enumerate(self.turn.edits, start=1):
            if getattr(item, "_position", None) is None:
                item._position = position   # as the model listed it, for notes
        if self.pending_turn is None:
            return True
        if len(self.turn.edits) > 1:
            self.edit_validation_feedback += (
                "\nYour correction contained multiple edits. Return at most ONE.")
            return _CONTINUE
        merged = deepcopy(self.pending_turn)
        merged.edits[self.pending_index:self.pending_index + 1] = deepcopy(self.turn.edits)
        if not merged.edits:
            self._clear_pending()
            self.edit_validation_feedback = ""
            self.feedback = "The pending batch was explicitly emptied; no files changed."
            return _CONTINUE
        if self.turn.edits:
            item = merged.edits[self.pending_index]
            item._slot = self.pending_slot
            item._position = self.pending_position
            self.repeated = (item.path, item.old_text) in self.rejected_texts
        self.turn = merged
        return True

    def _empty_proposal(self) -> LoopResult | None:
        self.empty_edit_streak += 1
        self.batch_notes = []
        self._record_attempt(0, [])
        if self.empty_edit_streak >= 2:
            return self.finish("no_edits", "Two consecutive edit proposals contained no edits.")
        self.feedback = ("Your edit proposal contained no edits. Investigate further, "
                         "propose a supported change, or return a stop action.")
        return _CONTINUE

    def _validate(self, notes: list[str]):
        """Return an applicable snapshot, or defer/finish this edit batch."""
        snapshot = None
        reason = key = None
        if getattr(self.config, "use_rules_guard", False):
            self._guard(notes)
        while True:
            snapshot, feedback = self._preflight_batch(snapshot, notes)
            self.edit_validation_feedback = feedback
            if not self.turn.edits or not feedback:
                break
            failing = self.turn.edits[self.pending_index]
            key = (failing.path, failing.old_text)
            found = re.search(r"Reason: (\S+)", feedback)
            reason = found.group(1) if found else ""
            if self._retarget(failing, reason, notes):
                continue
            why = self._drop_reason(failing, reason)
            if why is None:
                break
            self._drop_failed_edit(failing, reason, why, notes)
        if self.turn.edits and self.edit_validation_feedback:
            self._defer_batch(notes, reason, key)
            return None
        self._clear_pending()
        if not self.turn.edits:
            self._record_inapplicable_batch(notes)
            return None
        return snapshot

    def _preflight_batch(self, snapshot, notes: list[str]):
        """Check paths, prune redundant edits, then validate anchors and syntax.

        Keep the previous snapshot on early path failure, as the retry loop did
        before extraction. No files are written at this stage.
        """
        worktree, policy = self.context.worktree, self.patch_policy
        expanded, more = expand_macros(
            self.turn.edits, worktree, patterns._log_lines(self.log_path),
            target_arch=str((getattr(self.context, "task_metadata", None) or {}).get("target_arch", "")))
        if more:
            notes.extend(more)
            self.turn = deepcopy(self.turn)
            self.turn.edits = expanded
            if not self.turn.edits:
                return snapshot, "\n".join(more)
        self.pending_index, feedback = missing_prefix_feedback(
            worktree, self.turn.edits, patch_policy=policy)
        if feedback:
            return snapshot, feedback
        self.pending_index, feedback = uncapturable_feedback(
            worktree, self.turn.edits, patch_policy=policy)
        if feedback:
            return snapshot, feedback
        snapshot = CandidateSnapshot.capture(
            worktree, [edit.path for edit in self.turn.edits], patch_policy=policy)
        try:
            missing = len(patterns.missing_symbols(patterns._log_lines(self.log_path)))
        except Exception:
            missing = None
        kept, more, _ = prune_batch(snapshot, self.turn.edits, missing_symbol_count=missing)
        notes.extend(more)
        self.turn = deepcopy(self.turn)
        self.turn.edits = kept
        if not self.turn.edits:
            return snapshot, feedback
        self.pending_index, feedback = focused_feedback(
            snapshot, self.turn.edits, self.turn.hypothesis, missing_symbol_count=missing)
        if feedback:
            return snapshot, feedback
        self.pending_index, feedback = self._syntax_check(snapshot)
        return snapshot, feedback

    def _drop_failed_edit(self, failing, reason: str, why: str,
                          notes: list[str]) -> None:
        """Discard only the exhausted item, retaining the rest of the batch."""
        preview = failing.old_text[:60].replace("\n", "\\n")
        notes.append(
            f"Dropped edit {self.pending_position} ({failing.path}, old_text starting "
            f"{preview!r}) because {why}{' (' + reason + ')' if reason else ''}; "
            "the rest of the batch continued.")
        del self.turn.edits[self.pending_index]
        self.repeated = False
        self.rejected_texts.clear()

    def _defer_batch(self, notes: list[str], reason, key) -> None:
        """Save the batch for one focused model correction, without applying it."""
        if notes:
            self.edit_validation_feedback += "\n\nBefore this check: " + " ".join(notes)
        self.pending_turn = deepcopy(self.turn)
        self.pending_notes = list(notes)
        if reason in _ANCHOR_REASONS:
            # New-text errors can be fixed with the same old-text anchor.
            self.rejected_texts.add(key)

    def _record_inapplicable_batch(self, notes: list[str]) -> None:
        """Charge an empty batch as an attempt so it cannot retry indefinitely."""
        self._record_attempt(0, [])
        self.edit_validation_feedback = ""
        self.batch_notes = notes + ["No edit of that batch could be applied, so no "
                                    "file changed. Propose a new batch or stop."]
        self.feedback = " ".join(self.batch_notes)

    def _syntax_check(self, snapshot):
        """Send back an edit that would leave its file unparseable (syntax_guard).

        Returns (index, feedback), or (None, "") when the batch may be applied.
        Each finding is sent back once; an unchanged resend is applied.
        """
        if not getattr(self.config, "use_syntax_guard", False):
            return None, ""
        seconds = min(syntax_guard.COMPILE_BUDGET, self.syntax_seconds_left,
                      max(0.0, self.work_deadline - self.clock() - 60))
        started = monotonic()
        found = syntax_guard.check_batch(snapshot, self.turn.edits,
                                         worktree=self.context.worktree,
                                         allow=self.syntax_allowed, seconds=seconds,
                                         target_arch=str((getattr(self.context, "task_metadata",
                                                                  None) or {})
                                                         .get("target_arch") or ""),
                                         log_path=self.log_path)
        self.syntax_seconds_left = max(0.0, self.syntax_seconds_left - (monotonic() - started))
        if found is None:
            return None, ""
        index, problems, after = found
        edit = self.turn.edits[index]
        self.syntax_allowed.add(syntax_guard.edit_signature(edit))
        self.review_events.append({"event": "syntax_guard", "path": edit.path,
                                   "problems": [p.message for p in problems]})
        return index, syntax_guard.feedback(index, self.turn.edits, problems, after,
                                            self.turn.hypothesis)

    def _guard(self, notes: list[str]) -> None:
        """Drop each edit the rules guard refuses, with its reason (rules_guard)."""
        kept = []
        for position, edit in enumerate(self.turn.edits, 1):
            why = rules_guard.check(edit.path, edit.old_text, edit.new_text)
            if why is None:
                kept.append(edit)
            else:
                notes.append(f"Refused edit {position} ({edit.path}): {why}.")
                self.review_events.append({"event": "rules_guard", "path": edit.path,
                                           "reason": why})
        if len(kept) != len(self.turn.edits):
            self.turn = deepcopy(self.turn)
            self.turn.edits = kept

    def _retarget(self, failing, reason: str, notes: list[str]) -> bool:
        """Move an edit to the one file that holds its text (edit_repair)."""
        if reason != "old_text_not_found" or getattr(failing, "_retargeted", False):
            return False
        target = retarget_path(self.context.worktree, failing, patch_policy=self.patch_policy)
        if target is None:
            return False
        notes.append(
            f"Moved edit {getattr(failing, '_position', self.pending_index + 1)} "
            f"from {failing.path} to {target}: its old_text is not in the file it "
            "named and occurs in that one file only.")
        failing.path = target
        failing._retargeted = True
        return True

    def _drop_reason(self, failing, reason: str) -> str | None:
        """Why the failing item is dropped rather than sent back, or None."""
        slot = getattr(failing, "_slot", None)
        if slot is None:
            slot = next(self.slot_ids)
            failing._slot = slot
        if slot != self.pending_slot:
            # A different item: it gets corrections of its own, but not a
            # whole new budget after one item has used it up.
            if self.pending_slot is not None:
                self.correction_responses = min(
                    self.correction_responses, self.MAX_CORRECTIONS - 3)
            self.pending_slot = slot
            self.pending_position = getattr(failing, "_position", self.pending_index + 1)
            self.rejected_texts.clear()
            self.repeated = False
        if self.repeated:
            return "its correction repeated an old_text that was already rejected"
        if self.correction_responses >= self.MAX_CORRECTIONS:
            return "the correction budget was spent on it"
        return None

    def _warnings(self, snapshot) -> list[str]:
        originals = {saved.path: saved.content.decode("utf-8") for saved in snapshot.files}
        warnings = []
        for path, text in simulate(snapshot, self.turn.edits).items():
            warnings.extend(static_warnings(path, originals.get(path, ""), text))
        return warnings

    def _review(self, snapshot, notes: list[str], approved_key) -> bool:
        """Hold the batch for the model's approval; True when it is held."""
        if not self.review_edits:
            return False
        current_key = signature(snapshot, self.turn)
        if approved_key == current_key:
            return False
        diagnostics = (self.best_build.diagnostics if self.best_build is not None
                       else (self.initial_diagnostics or []))
        self.proposal_review, counts = render_review(
            snapshot, self.turn, diagnostics, patch_policy=self.patch_policy,
            historical=bool(self.attempts) and self.best_build is None)
        if self.ffi is not None:
            candidate = ffi_analysis.index_sources(self.context.worktree,
                                                   simulate(snapshot, self.turn.edits))
            self.proposal_review += '\n\n' + self.ffi.render(candidate, 'SIMULATED proposed source')
        self.review_turn = deepcopy(self.turn)
        self.review_key = current_key
        self.review_notes = notes
        self.review_events.append({"event": "proposed", "signature": current_key,
                                   "counts": counts, "feedback": self.proposal_review})
        if self.output_gate and output_verification.applies(self.review_turn.edits):
            self.verified_key = None
            self.verification_pending = True
            self.review_events.append({"event": "output_verification_requested",
                                       "signature": current_key, "phase": "before_review"})
        return True

    def _capture_failed(self, exc: Exception) -> None:
        self.feedback = f"The edit batch was not applied because its files could not be captured: {exc}"
        self.batch_notes = []
        self._record_attempt(0, [])
        self._clear_pending()
        self.edit_validation_feedback = (
            "=== Edit batch validation failed ===\n"
            "NO edits were written and NO build was requested.\n" + self.feedback)
        return _CONTINUE

    def _apply(self, snapshot, notes: list[str], warnings: list[str]):
        """Write the batch and record the attempt; an int, or a LoopResult."""
        if self._out_of_time():
            return self.finish("time_budget", "No time remains to apply the edit batch.")
        applied = apply_edits(self.context.worktree, self.turn.edits, patch_policy=self.patch_policy)
        if self.review_edits and applied != len(self.turn.edits):
            try:
                snapshot.restore()
            except (OSError, ValueError, RuntimeError) as exc:
                return self.finish("restoration_error", f"Review batch restore failed: {exc}")
            return self.finish("edit_application_error",
                               "Reviewed batch was not fully applied; restored files and skipped build.")
        self.feedback = f"Accepted {applied} of {len(self.turn.edits)} proposed edit operations."
        self.batch_notes = notes + [f"Warning (the edit was applied): {w}" for w in warnings]
        self.prediction = self._predict(snapshot) if applied else ""
        completed = self._complete_symbols(snapshot, applied)
        if completed:
            self.batch_notes += completed
            self.prediction = self._predict(snapshot)
        if self.batch_notes:
            self.feedback += " " + " ".join(self.batch_notes)
        # Record the attempt before requesting a build.
        self._record_attempt(applied, list(self.turn.edits))
        if self.ffi is not None:
            current = ffi_analysis.index_sources(self.context.worktree)
            self.feedback += '\n' + self.ffi.render(current, 'actual applied source')
            self.review_events.append({'event': 'ffi_applied',
                'coverage': self.ffi.report(current, 'actual applied source')})
        # Drop only the tool results the edit may have made outdated.
        self.tool_results[:] = _still_valid(self.tool_results, {e.path for e in self.turn.edits})
        self._touch({e.path for e in self.turn.edits})
        self.investigation_turns = 0
        return applied

    def _predict(self, snapshot) -> str:
        """verify.render for the batch just applied, in a run with no build
        feedback: the edited files evaluated under the target's facts."""
        self.all_fixed, self.fixed_tool_turns, self.fixed_refusals = False, 0, 0
        self.prediction_status = self.prediction_reason = ""
        if self.config.use_build_feedback and self.build_feedback is not None:
            return ""
        if self.target is None or not self.initial_diagnostics:
            return ""
        try:
            originals = {saved.path: saved.content.decode("utf-8") for saved in snapshot.files}
            verdicts = verify.predict(
                self.context.worktree, self.initial_diagnostics, self.target,
                changed={edit.path for edit in self.turn.edits}, originals=originals,
                log_lines=patterns._log_lines(self.log_path))
            self._summarise(verdicts)
            return verify.render(verdicts, self.target)
        except Exception:  # a prediction must never end a run
            self.all_fixed = False
            return ""

    def _summarise(self, verdicts) -> None:
        """The prediction in one word and one reason; arms the all-fixed stop gate."""
        if verdicts and all(v.ok is True for v in verdicts):
            self.all_fixed = True
            self.prediction_status = "fixed"
            self.prediction_reason = "every construct the errors came from is predicted fixed"
            return
        failing = next((v for v in verdicts if v.ok is False), None)
        if failing is not None:
            self.prediction_status = "not_fixed"
            self.prediction_reason = failing.text
            return
        unclear = next((v for v in verdicts if v.ok is None), None)
        self.prediction_status = "unknown"
        self.prediction_reason = unclear.text if unclear else "no static check covers this edit"

    def _build(self, snapshot, applied: int) -> LoopResult | None:
        """Build if feedback is available, keep or restore the candidate."""
        attempt = self.attempts[-1]
        build = None
        build_error = None
        expired_before_build = self._out_of_time()
        if (not expired_before_build and applied > 0 and self.config.use_build_feedback
                and self.build_feedback is not None):
            self.build_calls += 1
            try:
                build = self.build_feedback.request_build(self.context.worktree)
            except (OSError, RuntimeError, ValueError) as exc:
                build_error = f"{type(exc).__name__}: {exc}"
            else:
                attempt.build = build
        restored = False
        if applied > 0:
            should_restore = self.best_build is not None and (
                build is None or _build_rank(build) < _build_rank(self.best_build))
            if should_restore:
                try:
                    if snapshot is None:
                        raise RuntimeError("Missing pre-edit snapshot.")
                    snapshot.restore()
                except (OSError, ValueError, RuntimeError) as exc:
                    return self.finish("restoration_error",
                                       f"Could not fully restore the retained candidate: {exc}")
                restored = True
                self._touch({e.path for e in attempt.edits})
            else:
                self.kept_attempts.append(attempt.number)
                self.best_build = deepcopy(build)
        if expired_before_build:
            return self.finish("time_budget", "The working deadline was reached; no build was "
                               "started and candidate selection has completed.")
        if build_error is not None:
            return self.finish("build_error", f"Build feedback failed: {build_error}")
        return self._attempt_feedback(build, restored, applied)

    def _attempt_feedback(self, build, restored: bool, applied: int) -> LoopResult | None:
        """Tell the model what the attempt did; stop on a repeated diagnostic."""
        if build is None:
            self.feedback += ("\nNo build result is available for this attempt; "
                              "the candidate has not been validated.")
        elif build.succeeded:
            return self.finish("build_succeeded", "The build-feedback adapter reported success.")
        else:
            messages = "\n".join(d.message for d in build.diagnostics)
            self.feedback += (f"\nBuild failed. Reported stage: {build.stage_reached}."
                              f"\nDiagnostics:\n{messages or 'No diagnostics returned.'}")
        if restored:
            self.feedback += ("\nThis attempt was undone. The worktree now contains "
                              "the previously retained candidate. The diagnostics above "
                              "describe the discarded attempt, not the restored files.")
        if self._repeated_diagnostic(build, applied):
            return self.finish("repeated_diagnostic",
                               "Two consecutive changed attempts reported the same leading "
                               "diagnostic without improvement in the build-ranking heuristic.")
        self.latest_attempt_feedback = self.feedback
        self.feedback = ""
        return _CONTINUE

    def _repeated_diagnostic(self, build, applied: int) -> bool:
        if not (applied > 0 and build is not None and not build.succeeded
                and build.diagnostics and len(self.attempts) >= 2):
            return False
        previous = self.attempts[-2]
        previous_build = previous.build
        if not (previous.applied > 0 and previous_build is not None
                and not previous_build.succeeded and previous_build.diagnostics):
            return False
        current, before = build.diagnostics[0], previous_build.diagnostics[0]
        same = ((current.message, current.log_path, current.line)
                == (before.message, before.log_path, before.line))
        return same and _build_rank(build) <= _build_rank(previous_build)
