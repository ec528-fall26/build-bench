"""The recipe registry: one id per verified repair, recognised from the log
and expanded by the controller instead of typed by the model.

Why: hosted runs give no build feedback and one validation (master plan M2).
Four of the five "unrepairable" local cases turned out to have a repair the
validator accepts, but the model (gpt-oss-120b class) mistypes 30-line edits
and retypes 4,000-line files. A recipe is the repair, reduced to what the
evidence decides:

  recognise(log_lines, worktree, target_arch) -> Match | None
      Reads the log (and the tree) and says whether the signature is there,
      with the evidence lines and the parameters it settled on.
  expand(worktree, match, params) -> list[Edit]
      Computes the edits deterministically from the tree; raises Refusal when
      a precondition fails (never guesses, never half-applies).

The model invokes a recipe through the existing macro channel: an edit whose
old_text is ``@@recipe:<id>@@`` and whose new_text is plain-text parameters
(a tag, a target list; may be empty, "auto" or "default" for the defaults). ``@@tag-missing-symbols@@`` stays as an
alias of ``symbols-tag``. Nothing here raises out of a public function; a
failure is a Refusal note.

Standard library only; every scan is bounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable

TIERS = ("verified", "verified-template", "historical", "hypothesis")
MAX_LOG_LINES = 400_000
MAX_FILE_BYTES = 1024 * 1024
MACRO = re.compile(r"^@@recipe:(?P<id>[a-z0-9][a-z0-9-]*)@@$")
DEFAULT_WORDS = ("auto", "default")
ALIASES = {"@@tag-missing-symbols@@": "symbols-tag"}


class Refusal(Exception):
    """A precondition of the recipe failed; the message is for the model."""


@dataclass
class Match:
    recipe_id: str
    evidence: list[str]
    params: dict = field(default_factory=dict)
    line: int | None = None      # index in the log of the signature line
    offset: int | None = None    # line relative to the first fatal error (menu only)


@dataclass
class Recipe:
    id: str
    tier: str
    note: str
    recognise: Callable[[list, Path, str], "Match | None"]
    expand: Callable[[Path, Match, dict], list]
    summary: Callable[[list, Match, dict], str] | None = None


_RECIPES: dict[str, Recipe] = {}
_MODULES = ("symbols_tag", "strip_bsymbolic", "o2_on_test_segfault", "optimize_guard",
            "serial_helper", "cmake_redirect", "replace_removed_command", "arch_flags",
            "static_shim")
_loaded = False


def register(recipe: Recipe) -> Recipe:
    _RECIPES[recipe.id] = recipe
    return recipe


def load() -> None:
    """Import every recipe module once; a module that fails to import is skipped."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    import importlib
    for name in _MODULES:
        try:
            importlib.import_module(f"{__package__}.{name}")
        except Exception:  # a broken recipe must not take the others down
            continue


def get(recipe_id: str) -> Recipe | None:
    load()
    return _RECIPES.get(recipe_id)


def ids() -> list[str]:
    load()
    return sorted(_RECIPES)


def macro_token(recipe_id: str) -> str:
    return f"@@recipe:{recipe_id}@@"


def macro_id(old_text) -> str | None:
    """The recipe id an edit's old_text invokes, or None for an ordinary edit."""
    if not isinstance(old_text, str):
        return None
    token = old_text.strip()
    if token in ALIASES:
        return ALIASES[token]
    found = MACRO.match(token)
    return found.group("id") if found else None


def invocation(recipe_id: str, path: str, hypothesis: str, new_text: str = "") -> str:
    """The one-line edit a block tells the model to send."""
    import json
    return json.dumps({"action": "edit", "hypothesis": hypothesis,
                       "edits": [{"path": path, "old_text": macro_token(recipe_id),
                                  "new_text": new_text}]})


# --- shared helpers -----------------------------------------------------------

def read(worktree, relative: str) -> str:
    """The text of a worktree file ("" when unreadable, binary or over the limit)."""
    try:
        path = Path(worktree) / relative
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return ""


_HOST_ARCH = re.compile(r"dpkg-buildpackage: info: host architecture (?P<arch>[\w-]+)")


def host_arch(log_lines, target_arch: str = "") -> str:
    """The Debian name of the architecture that fails: the case's target, else
    what dpkg-buildpackage printed in the log; "" when neither says."""
    from .. import archnames
    named = archnames.debian(target_arch)
    if named:
        return named
    for line in list(log_lines)[:MAX_LOG_LINES]:
        found = _HOST_ARCH.search(line)
        if found:
            return archnames.debian(found.group("arch")) or found.group("arch")
    return ""


def find_line(log_lines, pattern, start: int = 0) -> int | None:
    """Index of the first log line (from ``start``) matching the compiled regex."""
    for index in range(max(0, start), min(len(log_lines), MAX_LOG_LINES)):
        line = log_lines[index]
        if isinstance(line, str) and pattern.search(line):
            return index
    return None


def parse_params(text) -> dict:
    """Plain-text parameters from a macro edit's new_text.

    ``key=value`` / ``key: value`` lines become entries; the raw text (stripped)
    is always under "_raw" and its whitespace-separated words under "_words".
    """
    raw = text.strip() if isinstance(text, str) else ""
    if raw.lower() in DEFAULT_WORDS:
        raw = ""   # "auto" / "default" / nothing all mean: use the recipe's defaults
    params: dict = {"_raw": raw, "_words": raw.split()}
    for line in raw.splitlines():
        found = re.match(r"^\s*([A-Za-z_][\w-]*)\s*[:=]\s*(.+?)\s*$", line)
        if found and " " not in found.group(1):
            params[found.group(1).lower()] = found.group(2)
    return params


# --- running a recipe ----------------------------------------------------------

def menu(log_lines, worktree, target_arch: str = "") -> list[tuple[Recipe, Match]]:
    """Every recipe whose signature is in the log, best first: the signature
    line closest after the first fatal error ranks first (ties by id)."""
    load()
    from .. import patterns
    lines = list(log_lines)[:MAX_LOG_LINES]
    first = patterns.first_fatal_index(lines)
    found = []
    for recipe in _RECIPES.values():
        try:
            match = recipe.recognise(lines, Path(worktree), target_arch)
        except Exception:
            match = None
        if match is None:
            continue
        if match.line is not None:
            match.offset = match.line - first if match.line >= first else 10**6 + first - match.line
        found.append((recipe, match))
    found.sort(key=lambda item: (item[1].offset if item[1].offset is not None else 10**9, item[0].id))
    return found


def run(recipe_id: str, worktree, log_lines, target_arch: str = "",
        params: dict | None = None) -> tuple[list, str]:
    """(edits, note) for one recipe; ([], note) when it refuses. Never raises."""
    recipe = get(recipe_id)
    if recipe is None:
        return [], (f"Recipe {recipe_id} is unknown; the recipes are: "
                    + ", ".join(ids()) + ". Write the edit yourself.")
    params = params or {}
    try:
        match = recipe.recognise(list(log_lines)[:MAX_LOG_LINES], Path(worktree), target_arch)
    except Exception:
        match = None
    if match is None:
        return [], (f"Recipe {recipe_id} refused: the log does not show the signature it repairs "
                    "(or the tree lacks the file it edits). It does not guess; write the edit yourself.")
    try:
        edits = recipe.expand(Path(worktree), match, params)
    except Refusal as reason:
        return [], f"Recipe {recipe_id} refused: {reason}"
    except Exception:
        return [], f"Recipe {recipe_id} refused: it could not compute the edit from this tree. Write the edit yourself."
    if not edits:
        return [], f"Recipe {recipe_id} refused: nothing to change (the tree may already have the repair)."
    try:
        detail = recipe.summary(edits, match, params) if recipe.summary else ""
    except Exception:
        detail = ""
    paths = ", ".join(dict.fromkeys(edit.path for edit in edits))
    return edits, f"Recipe {recipe_id} expanded: {detail or str(len(edits)) + ' edit(s)'} in {paths}."
