"""promote-indep-build-dep: a tool the arch-only build needs is only in Build-Depends-Indep.

libmbim (jammy, arm64): `autoreconf` stops with `Can't exec "gtkdocize"`. The
source has `Build-Depends-Indep: gtk-doc-tools <!nodoc>`, and Launchpad built
arm64 with `dpkg-buildpackage -B` (Build Type: any), which installs
Build-Depends and Build-Depends-Arch only. amd64 was a full build, so the tool
was there. python-msgspec (oracular, arm64) is the same shape: pybuild says
"Please Build-Depend on pybuild-plugin-pyproject", which is listed under
Build-Depends-Indep.

The maintainer fix is to list the package where the arch-only build reads it,
in Build-Depends. In rc.5 the model instead commented out `GTK_DOC_CHECK` in
libmbim's configure.ac, and automake then failed on the conditionals that
macro defines. python-msgspec passed in rc.5 with exactly this edit.

Verified on 13 November 2026 with real arm64 builds of libmbim in a jammy
container with only the arch-only dependencies installed (`apt-get build-dep
--arch-only`, `dpkg-buildpackage -B`): the unmodified source FAILS with the
log's `Can't exec "gtkdocize"`; with this recipe's line in Build-Depends the
tool is installed and the build PASSES. The validator itself runs the full
build and installs the indep packages too (its rc.5 libmbim log retrieves
gtk-doc-tools), so there the edit is harmless and what matters is not
breaking anything else.

Recognised only when (1) the log is an arch-only build, (2) the log lists the
packages it installed and a Build-Depends-Indep package is not among them, and
(3) an error line names that package, or names a missing program or Python
module whose name matches it. Refused when the case's frozen dependency set is
known and does not contain the package.

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from .registry import Match, Recipe, Refusal, read, register

ID = "promote-indep-build-dep"
CONTROL = "input/debian/control"
_ARCH_ONLY = re.compile(r"dpkg-buildpackage\b.*\s-B(?:\s|$)|^Build Type: any\s*$|debian/rules (?:binary|build)-arch\b")
_INSTALLED = re.compile(r"^(?:Unpacking|Setting up|Selecting previously unselected package) "
                        r"(?P<name>[a-z0-9][a-z0-9+.-]*)")
_MISSING = (
    re.compile(r"Can't exec \"(?P<tool>[\w.+-]+)\""),
    re.compile(r"(?:^|\s)(?:/bin/)?(?:sh|bash|dash): (?:\d+: |line \d+: )?(?P<tool>[\w.+-]+): (?:command )?not found"),
    re.compile(r"^(?:make(?:\[\d+\])?: )?(?P<tool>[\w.+-]+): (?:command not found|No such file or directory)\s*$"),
    re.compile(r"No module named '?(?P<module>[\w.]+)'?"),
    re.compile(r"(?:Could not find|Program|program) '?(?P<tool>[\w.+-]+)'? (?:not found|found: NO)"),
)
_FIELD = re.compile(r"^(?P<name>Build-Depends(?:-Indep|-Arch)?)[ \t]*:(?P<body>[^\n]*(?:\n[ \t][^\n]*)*)\n?",
                    re.MULTILINE)
WINDOW = 400          # log lines around the first error that are searched


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def fields(control: str) -> dict:
    """Build-Depends* fields of the source stanza: name -> (whole field text, [(package, entry)])."""
    stanza = control.split("\n\n", 1)[0] + "\n"
    out = {}
    for found in _FIELD.finditer(stanza):
        entries = []
        for raw in found.group("body").replace("\n", " ").split(","):
            raw = " ".join(raw.split())
            name = re.match(r"([a-z0-9][a-z0-9+.-]*)", raw)
            if name:
                entries.append((name.group(1), raw))
        out[found.group("name")] = (found.group(0), entries)
    return out


def installed_packages(log_lines) -> set:
    return {found.group("name") for line in log_lines if isinstance(line, str)
            for found in [_INSTALLED.match(line)] if found}


def _names(package: str, line: str) -> str | None:
    """How this log line names the package: literally, or as a missing program or module."""
    if re.search(r"(?<![\w.+-])" + re.escape(package) + r"(?![\w+-])", line):
        return package
    for pattern in _MISSING:
        found = pattern.search(line)
        if not found:
            continue
        if found.groupdict().get("module"):
            module = found.group("module").split(".")[0].replace("_", "-").lower()
            if package in (f"python3-{module}", f"python-{module}", f"pybuild-plugin-{module}"):
                return found.group("module")
            continue
        tool, wanted = _norm(found.group("tool")), _norm(package)
        if len(tool) < 4:
            continue
        shared = 0
        while shared < min(len(tool), len(wanted)) and tool[shared] == wanted[shared]:
            shared += 1
        if wanted == tool or shared >= max(5, (len(tool) * 3 + 4) // 5):
            return found.group("tool")
    return None


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    control = read(worktree, CONTROL)
    if not control:
        return None
    declared = fields(control)
    indep = declared.get("Build-Depends-Indep", ("", []))[1]
    if not indep:
        return None
    text = [line if isinstance(line, str) else "" for line in log_lines]
    if not any(_ARCH_ONLY.search(line) for line in text):
        return None
    installed = installed_packages(text)
    if len(installed) < 5:
        return None                         # the log does not show what was installed
    already = {name for name, _ in declared.get("Build-Depends", ("", []))[1]}
    absent = [(name, raw) for name, raw in indep if name not in installed and name not in already]
    if not absent:
        return None
    from .. import patterns
    first = patterns.first_fatal_index(text)
    low, high = max(0, first - 40), min(len(text), first + WINDOW)
    for index in range(low, high):
        line = text[index]
        if not re.search(r"rror|not found|No such file|Can't exec|No module|^E: |failed", line):
            continue
        for name, raw in absent:
            how = _names(name, line)
            if how:
                return Match(ID, evidence=[line.strip()[:240],
                                           f"({name} is in Build-Depends-Indep; the arch-only build did not install it)"],
                             params={"package": name, "entry": raw, "named": how}, line=index)
    return None


def expand(worktree, match: Match, params: dict) -> list:
    control = read(worktree, CONTROL)
    if not control:
        raise Refusal("input/debian/control cannot be read.")
    declared = fields(control)
    name, entry = match.params["package"], match.params["entry"]
    if "Build-Depends" not in declared:
        raise Refusal("debian/control has no Build-Depends field to extend.")
    field, entries = declared["Build-Depends"]
    if any(existing == name for existing, _ in entries):
        raise Refusal(f"{name} is already in Build-Depends; the failure has another cause.")
    from .. import syntax_guard
    locked = syntax_guard.locked_packages(worktree)
    if locked is not None and name not in locked:
        raise Refusal(f"{name} is not among the case's frozen dependencies, so the build could not install it.")
    if control.count(field) != 1:
        raise Refusal("the Build-Depends field is not unique in debian/control.")
    body = field.rstrip("\n").rstrip()
    body = body[:-1].rstrip() if body.endswith(",") else body
    return [Edit(CONTROL, field, f"{body},\n {entry}\n")]


def summary(edits, match, params) -> str:
    return (f"added {match.params['entry']} to Build-Depends (it was only in Build-Depends-Indep, which an "
            "arch-only build does not install)")


register(Recipe(
    ID, "verified",
    "Adds to Build-Depends in debian/control the Build-Depends-Indep package that the log's error names "
    "(by package name, or as a missing program or Python module), when the failing build was arch-only and "
    "did not install it. new_text: empty.",
    recognise, expand, summary))
