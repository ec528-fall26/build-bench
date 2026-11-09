"""arch-flags: compiler flags for one architecture, in debian/rules.

libunwind (plucky, arm64): the test programs fail to link because GCC emits
outline-atomics helpers (__aarch64_cas8_acq_rel, ...) that live hidden in
libgcc.a. Compiling with -mno-outline-atomics on arm64 makes the atomics
inline and passed the validator (193 s). The lines go after the
DEB_BUILD_MAINT_OPTIONS export, at make level, exported (dpkg-buildflags reads
the environment), in an ifeq on DEB_HOST_ARCH.

Only flags on a short allow-list are written: a mistyped ISA flag breaks every
compile, and this recipe has a single verified use.

Standard library only.
"""

from __future__ import annotations

from .. import patterns
from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, find_line, host_arch, read, register

ID = "arch-flags"
ALLOWED = {"arm64": ("-mno-outline-atomics",)}


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    at = find_line(log_lines, patterns._OUTLINE)
    if at is None:
        return None
    arch = host_arch(log_lines, target_arch) or "arm64"
    if arch not in ALLOWED:
        return None
    return Match(ID, evidence=[log_lines[at]], params={"arch": arch, "flags": list(ALLOWED[arch])}, line=at)


def expand(worktree, match: Match, params: dict) -> list:
    arch, flags = match.params["arch"], list(match.params["flags"])
    words = [w for w in (params.get("_words") or []) if w != "auto"]
    for word in words:
        if word == arch:
            continue
        if word.startswith("-"):
            if word not in ALLOWED[arch]:
                raise Refusal(f"flag {word} is not on the verified list for {arch} "
                              f"({', '.join(ALLOWED[arch])}); write that edit yourself.")
        else:
            raise Refusal(f"architecture {word} is not the failing one ({arch}).")
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    if all(flag in text for flag in flags):
        raise Refusal(f"debian/rules already has {' '.join(flags)}.")
    joined = " ".join(flags)
    add = (f"ifeq ($(DEB_HOST_ARCH),{arch})\n"
           f"export DEB_CFLAGS_MAINT_APPEND += {joined}\n"
           f"export DEB_CXXFLAGS_MAINT_APPEND += {joined}\n"
           "endif\n")
    old, new = rules_mk.insertion(text, add, ("DEB_BUILD_MAINT_OPTIONS",))
    rules_mk.checked(text, old, new, tokens=(f"DEB_HOST_ARCH),{arch})", joined))
    match.params["_report"] = (arch, joined)
    return [Edit(path, old, new)]


def summary(edits, match, params) -> str:
    arch, joined = match.params["_report"]
    return f"added the ifeq {arch} block exporting {joined} for CFLAGS and CXXFLAGS"


register(Recipe(
    ID, "verified",
    "Adds ifeq DEB_HOST_ARCH arm64 with export DEB_CFLAGS/CXXFLAGS_MAINT_APPEND += -mno-outline-atomics "
    "to debian/rules. new_text: empty.",
    recognise, expand, summary))
