"""optimize-guard-test-memory: the libyal memory-test interposer vs -O2/-O3.

libevtx's tests/evtx_test_memory.h wraps malloc/memset/... for fault
injection and crashes when the compiler optimises the wrappers' callers.
Adding `&& !defined( __OPTIMIZE__ )` to the guard of HAVE_EVTX_TEST_MEMORY
passed the validator (3463 s, FAIL 0)
(harness/replays/libevtx-verified-optimize-guard.json). The second choice
after o2-on-test-segfault: it changes the test harness, not the build flags.

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from .. import patterns
from .registry import Match, Recipe, Refusal, read, register

ID = "optimize-guard-test-memory"
_DEFINE = re.compile(r"^\s*#\s*define\s+(?P<name>HAVE_\w*TEST_MEMORY)\b")
MAX_HEADERS = 8


def _headers(worktree) -> list[str]:
    found = []
    for relative, text in patterns._source_files(worktree, (".h",)):
        parts = relative.split("/")
        if len(parts) == 3 and parts[1] == "tests" and parts[2].endswith("_test_memory.h"):
            found.append(relative)
    return sorted(found)[:MAX_HEADERS]


def _guard_site(text: str):
    """(old_text, new_text, macro) for the #if that guards the define, or None."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        define = _DEFINE.match(line)
        if not define or index == 0:
            continue
        guard = lines[index - 1]
        before = lines[index - 2] if index >= 2 else ""
        is_if = re.match(r"^\s*#\s*if\b", guard) or before.rstrip().endswith("\\")
        if not is_if or "__OPTIMIZE__" in guard:
            continue
        cut = guard.rfind("&&")
        tail = guard[cut:] if cut != -1 else guard
        declared = line[:define.end()]
        old = f"{tail}\n{declared}"
        if text.count(old) != 1:
            continue
        return old, f"{tail.rstrip()} && !defined( __OPTIMIZE__ )\n{declared}", define.group("name")
    return None


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    from .o2_on_test_segfault import segfault_in_tests
    at = segfault_in_tests(log_lines)
    if at is None:
        return None
    sites = []
    for relative in _headers(worktree):
        found = _guard_site(read(worktree, relative))
        if found:
            sites.append((relative, *found))
    if not sites:
        return None
    return Match(ID, evidence=[log_lines[at]] + [f"{path} guards {macro}" for path, _, _, macro in sites],
                 params={"sites": sites}, line=at)


def expand(worktree, match: Match, params: dict) -> list:
    edits = [Edit(path, old, new) for path, old, new, _ in match.params["sites"]]
    if not edits:
        raise Refusal("no tests/*_test_memory.h guard to extend.")
    return edits


def summary(edits, match, params) -> str:
    return "added `&& !defined( __OPTIMIZE__ )` to the " + ", ".join(m for *_, m in match.params["sites"]) + " guard"


register(Recipe(
    ID, "verified",
    "Second choice for a test segfault: adds && !defined( __OPTIMIZE__ ) to the HAVE_*_TEST_MEMORY "
    "guard in tests/*_test_memory.h. new_text: empty.",
    recognise, expand, summary))
