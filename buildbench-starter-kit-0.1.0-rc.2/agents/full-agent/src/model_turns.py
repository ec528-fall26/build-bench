"""Parse full-agent model replies into the shared ModelTurn contract."""

from __future__ import annotations

import json
import re

from .contracts import ModelTurn, ToolRequest
from .tools import TOOLS
from .types import Edit


_REASONING = re.compile(
    r"<reasoning>.*?</reasoning>",
    re.IGNORECASE | re.DOTALL,
)


def _malformed(reason: str) -> ModelTurn:
    return ModelTurn(
        action="malformed",
        tool_requests=[],
        hypothesis="",
        evidence=[],
        edits=[],
        reason=reason,
    )


def _extract_action_object(text: str) -> dict:
    """Find the last action object outside reasoning blocks."""
    text = _REASONING.sub("", text)
    decoder = json.JSONDecoder()
    found = None
    position = 0

    while position < len(text):
        start = text.find("{", position)
        if start == -1:
            break

        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            position = start + 1
            continue

        if isinstance(value, dict) and "action" in value:
            found = value

        position = end

    if found is None:
        raise ValueError("No JSON object with an 'action' field was found.")

    return found


def parse_model_turn(text: str) -> ModelTurn:
    """Return a structured turn, including an explanation for invalid replies."""
    if not isinstance(text, str):
        return _malformed("The model reply must be text.")

    try:
        data = _extract_action_object(text)
    except ValueError as exc:
        return _malformed(str(exc))

    action = data.get("action")

    if action == "stop":
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return _malformed("A stop reply must include a non-empty reason.")

        return ModelTurn(
            action="stop",
            tool_requests=[],
            hypothesis="",
            evidence=[],
            edits=[],
            reason=reason,
        )

    if action == "tool":
        requests = data.get("requests")
        why = data.get("why")

        if not isinstance(requests, list) or not requests:
            return _malformed(
                "A tool reply must include a non-empty requests list."
            )
        if not isinstance(why, str) or not why.strip():
            return _malformed(
                "A tool reply must include a non-empty why field."
            )

        parsed_requests = []
        for index, request in enumerate(requests):
            if not isinstance(request, dict):
                return _malformed(
                    f"Tool request {index} must be an object."
                )

            tool = request.get("tool")
            args = request.get("args")

            if not isinstance(tool, str) or tool not in TOOLS:
                return _malformed(
                    f"Tool request {index} names an unknown tool: {tool!r}."
                )
            if not isinstance(args, dict):
                return _malformed(
                    f"Tool request {index} must include an args object."
                )

            parsed_requests.append(ToolRequest(tool=tool, args=args))

        return ModelTurn(
            action="tool",
            tool_requests=parsed_requests,
            hypothesis="",
            evidence=[],
            edits=[],
            reason=why,
        )

    if action == "edit":
        hypothesis = data.get("hypothesis")
        evidence = data.get("evidence")
        edits = data.get("edits")

        if not isinstance(hypothesis, str) or not hypothesis.strip():
            return _malformed(
                "An edit reply must include a non-empty hypothesis."
            )
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) for item in evidence
        ):
            return _malformed(
                "An edit reply must include an evidence list of strings."
            )
        if not isinstance(edits, list):
            return _malformed(
                "An edit reply must include an edits list."
            )

        parsed_edits = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                return _malformed(
                    f"Edit {index} must be an object."
                )

            for field in ("path", "old_text", "new_text"):
                if not isinstance(edit.get(field), str):
                    return _malformed(
                        f"Edit {index} must include a string {field}."
                    )

            if not edit["path"] or not edit["old_text"]:
                return _malformed(
                    f"Edit {index} must have non-empty path and old_text."
                )

            parsed_edits.append(
                Edit(
                    path=edit["path"],
                    old_text=edit["old_text"],
                    new_text=edit["new_text"],
                )
            )

        return ModelTurn(
            action="edit",
            tool_requests=[],
            hypothesis=hypothesis,
            evidence=evidence,
            edits=parsed_edits,
            reason="",
        )
    return _malformed(f"Unknown action: {action!r}.")
