"""cmake-redirect-imported-dirs: repair imported targets whose include dir is missing.

ectrans (noble, arm64): libfiat-dev's exported fiat-targets.cmake names Fortran
module directories the package did not install on this architecture. The
repair is a loop after the find_package call that replaces each missing
directory by the one dh-fortran-mod really installed
(/usr/include/<triplet>/fortran/gfortran-mod-*/<leaf>), for every imported
target of the package, not only the one CMake names first. Verified (441 s):
harness/replays/ectrans-verified-cmake-redirect.json. The glob is restricted
to that location; $(FMODDIR) is empty on arm64 and a symlink variant failed.

Standard library only.
"""

from __future__ import annotations

import re

from .. import patterns
from ..types import Edit
from .registry import Match, Recipe, Refusal, find_line, read, register

ID = "cmake-redirect-imported-dirs"
_START = re.compile(r'Imported target "[^"\s]+" includes non-existent path')


def _statement_end(text: str, start: int) -> int:
    """Offset just after the closing parenthesis of the call that starts at ``start``."""
    depth = 0
    for at in range(text.index("(", start), len(text)):
        if text[at] == "(":
            depth += 1
        elif text[at] == ")":
            depth -= 1
            if depth == 0:
                return at + 1
    raise Refusal("the find_package call is not closed.")


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    at = find_line(log_lines, _START)
    if at is None:
        return None
    facts = patterns.imported_targets(log_lines, worktree)
    if not facts or not any("/" in path for path in facts["paths"]):
        return None
    return Match(ID, evidence=[log_lines[at]] + [f"also linked: {t} ({w})" for t, w in list(facts["siblings"].items())[:4]],
                 params={"facts": facts}, line=at)


def expand(worktree, match: Match, params: dict) -> list:
    facts = match.params["facts"]
    where = facts.get("find_package")
    if not where:
        raise Refusal("no find_package call for the imported package was found in input/; "
                      "put the loop after the call that imports it yourself.")
    path, _, call = where
    text = read(worktree, path)
    if not text:
        raise Refusal(f"{path} cannot be read.")
    if "_dep_target" in text:
        raise Refusal(f"{path} already has the redirect loop.")
    targets = list(facts["named"]) + list(facts["siblings"])
    extra = [word for word in (params.get("_words") or []) if re.fullmatch(r"[\w.+-]+", word) and word != "auto"]
    targets += [word for word in extra if word not in targets]
    start = text.find(call)
    if start == -1 or text.count(call) != 1:
        raise Refusal(f"the find_package line in {path} is not unique.")
    end = _statement_end(text, start)
    line_end = text.find("\n", end)
    stop = len(text) if line_end == -1 else line_end + 1
    line_start = text.rfind("\n", 0, start) + 1
    old = text[line_start:stop]
    if text.count(old) != 1:
        raise Refusal(f"the find_package statement in {path} is not unique.")
    name = patterns._FIND_PACKAGE.search(call)
    snippet = patterns.cmake_repair_snippet(targets, package=name.group("name") if name else "")
    suffix = "" if old.endswith("\n") else "\n"
    return [Edit(path, old, old + suffix + snippet + "\n")]


def summary(edits, match, params) -> str:
    facts = match.params["facts"]
    return ("redirects the missing include directories of " + ", ".join(list(facts["named"]) + list(facts["siblings"]))
            + " to /usr/include/<triplet>/fortran/gfortran-mod-*/ after the find_package call")


register(Recipe(
    ID, "verified",
    "Inserts, after the find_package line, a CMake loop that points each imported target's missing include "
    "directory at /usr/include/<triplet>/fortran/gfortran-mod-*/<dir>. new_text: empty, or extra target names.",
    recognise, expand, summary))
