"""python-version-slice: `sys.version[:3]` is "3.1" on Python 3.10 and later.

python-reportlab (jammy, amd64): debian/rules has

    VER3 := $(shell /usr/bin/python3 -c 'import sys; print(sys.version[:3])')

and builds the documentation with PYTHONPATH=$(wildcard build/lib.*-$(VER3)).
With Python 3.10 the slice gives "3.1", the wildcard matches nothing, and the
docs step dies with "ModuleNotFoundError: No module named 'reportlab'". Only
the full build runs that step, which is why the arch-only build on the other
architecture passed. Not an architecture problem at all.

Verified on 13 November 2026 with a real jammy build (`dpkg-buildpackage -b`):
unmodified source fails with that traceback, with the version taken from
sys.version_info the build passes and the doc package is built.

Recognised when debian/rules (or a top-level setup.py, Makefile or configure
file) takes a three-character slice of sys.version and the log shows a Python
3.10 or later interpreter. The replacement has no quotes, so it is valid inside
either kind of shell quoting. A baseline: the controller adds it after the
model has finished.

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from .registry import Match, Recipe, Refusal, read, register

ID = "python-version-slice"
FILES = ("input/debian/rules", "input/setup.py", "input/Makefile", "input/configure.ac", "input/Makefile.am")
_SLICE = re.compile(r"sys\.version\s*\[\s*0?\s*:\s*3\s*\]")
_PY310 = re.compile(r"python3\.1\d\b|Python 3\.1\d\b|python3_3\.1\d|cpython-31\d|lib\.linux-[\w-]+-3\.1\d\b")
# In parentheses: the slice may be an operand ("python%s" % sys.version[:3]) or have a method called on it.
REPLACEMENT = "(str(sys.version_info[0])+chr(46)+str(sys.version_info[1]))"


def _found(worktree) -> list:
    out = []
    for path in FILES:
        text = read(worktree, path)
        for number, line in enumerate((text or "").split("\n"), start=1):
            if _SLICE.search(line):
                out.append((path, number, line))
    return out


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    found = _found(worktree)
    if not found:
        return None
    seen = next((line for line in log_lines if isinstance(line, str) and _PY310.search(line)), None)
    if seen is None:
        return None
    path, number, line = found[0]
    return Match(ID, evidence=[f"{path}:{number}: {line.strip()[:200]}",
                               f"(the log shows Python 3.10 or later: {seen.strip()[:120]})"],
                 params={"files": sorted({p for p, _, _ in found})}, line=None)


def expand(worktree, match: Match, params: dict) -> list:
    edits = []
    for path, _, line in _found(worktree):
        text = read(worktree, path)
        if text.count(line) != 1:
            raise Refusal(f"{path}: the line with the version slice is not unique; edit it yourself.")
        edits.append(Edit(path, line, _SLICE.sub(REPLACEMENT, line)))
    if not edits:
        raise Refusal("no sys.version[:3] left to replace.")
    return edits


def summary(edits, match, params) -> str:
    return (f"replaced sys.version[:3] by the major.minor version from sys.version_info in "
            f"{', '.join(sorted({e.path for e in edits}))}")


register(Recipe(
    ID, "baseline",
    "Replaces sys.version[:3] (\"3.1\" on Python 3.10 and later) by the major.minor version built from "
    "sys.version_info, in debian/rules and top-level build files. new_text: empty.",
    recognise, expand, summary))
