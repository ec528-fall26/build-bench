"""arm64-x86-semantics: compile on arm64 with the three defaults x86 has.

A package whose tests pass on amd64 and fail on arm64, with no compile error,
often trips over one of three differences that are not in the package's logic:

  plain `char` is unsigned on arm64        `char c = fgetc(f); if (c == EOF)` never ends
  GCC contracts a*b+c into a fused         a seeded numeric test misses a narrow
    multiply-add on arm64, not on x86        tolerance by one rounding
  code that reads memory through a         works where the compiler happens not to
    pointer of another type                  exploit it, breaks at -O2 elsewhere

Each has a compiler flag that restores the behaviour the amd64 build has:
-fsigned-char, -ffp-contract=off, -fno-strict-aliasing. They change how the
package's own code is compiled on arm64 only; no test is touched.

Verified on 13 November 2026 with real arm64 package builds of three public
cases, unmodified source plus exactly this block (today's archive):

  tasmanian (resolute)  PythonDream fails a variance tolerance   -> 26/26 tests pass (-ffp-contract=off is the one that matters)
  xsimd (resolute)      10 of 343 test cases fail                 -> 343/343 pass (-fno-strict-aliasing)
  mlmmj (questing)      24 of 196 fail, four by 300 s timeout     -> 195/196 pass (-fsigned-char); the one left
                                                                    fails with the source fix too

All three failed in the rc.5 Development Validation run; xsimd and tasmanian
with no usable change at all. It does not help where the defect is in
arm-specific code (hippomocks, libcvd, sphde, libmaus2).

Recognised only on arm64, when the build fails in its test step and the log
shows C or C++ being compiled. Added by the controller after the model has
finished (loop.BASELINE_IDS), on top of whatever the model did, and placed
just before the first rule of debian/rules so that it follows every
assignment in the header.

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, host_arch, read, register

ID = "arm64-x86-semantics"
FLAGS = "-fsigned-char -ffp-contract=off -fno-strict-aliasing"
_TEST_FAILED = re.compile(r"dh_auto_test: error:|\[[^\]]*override_dh_auto_test\] Error|"
                          r"\[[^\]]*execute_(?:after|before)_dh_auto_test\] Error")
_COMPILE = re.compile(r"(?:^|[\s/])(?:gcc|g\+\+|cc|c\+\+|[\w-]+-linux-gnu-g(?:cc|\+\+))(?:-\d+)?\s.*\s-c\s")
_COMPILE_ERROR = re.compile(r":\d+:\d+: (?:fatal )?error: |: undefined reference to |collect2: error:")
_VARIABLES = ("DEB_CFLAGS_MAINT_APPEND", "DEB_CXXFLAGS_MAINT_APPEND")


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    if host_arch(log_lines, target_arch) != "arm64":
        return None
    text = [line for line in log_lines if isinstance(line, str)]
    failed = next((i for i in range(len(text) - 1, -1, -1) if _TEST_FAILED.search(text[i])), None)
    if failed is None:
        return None
    if not any(_COMPILE.search(line) for line in text[:failed]):
        return None                       # nothing these flags could reach
    if any(_COMPILE_ERROR.search(line) for line in text[max(0, failed - 400):failed]):
        return None                       # the tests did not compile or link: another kind of failure
    rules = read(worktree, "input/debian/rules")
    if not rules or all(flag in rules for flag in FLAGS.split()):
        return None
    return Match(ID, evidence=[text[failed].strip()[:240]], params={"arch": "arm64"}, line=failed)


def expand(worktree, match: Match, params: dict) -> list:
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    add = ("# Compile as on x86, where the tests pass: signed plain char, no fused\n"
           "# multiply-add contraction, no type-based aliasing assumptions.\n"
           "ifeq ($(DEB_HOST_ARCH),arm64)\n"
           + "".join(f"export {name} += {FLAGS}\n" for name in _VARIABLES)
           + "endif\n")
    # Just before the first rule: after every assignment in the header, including
    # ones inside conditionals for other architectures (xsimd sets
    # DEB_CXXFLAGS_MAINT_APPEND = ... for armhf only, below its exports).
    placed = rules_mk.before_first_rule(text, add + "\n")
    old, new = placed if placed else rules_mk.insertion(text, add, _VARIABLES + ("DEB_BUILD_MAINT_OPTIONS",))
    after = text.replace(old, new, 1)
    if rules_mk.assigned_after(after, add, _VARIABLES):
        raise Refusal("debian/rules assigns DEB_CFLAGS/CXXFLAGS_MAINT_APPEND with = further down, which "
                      "would drop the new entries; add the flags to that line yourself.")
    rules_mk.checked(text, old, new, tokens=(FLAGS,))
    return [Edit(path, old, new)]


def summary(edits, match, params) -> str:
    return f"exported {FLAGS} in DEB_CFLAGS/CXXFLAGS_MAINT_APPEND for arm64"


register(Recipe(
    ID, "baseline",       # not offered as a preview or a stop hint: the controller applies it, see loop.BASELINE_IDS
    "Exports -fsigned-char -ffp-contract=off -fno-strict-aliasing in DEB_CFLAGS_MAINT_APPEND and "
    "DEB_CXXFLAGS_MAINT_APPEND for arm64 in debian/rules, so the package's code is compiled with the "
    "char signedness, floating-point contraction and aliasing behaviour it has on amd64. For a test "
    "step that fails only on arm64 with no compile error. new_text: empty.",
    recognise, expand, summary))
