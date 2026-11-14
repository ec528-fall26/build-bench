"""wno-error: keep a newer compiler's false-positive-prone warning a warning.

liboqs (jammy, arm64: -Werror=array-parameter, 8 errors in one file, more in
its siblings) and bzrtp (plucky, amd64: -Werror=stringop-overflow) failed
because the package builds with a blanket -Werror and a newer GCC warns. The
rc.5 Development Validation run edited one file in each, and the build failed
on the next file with the same warning. The maintainer fix is one flag for the
whole build: -Wno-error=<warning> in DEB_CFLAGS/CXXFLAGS_MAINT_APPEND, which
dpkg-buildflags passes to every compile. GCC keeps the per-warning setting even
when the package's own -Werror comes later on the command line (checked with
gcc 13); it does not override an explicit -Werror=<warning>, so that refuses.

Recognised only when every compiler error in the log carries [-Werror=X], the
log says "all warnings being treated as errors", the compile lines carry the
dpkg-buildflags hardening flags, and every X is on ALLOWED: warnings from
GCC's optimiser heuristics that are known for false positives. Diagnostics
that usually mean a real portability bug (type-limits from char signedness,
implicit declarations, pointer and integer conversions, format-security) are
not on it and are left to the model.

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, host_arch, read, register

ID = "wno-error"

ALLOWED = frozenset({
    "array-parameter", "array-bounds", "vla-parameter",
    "stringop-overflow", "stringop-overread", "stringop-truncation",
    "maybe-uninitialized", "dangling-pointer", "use-after-free", "free-nonheap-object",
    "restrict", "alloc-size-larger-than", "mismatched-new-delete",
    "format-truncation", "format-overflow", "address", "nonnull", "nonnull-compare",
    "deprecated-declarations", "class-memaccess", "attributes",
})

_WERROR = re.compile(r":\d+(?::\d+)?: error: .*\[-Werror=(?P<name>[a-z0-9+-]+?)=?\]")
_COMPILER_ERROR = re.compile(r"^\S+:\d+(?::\d+)?: (?:fatal )?error: ")
_ALL_AS_ERRORS = re.compile(r"\bcc1(?:plus)?: all warnings being treated as errors")
_BUILDFLAGS = re.compile(r"-fstack-protector-strong|-f(?:file|debug)-prefix-map=")
_TIMESTAMP = re.compile(r"^\[\s*\d+s\]\s?")


def _strip(line: str) -> str:
    return _TIMESTAMP.sub("", line) if isinstance(line, str) else ""


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    names: dict[str, None] = {}
    evidence, first = [], None
    saw_all, saw_flags = False, False
    for index, raw in enumerate(log_lines):
        line = _strip(raw)
        if _COMPILER_ERROR.match(line):
            found = _WERROR.search(line)
            if not found:
                return None            # a real compile error: not this signature
            names.setdefault(found.group("name"), None)
            if first is None:
                first = index
            if len(evidence) < 3:
                evidence.append(line.strip()[:240])
        elif _ALL_AS_ERRORS.search(line):
            saw_all = True
        if not saw_flags and _BUILDFLAGS.search(line):
            saw_flags = True
    if not names or not saw_all or not saw_flags:
        return None
    if any(name not in ALLOWED for name in names):
        return None
    if not read(worktree, "input/debian/rules"):
        return None
    explicit = [name for name in names if _explicit(log_lines, name)]
    return Match(ID, evidence=evidence, line=first,
                 params={"names": list(names), "explicit": explicit,
                         "arch": host_arch(log_lines, target_arch)})


def _explicit(log_lines, name: str) -> bool:
    """The package's own command line names -Werror=<name> (which wins over us)."""
    pattern = re.compile(r"(?:^|\s)-Werror=" + re.escape(name) + r"(?=\s|$)")
    return any(pattern.search(_strip(line)) and "error:" not in line and "supports" not in line
               for line in log_lines)


def expand(worktree, match: Match, params: dict) -> list:
    names = list(match.params["names"])
    for name in match.params.get("explicit") or []:
        raise Refusal(f"the package's own compile command names -Werror={name}, which a "
                      f"-Wno-error={name} in the build flags does not override.")
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    flags = " ".join(f"-Wno-error={name}" for name in names)
    if all(f"-Wno-error={name}" in text for name in names):
        raise Refusal(f"debian/rules already has {flags}.")
    arch = match.params.get("arch") or ""
    body = (f"export DEB_CFLAGS_MAINT_APPEND += {flags}\n"
            f"export DEB_CXXFLAGS_MAINT_APPEND += {flags}\n")
    comment = f"# A newer GCC reports {', '.join(names)} as errors under the package's -Werror\n"
    add = comment + (f"ifeq ($(DEB_HOST_ARCH),{arch})\n{body}endif\n" if arch else body)
    # after an existing DEB_C*FLAGS_MAINT_APPEND export, so a later "=" cannot drop ours
    old, new = rules_mk.insertion(text, add, ("DEB_CFLAGS_MAINT_APPEND", "DEB_CXXFLAGS_MAINT_APPEND",
                                              "DEB_BUILD_MAINT_OPTIONS"))
    after = text.replace(old, new, 1)
    tail = after[after.index(new) + len(new):]
    if re.search(r"^\s*(?:export\s+)?DEB_C(?:XX)?FLAGS_MAINT_APPEND\s*:?=", tail, re.MULTILINE):
        raise Refusal("debian/rules assigns DEB_CFLAGS/CXXFLAGS_MAINT_APPEND with = further down, "
                      "which would drop the new flags; add -Wno-error to that line yourself.")
    rules_mk.checked(text, old, new, tokens=(flags,))
    match.params["_report"] = (flags, arch)
    return [Edit(path, old, new)]


def summary(edits, match, params) -> str:
    flags, arch = match.params["_report"]
    where = f" for {arch}" if arch else ""
    return f"exported {flags} in DEB_CFLAGS/CXXFLAGS_MAINT_APPEND{where}"


register(Recipe(
    ID, "verified-template",
    "Exports -Wno-error=<warning> in DEB_CFLAGS/CXXFLAGS_MAINT_APPEND (in an ifeq on the failing "
    "architecture) in debian/rules, for each warning the log shows as [-Werror=...]. new_text: empty.",
    recognise, expand, summary))
