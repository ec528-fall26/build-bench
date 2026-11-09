"""replace-removed-command: `setup.py build_sphinx` -> `python3 -m sphinx`.

python-intbitset (noble, amd64): Sphinx 7 removed the setuptools command, so
`python3 setup.py build_sphinx` fails with "invalid command 'build_sphinx'" in
the -indep step. The accepted repair replaces that one command with the direct
Sphinx call, source and build directories from setup.cfg's [build_sphinx], in
the same target, so the -doc package's .install file still finds the HTML.
Disabling the target is what failed in the first run.

Standard library only.
"""

from __future__ import annotations

import re

from .. import patterns
from ..types import Edit
from .registry import Match, Recipe, Refusal, find_line, read, register

ID = "replace-removed-command"
_CALL = re.compile(r"^(?P<lead>\t[@+-]*)(?P<py>python3?|\$\(PYTHON\))\s+setup\.py\s+build_sphinx\b(?P<rest>[^\n]*)$", re.M)


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    at = find_line(log_lines, patterns._BUILD_SPHINX)
    if at is None:
        return None
    return Match(ID, evidence=[log_lines[at]], params={}, line=at)


def expand(worktree, match: Match, params: dict) -> list:
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    calls = list(_CALL.finditer(text))
    if not calls:
        raise Refusal("debian/rules has no `python3 setup.py build_sphinx` recipe line "
                      "(it may be disabled, or run from another file); edit that line yourself.")
    source, build = patterns._setup_cfg_sphinx(worktree)
    html = build.rstrip("/") + "/html"
    edits = []
    for call in calls:
        line = call.group(0)
        if text.count(line) != 1:
            raise Refusal("the build_sphinx line of debian/rules is not unique.")
        edits.append(Edit(path, line, f"{call.group('lead')}python3 -m sphinx -b html {source} {html}"))
    match.params["_report"] = (source, html)
    return edits


def summary(edits, match, params) -> str:
    source, html = match.params["_report"]
    return f"replaced `setup.py build_sphinx` with `python3 -m sphinx -b html {source} {html}` in the same target"


register(Recipe(
    ID, "verified",
    "Replaces the python3 setup.py build_sphinx recipe line in debian/rules with python3 -m sphinx -b html "
    "<source> <build>/html, keeping the target. new_text: empty.",
    recognise, expand, summary))
