"""o2-on-test-segfault: Ubuntu's own fix for libevtx, built with -O2 not -O3.

libevtx (plucky, amd64): test_tools.sh dies with a segmentation fault (exit
status 139) when the package is compiled with -O3. Ubuntu's libevtx
20240504-1ubuntu1 strips -O3 and appends -O2 on amd64 and ppc64el; that edit
passed the validator (2846 s, test_tools.sh PASS)
(harness/replays/libevtx-verified-o2-on-amd64.json).

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, find_line, host_arch, read, register

ID = "o2-on-test-segfault"
ARCHES = ("amd64", "ppc64el")
SEGFAULT = re.compile(r"Segmentation fault|\(core dumped\)|exit status:? 139\b|exited with status 139")
_O3 = re.compile(r"(?<![\w-])-O3(?![\w-])")
_COMPILE = re.compile(r"(?:^|[\s/])(?:gcc|g\+\+|cc|c\+\+|clang(?:\+\+)?|[\w-]+-linux-gnu-g(?:cc|\+\+))\s")
_CXX = re.compile(r"(?:^|[\s/])(?:g\+\+|c\+\+|clang\+\+|[\w-]+-linux-gnu-g\+\+)\s")
_TEST_STAGE = re.compile(r"dh_auto_test|^FAIL[: ]|^# FAIL|Testsuite summary|\bmake\b.* check\b|test_runner|\(FAIL\)")


def segfault_in_tests(log_lines) -> int | None:
    """Index of a segfault line that belongs to a test run, or None."""
    at = find_line(log_lines, SEGFAULT)
    if at is None:
        return None
    if find_line(log_lines, _TEST_STAGE) is None:
        return None
    return at


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    arch = host_arch(log_lines, target_arch)
    if arch not in ARCHES:
        return None
    at = segfault_in_tests(log_lines)
    if at is None:
        return None
    o3 = next((line for line in log_lines if isinstance(line, str)
               and _COMPILE.search(line) and _O3.search(line)), None)
    if o3 is None:
        return None
    cxx = any(isinstance(line, str) and _CXX.search(line) and _O3.search(line) for line in log_lines)
    return Match(ID, evidence=[log_lines[at], o3.strip()[:200]],
                 params={"arch": arch, "cxx": cxx}, line=at)


def expand(worktree, match: Match, params: dict) -> list:
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    if re.search(r"DEB_C(?:XX)?FLAGS_MAINT_STRIP\s*[:+?]?=[^\n]*-O3", text):
        raise Refusal("debian/rules already strips -O3; the segfault has another cause.")
    languages = ["C"] + (["CXX"] if match.params.get("cxx") else [])
    lines = [f"ifneq (,$(filter $(DEB_HOST_ARCH), {' '.join(ARCHES)}))"]
    for language in languages:
        strip = "+=" if rules_mk.has_assignment(text, f"DEB_{language}FLAGS_MAINT_STRIP") else "="
        append = "+=" if rules_mk.has_assignment(text, f"DEB_{language}FLAGS_MAINT_APPEND") else "="
        lines.append(f"  export DEB_{language}FLAGS_MAINT_STRIP {'' if strip == '+=' else ' '}{strip} -O3")
        lines.append(f"  export DEB_{language}FLAGS_MAINT_APPEND {append} -O2")
    lines.append("endif")
    old, new = rules_mk.insertion(text, "\n".join(lines) + "\n", ("DEB_BUILD_MAINT_OPTIONS",))
    rules_mk.checked(text, old, new, tokens=("-O3", "-O2", "DEB_HOST_ARCH"))
    return [Edit(path, old, new)]


def summary(edits, match, params) -> str:
    return "strips -O3 and appends -O2 on " + " and ".join(ARCHES) + " (Ubuntu's own libevtx fix)"


register(Recipe(
    ID, "verified",
    "Compiles with -O2 instead of -O3 on amd64/ppc64el via DEB_CFLAGS_MAINT_STRIP/APPEND in debian/rules "
    "(the fix Ubuntu uploaded for libevtx). new_text: empty.",
    recognise, expand, summary))
