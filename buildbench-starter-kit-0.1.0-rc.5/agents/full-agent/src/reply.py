"""Part E: turn the model's reply text into a ModelTurn.

The format was decided on 7 October (docs/full-agent.md, "Model reply format"):
ordinary text that ends in one JSON object with an "action" key. This is the one
layer that knows that format. Parts B and C work with ModelTurn and ToolRequest,
so if the organizers confirm native tool calling only this file changes.

Parsing is tolerant where the model's intent is unambiguous and nothing is lost
(prose around the JSON, drafts, reasoning blocks, code fences, a single object
where a list was asked for, a literal newline inside a string). It is strict
where a guess could apply the wrong change: every edit needs a path, the exact
text to replace and its replacement. Anything else becomes
ModelTurn(action="malformed", reason=...), with a reason written for the model
to read (prompts.malformed_message). This function never raises.

Standard library only.
"""

from __future__ import annotations

import json
import re

from .contracts import ModelTurn, ToolRequest
from .tools import TOOLS
from .types import Edit


ACTIONS = ("tool", "edit", "stop")
CUT_OFF = "length"  # finish_reason when the reply hit the output limit

# Blocks some models think aloud in. Stripping them is tolerance, not a format
# we ask for: the prompts never mention these tags.
_THINKING = re.compile(r"<(reasoning|think|thinking)>.*?</\1>", re.IGNORECASE | re.DOTALL)
# Arguments Part B requires as integers; a model may send "40" or 40.0.
_INTEGER_ARGS = {"read_file": ("start_line", "end_line")}


class _Malformed(ValueError):
    pass


def _turn(action: str, *, requests=None, hypothesis: str = "", evidence=None,
          edits=None, reason: str = "") -> ModelTurn:
    return ModelTurn(action, requests or [], hypothesis, evidence or [], edits or [], reason)


def extract_action_json(text: str) -> dict:
    """Return the last JSON object with an "action" key, ignoring what surrounds it.

    Objects nested inside an accepted one are skipped, so a tool argument or a
    file's content can never be mistaken for the reply. With no "action" object
    at all, the last object with an "edits" key is returned: the baseline's
    format, which a model may fall back to.
    """
    text = _THINKING.sub("", text)
    # strict=False accepts a literal newline or tab inside a string and keeps it.
    decoder = json.JSONDecoder(strict=False)
    found = legacy = None
    resume = 0
    for match in re.finditer(r"\{", text):
        if match.start() < resume:
            continue
        try:
            value, end = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if "action" in value:
            found, resume = value, end
        elif "edits" in value:
            legacy, resume = value, end
    if found is not None:
        return found
    if legacy is not None:
        return legacy
    raise ValueError('no JSON object with an "action" key was found')


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _requests(data: dict) -> list[ToolRequest]:
    items = data.get("requests")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        raise _Malformed('a "tool" reply needs a non-empty "requests" list')
    requests = []
    for number, item in enumerate(items, start=1):
        name = item.get("tool") if isinstance(item, dict) else None
        if not isinstance(name, str) or name.strip() not in TOOLS:
            raise _Malformed(f"request {number} names an unknown tool ({name!r}); "
                             f"the tools are {', '.join(TOOLS)}")
        name = name.strip()
        args = item.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise _Malformed(f'request {number} ({name}): "args" must be a JSON object')
        args = dict(args)
        for key in _INTEGER_ARGS.get(name, ()):
            value = args.get(key)
            if isinstance(value, str) and value.strip().isdigit():
                args[key] = int(value)
            elif isinstance(value, float) and value.is_integer():
                args[key] = int(value)
        requests.append(ToolRequest(name, args))
    return requests


def _edits(data: dict) -> list[Edit]:
    items = data.get("edits")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise _Malformed('an "edit" reply needs an "edits" list')
    edits = []
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise _Malformed(f"edit {number} must be a JSON object")
        for field in ("path", "old_text", "new_text"):
            if not isinstance(item.get(field), str):
                raise _Malformed(f'edit {number}: "{field}" must be a string')
        if not item["path"].strip() or not item["old_text"]:
            raise _Malformed(f'edit {number}: "path" and "old_text" must not be empty')
        replace_all = item.get("replace_all", False)
        if type(replace_all) is not bool:
            raise _Malformed(f'edit {number}: "replace_all" must be a JSON boolean')
        edits.append(Edit(item["path"].strip(), item["old_text"], item["new_text"], replace_all))
    return edits


def _evidence(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value
            if isinstance(item, (str, int, float)) and not isinstance(item, bool)
            and str(item).strip()]


def _edit_turn(data: dict, hypothesis: str) -> ModelTurn:
    edits = _edits(data)
    if not hypothesis:
        raise _Malformed('an "edit" reply must state a "hypothesis": one sentence saying '
                         'what is wrong and where. To give up instead, reply with "stop"')
    return _turn("edit", hypothesis=hypothesis, evidence=_evidence(data.get("evidence")),
                 edits=edits)


def _parse(data: dict, allow_tools: bool, allow_review: bool = False) -> ModelTurn:
    if "action" not in data:
        # The baseline's {"edits": [...], "rationale": "..."}; an empty list meant
        # "no supported repair".
        rationale = _text(data.get("rationale"))
        if data.get("edits") == []:
            return _turn("stop", reason=rationale)
        return _edit_turn(data, _text(data.get("hypothesis")) or rationale)

    action = data["action"].strip().lower() if isinstance(data["action"], str) else None
    offered = (ACTIONS if allow_tools else ACTIONS[1:]) + (("approve",) if allow_review else ())
    if action == "approve":
        if not allow_review:
            raise _Malformed('approve is only available while reviewing a pending diff')
        if data.get("edits") or data.get("requests"):
            raise _Malformed('approve cannot include edits or tool requests')
        reason = _text(data.get("reason"))
        if not reason:
            raise _Malformed('approve needs a reason explaining the review')
        return _turn("approve", reason=reason)
    if action == "tool" and not allow_tools:
        choices = '"edit", "approve", or "stop"' if allow_review else '"edit" or "stop"'
        raise _Malformed('tools are not available in this run; reply with ' + choices)
    if action == "tool":
        # reason carries the model's "why" for the run record.
        return _turn("tool", requests=_requests(data), reason=_text(data.get("why")))
    if action == "edit":
        return _edit_turn(data, _text(data.get("hypothesis")) or _text(data.get("rationale")))
    if action == "stop":
        return _turn("stop", reason=_text(data.get("reason")))
    raise _Malformed(f'"action" must be one of {", ".join(offered)}; got {data["action"]!r}')


def parse_turn(text, *, finish_reason: str | None = None, allow_tools: bool = True, allow_review: bool = False) -> ModelTurn:
    """Parse one reply. ``finish_reason`` is the endpoint's, when it gave one.

    Pass ``allow_tools=False`` when AgentConfig.use_tools is off, so a tool
    request is answered with an explanation instead of being run.
    """
    if not isinstance(text, str) or not text.strip():
        return _turn("malformed", reason="the reply had no text")
    try:
        data = extract_action_json(text)
    except ValueError as error:
        if finish_reason == CUT_OFF:
            return _turn("malformed", reason="the reply was cut off at the output limit "
                         "before its JSON object was complete; write less before the JSON")
        return _turn("malformed", reason=str(error))
    try:
        return _parse(data, allow_tools, allow_review)
    except _Malformed as error:
        return _turn("malformed", reason=str(error))
