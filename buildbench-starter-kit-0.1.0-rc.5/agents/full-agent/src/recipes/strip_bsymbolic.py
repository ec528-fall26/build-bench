"""strip-bsymbolic-functions: Ubuntu's default link flag crashes ld on aarch64.

libmceliece (noble, arm64): a shared link dies with "BFD ... assertion fail
../../bfd/elfnn-aarch64.c:9754" (62 times). Ubuntu's dpkg-buildflags adds
-Wl,-Bsymbolic-functions; stripping it passes the validator (298 s). Verified
on the real validator: -z now and LTO changes do NOT help, so this recipe never
proposes them (harness/replays/libmceliece-verified-strip-bsymbolic-functions.json).

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, find_line, host_arch, read, register

ID = "strip-bsymbolic-functions"
_ASSERTION = re.compile(r"BFD .*assertion fail .*elfnn-aarch64\.c")
_SHARED = re.compile(r"-shared\b|\bsharedlib\b|\.so\b|--mode=link|-soname")
FLAG = "-Wl,-Bsymbolic-functions"
BEFORE, AFTER = 80, 12


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    at = find_line(log_lines, _ASSERTION)
    if at is None:
        return None
    window = log_lines[max(0, at - BEFORE):at + AFTER]
    if not any(isinstance(line, str) and _SHARED.search(line) for line in window):
        return None
    count = sum(1 for line in log_lines if isinstance(line, str) and _ASSERTION.search(line))
    return Match(ID, evidence=[log_lines[at], f"({count} assertion lines in the log)"],
                 params={"arch": host_arch(log_lines, target_arch) or "arm64"}, line=at)


def expand(worktree, match: Match, params: dict) -> list:
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    if re.search(r"DEB_LDFLAGS_MAINT_STRIP\s*[:+?]?=[^\n]*-Bsymbolic-functions", text):
        raise Refusal("debian/rules already strips -Wl,-Bsymbolic-functions; the failure has another cause.")
    operator = "+=" if rules_mk.has_assignment(text, "DEB_LDFLAGS_MAINT_STRIP") else "="
    line = f"export DEB_LDFLAGS_MAINT_STRIP {operator} {FLAG}\n"
    guarded = "arm64" in (params.get("_raw") or "") or params.get("guarded", "").lower() in ("1", "yes", "true")
    add = f"ifeq ($(DEB_HOST_ARCH),arm64)\n{line}endif\n" if guarded else line
    prefer = ("DEB_LDFLAGS_MAINT_STRIP",) if operator == "+=" else ("DEB_LDFLAGS_MAINT_APPEND",)
    old, new = rules_mk.insertion(text, add, prefer)
    rules_mk.checked(text, old, new, tokens=(f"DEB_LDFLAGS_MAINT_STRIP {operator} {FLAG}",))
    return [Edit(path, old, new)]


def summary(edits, match, params) -> str:
    return f"added `export DEB_LDFLAGS_MAINT_STRIP = {FLAG}` after the export block"


register(Recipe(
    ID, "verified",
    "Adds export DEB_LDFLAGS_MAINT_STRIP = -Wl,-Bsymbolic-functions to debian/rules (verified on the "
    "validator). new_text: empty, or 'arm64' to wrap it in ifeq DEB_HOST_ARCH arm64.",
    recognise, expand, summary))
