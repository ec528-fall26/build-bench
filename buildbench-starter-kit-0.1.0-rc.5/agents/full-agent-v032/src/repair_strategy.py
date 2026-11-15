"""Opt-in small-group repair prompts; no changes to edit application."""
from copy import copy
import os

INSTRUCTIONS = """
Experimental small-group repair workflow
- Your immediate task is the selected error group, not every error in the package.
- Propose at most three exact replacement edits addressing this group.
- Prefer a coherent fix for related causes; do not blindly cast every pointer.
- Preserve portability and existing behavior.
- Use the exact resolved input/ path shown in the evidence.
- Copy old_text exactly and include enough surrounding text to make it unique.
- Complete source may already be supplied. Read more only for specific missing evidence.
- Do not claim a tool failed unless an actual tool result reports failure.
- A partial repair is useful: a rebuild will determine what remains.
- Success still requires a successful build; do not claim package repair from an edit alone.
"""



SHARED_CAUSE_INSTRUCTIONS = """
Experimental shared-cause repair workflow
- Consider the available diagnostics across files before choosing a repair.
- Form a hypothesis for a common cause supported by source and compiler evidence.
- Similar error messages alone do not prove a shared cause.
- Inspect shared definitions, interfaces, producers, and consumers when relevant.
- Prefer a consistent repair of the underlying contract over repeated local workarounds.
- Preserve platform portability, public behavior, and tests.
- Address related affected locations across files when evidence supports doing so.
- There is no three-edit limit in this strategy. Keep the batch coherent and justified.
- Do not change unrelated code or perform blind global substitutions.
- Use exact resolved input/ paths, including the input/ prefix.
- Copy old_text exactly with unique surrounding context; avoid duplicate edits.
- Complete source may already be supplied. Request tools only for missing evidence.
- Read additional affected files when they are not included in the supplied source.
- Source and diagnostic displays can be bounded; do not assume omitted content was read.
- Explain the shared cause and the scope of the proposed repair in the hypothesis.
- A valid edit is not proof of repair; success requires the build validator.
"""


def instructions_for(strategy):
    if strategy == "shared-cause":
        return SHARED_CAUSE_INSTRUCTIONS
    if strategy == "small-group":
        return INSTRUCTIONS
    return ""


def configured_strategy():
    value = os.environ.get("BB_REPAIR_STRATEGY", "default")
    if value not in {"default", "small-group", "shared-cause"}:
        raise ValueError("BB_REPAIR_STRATEGY must be default, small-group, or shared-cause")
    return value


def select_group(diagnostics):
    """First three diagnostics for the first uniquely resolved source file.

    This groups by location, not by a proven common cause.
    """
    for diagnostic in diagnostics:
        if len(diagnostic.resolved) == 1:
            path = diagnostic.resolved[0]
            return [
                item for item in diagnostics
                if item.resolved == [path]
            ][:3]
    return list(diagnostics[:3])


def opening_inputs(request, initial_diagnostics, initial_log, *, strategy="small-group"):
    """Use retained-build errors, without mixing them with the initial log."""
    choose = list if strategy == "shared-cause" else select_group
    if request.current_build is not None:
        context = copy(request.context)
        context.log_tail = ""
        diagnostics = request.current_build.diagnostics
        return context, choose(diagnostics), None, len(diagnostics)

    # If edits have occurred without a retained build, initial errors are
    # historical evidence rather than verified errors in the current files.
    if request.latest_attempt is not None:
        context = copy(request.context)
        context.log_tail = ""
        return context, choose(initial_diagnostics), None, len(initial_diagnostics)

    return (
        request.context, choose(initial_diagnostics),
        initial_log, len(initial_diagnostics),
    )


def attempt_summary(attempt, current_build):
    if attempt is None:
        return ""
    text = (
        f"Latest attempt {attempt.number}: applied {attempt.applied} "
        f"of {len(attempt.edits)} edits."
    )
    if attempt.applied == 0:
        return text + " No edits from that attempt were applied."
    if attempt.build is None:
        return text + " No build result is available; do not assume success."
    if current_build != attempt.build:
        return (
            text + " That candidate was not retained. Use current source and "
            "the retained-build diagnostics shown above."
        )
    return text + " Its candidate was retained; use the updated errors shown above."
