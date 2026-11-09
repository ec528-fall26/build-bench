"""Controller-side help once the model has started a mechanical repair.

Evidence (live run of 0.6.0-rc.4, libbpp-core): the model hand-tagged 11 of 12
missing symbols and stopped, one line short. Finishing that last line is not a
judgement call: the symbols-tag recipe computes it from the log and the file.
This module computes the recipe's edits for exactly the lines that are still
untagged and the recipe target for a stop bounce; it never writes anything and
never raises. loop.py applies the edits through the normal gates.

Standard library only.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

RECIPE = "symbols-tag"
_TAG_MARKERS = ("(arch=", "(optional", "(arch-bits")


def _untagged(line: str) -> bool:
    """The definition verify._symbols uses for a symbol line still without a tag."""
    return not any(marker in line for marker in _TAG_MARKERS)


def symbols_completion(worktree, log_lines, target_arch: str, changed) -> tuple[list, list[str]]:
    """(edits, names): the symbols-tag edits for the reported-missing lines of the
    symbols file the log names that are still untagged, when the model has
    edited that file. ([], []) in every other case; only tag insertions."""
    try:
        from .recipes import registry
        recipe = registry.get(RECIPE)
        if recipe is None:
            return [], []
        match = recipe.recognise(list(log_lines), Path(worktree), target_arch)
        if match is None:
            return [], []
        path = match.params.get("file") or ""
        if ".symbols" not in PurePosixPath(path).name or path not in set(changed):
            return [], []
        edits, _ = registry.run(RECIPE, worktree, log_lines, target_arch, {"path": path})
        kept, names = [], []
        for edit in edits:
            if edit.path != path or not _untagged(edit.old_text):
                continue   # a line that already carries a tag is the model's choice
            if _bare(edit.new_text) != _bare(edit.old_text) or edit.new_text == edit.old_text:
                continue   # insertion of a tag only
            kept.append(edit)
            names.append(_name(edit.old_text))
        return kept, names
    except Exception:
        return [], []


def _bare(line: str) -> str:
    """The line without its leading tag group."""
    return re.sub(r"^\s*(?:\([^()]*\))?\s*", "", line.rstrip("\r"))


def _name(line: str) -> str:
    body = re.sub(r"^\s*(?:\([^()]*\))?\s*", "", line.rstrip("\r"))
    found = re.match(r'("(?:[^"\\]|\\.)*"(?:@\S*)?|\S+)', body)
    return (found.group(1) if found else body).replace('"', "")


def recipe_target(recipe_id: str, match) -> str:
    """The path a recipe invocation names for this match."""
    params = getattr(match, "params", {}) or {}
    if recipe_id == "symbols-tag":
        return params.get("file") or "input/debian/<package>.symbols"
    if recipe_id == "static-archive-shim":
        return "input/setup.py"
    if recipe_id == "cmake-redirect-imported-dirs":
        find = (params.get("facts") or {}).get("find_package")
        return find[0] if find else "input/CMakeLists.txt"
    if recipe_id == "serial-helper-build":
        return params.get("file") or "input/debian/rules"
    if recipe_id == "optimize-guard-test-memory":
        sites = params.get("sites") or []
        return sites[0][0] if sites else "input/debian/rules"
    return "input/debian/rules"


def stop_hint(worktree, log_lines, target_arch: str) -> str:
    """The recipe invocation paragraph for the best verified recipe the log
    shows, or "" (computed on whatever tree is given)."""
    try:
        from . import patterns
        from .recipes import registry
        for recipe, match in registry.menu(log_lines, worktree, target_arch):
            if recipe.tier in patterns.PREVIEW_TIERS:
                return patterns.invocation_paragraph(
                    recipe.id, recipe_target(recipe.id, match), f"apply the verified {recipe.id} repair")
    except Exception:
        pass
    return ""
