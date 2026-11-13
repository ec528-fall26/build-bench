"""strip-buildflag: leave out a dpkg-buildflags flag the target's compiler rejects.

eegdev and rocm-device-libs (noble, amd64) build on arm64 and fail on amd64
with "gcc: error: unrecognized command-line option '-fno-cf-protection'":
noble's dpkg-buildflags turns a disabled `branch` hardening feature into that
flag on amd64, GCC does not know it, and configure stops with "C compiler
cannot create executables". The flag is nowhere in the package; arm64 gets
-mbranch-protection instead and never sees it. In the rc.5 Development
Validation run the model left eegdev unchanged in effect. The maintainer fix
is to strip the flag from the flags dpkg-buildflags hands out, on the failing
architecture only: DEB_CFLAGS_MAINT_STRIP / DEB_CXXFLAGS_MAINT_STRIP, which
dpkg-buildflags applies after every feature.

Recognised only when the compiler's own "unrecognized command-line option"
names a flag from dpkg-buildflags' vocabulary (hardening, prefix maps, LTO,
frame pointers) and no file of the package contains that flag, so it can only
come from dpkg-buildflags. A flag the package sets itself (qmidiarp's -msse,
blis's -mavx512pf) is not this recipe: the block lists where it is set.

Verified on 13 November 2026 with real amd64 builds of eegdev (emulated, noble,
dpkg-dev 1.22.5ubuntu4 from snapshot.ubuntu.com as in the log; today's
1.22.6ubuntu6 emits -fcf-protection=none and no longer fails): unmodified
source FAILS at "C compiler cannot create executables", with this recipe's
lines it PASSES and no second failure follows. rocm-device-libs was not built.

Standard library only; the tree scan is bounded.
"""

from __future__ import annotations

from pathlib import Path
import re

from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, host_arch, read, register

ID = "strip-buildflag"

# What dpkg-buildflags can emit (dpkg 1.21-1.22, Ubuntu's additions included).
FROM_BUILDFLAGS = (
    "-fno-cf-protection", "-fcf-protection", "-mbranch-protection", "-mno-branch-protection",
    "-fstack-clash-protection", "-fno-stack-clash-protection",
    "-fstack-protector", "-fno-stack-protector",
    "-ffile-prefix-map", "-fdebug-prefix-map", "-fmacro-prefix-map",
    "-fno-omit-frame-pointer", "-mno-omit-leaf-frame-pointer",
    "-flto", "-ffat-lto-objects", "-fno-lto",
    "-fzero-call-used-regs", "-ftrivial-auto-var-init", "-mshstk",
)
# Two wordings: the driver does not know the option at all, or cc1 knows it
# and the target cannot do it (golf, arm64: "cc1: error: '-fcf-protection=full'
# is not supported for this target", from -fcf-protection in its own Makefile).
UNRECOGNISED = re.compile(
    r"(?:^|[\s/])(?P<compiler>(?:[\w.+-]*-)?(?:gcc|g\+\+|cc|c\+\+|cc1|cc1plus|gfortran|f95)(?:-\d+)?): "
    r"(?:fatal )?error: (?:unrecognized command[- ]line option ['‘](?P<flag>-[^'’\s]+)['’]"
    r"|['‘](?P<unsupported>-[^'’\s]+)['’] is not supported for this target)")
_TIMESTAMP = re.compile(r"^\[\s*\d+s\]\s?")
MAX_FILES = 4000
MAX_FILE_BYTES = 512 * 1024
_SKIP_DIRS = {".git", ".pc", ".pybuild", "__pycache__"}
_CXX = ("g++", "c++", "cc1plus")
_FORTRAN = ("gfortran", "f95")


def rejected_flags(log_lines) -> list[tuple[str, str, int, str]]:
    """(flag, compiler, log index, line) for each distinct rejected option, in log order."""
    seen, out = set(), []
    for index, raw in enumerate(log_lines):
        if not isinstance(raw, str) or ("nrecognized command" not in raw
                                        and "not supported for this target" not in raw):
            continue
        found = UNRECOGNISED.search(_TIMESTAMP.sub("", raw))
        flag = found and (found.group("flag") or found.group("unsupported"))
        if flag and flag not in seen:
            seen.add(flag)
            out.append((flag, found.group("compiler"), index, _TIMESTAMP.sub("", raw).strip()[:240]))
    return out


def set_by_package(worktree, flag: str, limit: int = 12) -> list[str]:
    """Where the package itself sets ``flag``: as reported, or without its
    value (the compiler says -fcf-protection=full for a plain -fcf-protection)."""
    found = occurrences(worktree, flag, limit)
    if not found and "=" in flag:
        found = occurrences(worktree, flag.split("=", 1)[0], limit)
    return found


def occurrences(worktree, flag: str, limit: int = 12) -> list[str]:
    """`path:line` of each place a package file contains ``flag`` as a whole word."""
    root = Path(worktree) / "input"
    pattern = re.compile(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])")
    found, scanned = [], 0
    try:
        stack = [root]
        while stack and scanned < MAX_FILES and len(found) < limit:
            directory = stack.pop()
            for entry in sorted(directory.iterdir()):
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS:
                        stack.append(entry)
                    continue
                scanned += 1
                try:
                    if entry.stat().st_size > MAX_FILE_BYTES:
                        continue
                    text = entry.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if flag not in text:
                    continue
                for number, line in enumerate(text.split("\n"), start=1):
                    if pattern.search(line):
                        found.append(f"{entry.relative_to(Path(worktree)).as_posix()}:{number}")
                        break
                if scanned >= MAX_FILES or len(found) >= limit:
                    break
    except OSError:
        pass
    return found


def _from_buildflags(flag: str) -> bool:
    return any(flag == known or flag.startswith(known + "=") or
               (known == "-fstack-protector" and flag.startswith(known + "-"))
               for known in FROM_BUILDFLAGS)


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    injected = []
    for flag, compiler, index, line in rejected_flags(log_lines):
        if not _from_buildflags(flag):
            continue
        if set_by_package(worktree, flag, limit=1):
            continue                       # the package sets it itself: not ours to strip
        injected.append((flag, compiler, index, line))
    if not injected or not read(worktree, "input/debian/rules"):
        return None
    compilers = {compiler.rsplit("-", 1)[0] if compiler[-1:].isdigit() else compiler
                 for _, compiler, _, _ in injected}
    return Match(ID, evidence=[line for _, _, _, line in injected[:2]], line=injected[0][2],
                 params={"flags": [flag for flag, _, _, _ in injected],
                         "fortran": any(c.endswith(_FORTRAN) for c in compilers),
                         "arch": host_arch(log_lines, target_arch)})


def expand(worktree, match: Match, params: dict) -> list:
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    flags = " ".join(match.params["flags"])
    variables = ["DEB_CFLAGS_MAINT_STRIP", "DEB_CXXFLAGS_MAINT_STRIP"]
    if match.params.get("fortran"):
        variables += ["DEB_FFLAGS_MAINT_STRIP", "DEB_FCFLAGS_MAINT_STRIP"]
    if all(re.search(r"^\s*(?:export\s+)?" + name + r"\s*[:+?]?=[^\n]*" + re.escape(flag) + r"(?![\w=-])",
                     text, re.MULTILINE)
           for name in variables[:2] for flag in match.params["flags"]):
        raise Refusal(f"debian/rules already strips {flags}; the failure has another cause.")
    arch = match.params.get("arch") or ""
    body = "".join(f"export {name} += {flags}\n" for name in variables)
    comment = f"# The compiler on this architecture rejects {flags}, which dpkg-buildflags adds\n"
    add = comment + (f"ifeq ($(DEB_HOST_ARCH),{arch})\n{body}endif\n" if arch else body)
    old, new = rules_mk.insertion(text, add, ("DEB_CFLAGS_MAINT_STRIP", "DEB_BUILD_MAINT_OPTIONS",
                                              "DEB_CFLAGS_MAINT_APPEND"))
    after = text.replace(old, new, 1)
    tail = after[after.index(new) + len(new):]
    if re.search(r"^\s*(?:export\s+)?DEB_C(?:XX)?FLAGS_MAINT_STRIP\s*:?=", tail, re.MULTILINE):
        raise Refusal("debian/rules assigns DEB_CFLAGS/CXXFLAGS_MAINT_STRIP with = further down, "
                      "which would drop the new entries; add the flag to that line yourself.")
    rules_mk.checked(text, old, new, tokens=(flags,))
    match.params["_report"] = (flags, arch, len(variables))
    return [Edit(path, old, new)]


def summary(edits, match, params) -> str:
    flags, arch, count = match.params["_report"]
    where = f" for {arch}" if arch else ""
    names = "DEB_CFLAGS/CXXFLAGS_MAINT_STRIP" if count == 2 else "DEB_C/CXX/F/FCFLAGS_MAINT_STRIP"
    return f"exported {flags} in {names}{where}"


register(Recipe(
    ID, "verified",
    "Exports DEB_CFLAGS_MAINT_STRIP and DEB_CXXFLAGS_MAINT_STRIP += <flag> (in an ifeq on the failing "
    "architecture) in debian/rules, for each dpkg-buildflags flag the compiler reports as an "
    "unrecognized command-line option and that no package file contains. new_text: empty.",
    recognise, expand, summary))
