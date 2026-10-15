"""Connects Part C's loop to the model: the loop's next_turn callback.

Each turn is one model call. The request is rebuilt from the loop's snapshot
(LoopRequest), not carried over as a growing conversation: with use_history
off, an accumulated conversation would still show the model every earlier
attempt and the history-off arm of Demo 3 would measure nothing. What the
model sees each turn, all worded by Part E (prompts.py):

    system   the instructions for the enabled capabilities
    user     the case evidence, with the repair history when use_history is on
             the result of the latest edit, with the new build's errors
             why the previous reply was malformed, if it was
             the results of tool requests made since the latest edit
             what is left of the budget

Replies are parsed by Part E (reply.py). A reply with no JSON action at all
(only reasoning, or reasoning and a sentence such as "Let's search.") gets one
follow-up in the same turn: the reply is sent back and only the JSON object is
asked for. gpt-oss-120b did this on 6 of 11 turns in the first two live rcran
runs and 2 of 19 on ectrans (docs/part5-findings.md), always right after
deciding what to do; with temperature 0 and a near-identical request, asking
again from scratch repeated it. A reply whose JSON is wrong is not followed up:
it gets Part E's explanation on the next turn.

Standard library only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from time import monotonic
from typing import Callable

from . import prompts
from .contracts import AgentConfig, Budget, BuildResult, Diagnostic, ModelTurn
from .edit_applier import LOGGER as EDIT_LOGGER
from .loop import LoopRequest
from .model_client import ModelCallFailed, request_completion
from .reply import extract_action_json, parse_turn
from .types import CaseContext


# (reply text, finish_reason, {"input_tokens", "output_tokens"})
Completion = tuple[str, "str | None", dict]
Complete = Callable[[list[dict]], Completion]

MAX_TOOL_RESULTS = 8  # most recent results shown; older ones are counted
FOLLOW_UP = ("Your reply ended without the JSON object. "
             "Reply now with only the JSON object for the action you decided on.")


def client_completion(client) -> Complete:
    """One chat completion through Part 3's ModelClient settings.

    Failures raise RuntimeError, which the loop records as model_error.
    """
    def complete(messages: list[dict]) -> Completion:
        client.served_model = None
        if not client.api_key:
            raise RuntimeError("no model API key configured (set BB_MODEL_API_KEY)")
        try:
            body = request_completion(client.base_url, client.api_key, messages,
                                      opener=client.opener, sleep=client.sleep,
                                      max_attempts=client.max_attempts, timeout=client.timeout)
        except (ModelCallFailed, ValueError) as error:
            raise RuntimeError(f"model call failed: {error}") from None
        body = body if isinstance(body, dict) else {}
        served = body.get("model")
        client.served_model = served if isinstance(served, str) else None
        counts = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        usage = {"input_tokens": counts.get("prompt_tokens"),
                 "output_tokens": counts.get("completion_tokens")}
        try:
            choice = body["choices"][0]
            text, finish = choice["message"]["content"], choice.get("finish_reason")
        except (KeyError, IndexError, TypeError):
            text, finish = "", None
        return (text if isinstance(text, str) else ""), finish, usage

    return complete


class _Rejections(logging.Handler):
    """Part 4 only logs why an edit was rejected; collect it for the model."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        message = record.getMessage()
        if message.startswith("edit_rejected"):
            self.messages.append(message)


def _first_error(build: BuildResult | None) -> tuple | None:
    if build is None or build.succeeded or not build.diagnostics:
        return None
    first = build.diagnostics[0]
    return first.message, first.log_path, first.line


def _distinct(results: list) -> tuple[list, int]:
    """Each identical request once (its latest result), and how many repeats."""
    latest = {}
    for result in results:
        key = (result.request.tool, json.dumps(result.request.args, sort_keys=True, default=str))
        latest.pop(key, None)
        latest[key] = result
    return list(latest.values()), len(results) - len(latest)


def _no_action(text: str) -> bool:
    """A non-empty reply in which no JSON action object can be found."""
    if not text.strip():
        return False
    try:
        extract_action_json(text)
    except ValueError:
        return True
    return False


def _add(first: dict, second: dict) -> dict:
    """Token counts of two calls; unknown if either is unknown, never 0."""
    total = {}
    for name in ("input_tokens", "output_tokens"):
        a, b = first.get(name), second.get(name)
        total[name] = a + b if type(a) is int and type(b) is int else None
    return total


def _body(text: str) -> str:
    """A Part E message without its closing "decide what to do next" line."""
    return text.rstrip("\n").removesuffix(prompts._NEXT).rstrip("\n")


class ModelAdapter:
    """Part C's next_turn: (LoopRequest) -> (ModelTurn, usage)."""

    def __init__(self, complete: Complete, context: CaseContext, *,
                 config: AgentConfig, budget: Budget, diagnostics: list[Diagnostic],
                 log_path: Path | None, package: str | None = None,
                 clock: Callable[[], float] = monotonic):
        self.complete = complete
        self.context = context
        self.config = config
        self.budget = budget
        self.diagnostics = diagnostics
        self.log_path = log_path
        self.package = package
        self.clock = clock
        self.started = clock()
        self.turns: list[dict] = []    # one summary per model call, for the run record
        self.replies: list[dict] = []  # raw replies {turn, follow_up, reply}, for the harness
        self._last_turn: ModelTurn | None = None
        self._seen_attempt = 0
        self._builds = 0
        self._previous_error = None
        self._attempt_note = ""
        self._tokens: int | None = 0
        self._rejections = _Rejections()
        EDIT_LOGGER.addHandler(self._rejections)

    def close(self) -> None:
        EDIT_LOGGER.removeHandler(self._rejections)

    def __call__(self, request: LoopRequest) -> tuple[ModelTurn, dict]:
        messages = self.messages(request)
        started = self.clock()
        text, finish, usage = self.complete(messages)
        turn = parse_turn(text, finish_reason=finish, allow_tools=self.config.use_tools)
        follow_up = turn.action == "malformed" and finish != "length" and _no_action(text)
        number = len(self.turns) + 1
        if follow_up:
            self.replies.append({"turn": number, "follow_up": False, "reply": text})
            text, finish, more = self.complete(messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": FOLLOW_UP}])
            usage = _add(usage, more)
            turn = parse_turn(text, finish_reason=finish, allow_tools=self.config.use_tools)
        self._last_turn = turn
        counts = [usage.get("input_tokens"), usage.get("output_tokens")]
        if self._tokens is not None and all(type(c) is int for c in counts):
            self._tokens += sum(counts)
        else:
            self._tokens = None
        self.replies.append({"turn": number, "follow_up": follow_up, "reply": text})
        self.turns.append({
            "turn": number, "action": turn.action,
            "tools": [r.tool for r in turn.tool_requests], "edits": len(turn.edits),
            "reason": turn.reason, "finish_reason": finish, "usage": dict(usage),
            "follow_up": follow_up,
            "seconds": round(self.clock() - started, 1),
        })
        return turn, usage

    def messages(self, request: LoopRequest) -> list[dict]:
        config = self.config
        parts = [_body(prompts.opening_message(
            request.context, config, diagnostics=self.diagnostics, log_path=self.log_path,
            package=self.package, history=request.history,
            kept=request.kept_attempts if config.use_history else None))]

        latest = request.latest_attempt
        if latest is not None and latest.number != self._seen_attempt:
            self._new_attempt(latest, request.current_build)
        if latest is not None:
            parts.append(self._attempt_note)
        if self._last_turn is not None and self._last_turn.action == "malformed":
            parts.append(_body(prompts.malformed_message(self._last_turn, config)))
        if request.tool_results:
            distinct, repeats = _distinct(request.tool_results)
            shown = distinct[-MAX_TOOL_RESULTS:]
            hidden = len(distinct) - len(shown)
            notes = []
            if repeats:
                notes.append(f"{repeats} of your requests repeated an earlier one and "
                             f"{'are' if repeats != 1 else 'is'} shown once. A repeated request "
                             "returns the same result: try something different instead.")
            if hidden:
                notes.append(f"{hidden} earlier tool result{'s' * (hidden != 1)} not shown again.")
            note = " ".join(notes)
            parts.append("=== Results of your tool requests ===\n"
                         + _body(prompts.tool_results_message(shown, note=note)))
        parts.append(prompts.budget_note(
            self.budget, attempts_used=latest.number if latest else 0,
            builds_used=self._builds, tokens_used=self._tokens,
            seconds_used=self.clock() - self.started))
        parts.append(prompts._NEXT)
        return [
            {"role": "system", "content": prompts.system_prompt(config, self.budget)},
            {"role": "user", "content": "\n\n".join(p for p in parts if p.strip()) + "\n"},
        ]

    def _new_attempt(self, attempt, current_build: BuildResult | None) -> None:
        """Word the result of an attempt the model has not been told about yet."""
        self._seen_attempt = attempt.number
        rejections = self._rejections.messages[:]
        self._rejections.messages.clear()
        if attempt.build is not None:
            self._builds += 1
        error = _first_error(attempt.build)
        same = error is not None and error == self._previous_error
        if error is not None:
            self._previous_error = error
        # The loop keeps the better candidate: if this attempt's build is not
        # the one retained, its edits were undone.
        undone = attempt.applied > 0 and current_build != attempt.build
        self._attempt_note = _body(prompts.attempt_result_message(
            attempt, rejections=rejections, undone=undone, same_first_error=same))
