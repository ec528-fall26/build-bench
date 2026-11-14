"""gcs-report-dynamic-none: a dependency without the GCS marking stops an arm64 link.

efibootmgr (resolute), kdsoap, grantlee5, kf6-frameworkintegration (questing),
all arm64: objects built with -mbranch-protection=standard (dpkg-buildflags
"branch" hardening) are GCS-marked, a shared library they link against
(libpopt 1.19+dfsg-2, Qt 6.8.3) is not, ld (binutils 2.45) says

    libpopt.so: warning: GCS is required by -z gcs, but this shared library
    lacks the necessary property note. ...

and the package links with -Wl,--fatal-warnings, so collect2 fails. Reproduced
on 13 November 2026 with the binutils and library versions of the logs
(snapshot.ubuntu.com) and verified with real arm64 package builds of efibootmgr
18-2ubuntu2 and kdsoap 2.2.0+dfsg-3ubuntu2:

  -Wl,-z,gcs-report-dynamic=none in an exported DEB_LDFLAGS_MAINT_APPEND   PASS (both)
  -Wl,--no-fatal-warnings in LDFLAGS      FAIL on efibootmgr: its own Makefile
                                          puts --fatal-warnings after LDFLAGS
  -Wl,-z,nogcs                            FAIL: "-z nogcs ignored" is itself a warning
  --no-gcs                                not an ld option (rc.5 lost efibootmgr on it)
  hardening=-branch                       passes, but turns PAC/BTI off: not used

efibootmgr's debian/rules already has the right line, not exported, so
dpkg-buildflags never sees it; adding `export` to that line alone passes.

Standard library only.
"""

from __future__ import annotations

import re

from ..types import Edit
from . import rules_mk
from .registry import Match, Recipe, Refusal, host_arch, read, register

ID = "gcs-report-dynamic-none"
OPTION = "-Wl,-z,gcs-report-dynamic=none"
_WARNING = re.compile(r"warning: GCS is required by -z gcs, but this shared library lacks")
_LINK_FAILED = re.compile(r"collect2: error: ld returned|final link failed")
# Another reason for the same link to fail: then this recipe is not the repair.
_OTHER_CAUSE = re.compile(r"undefined reference to|cannot find -l|multiple definition of|"
                          r"relocation R_\w+ against|: error: (?!ld returned)")
_ASSIGNED = re.compile(r"^(?P<indent>[ \t]*)(?P<export>export[ \t]+)?DEB_LDFLAGS_MAINT_APPEND[ \t]*"
                       r"[:+?]?=[^\n]*gcs-report-dynamic=none[^\n]*\n", re.MULTILINE)
AFTER = 40          # collect2 follows the warnings of one link within this many lines


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    arch = host_arch(log_lines, target_arch)
    if arch not in ("arm64", "aarch64", ""):
        return None
    first, count, libraries = None, 0, []
    for index, line in enumerate(log_lines):
        if not isinstance(line, str) or not _WARNING.search(line):
            continue
        count += 1
        name = line.split(": warning:", 1)[0].rsplit("/", 1)[-1].strip()
        if name and name not in libraries:
            libraries.append(name)
        if first is not None:
            continue
        window = [text for text in log_lines[index + 1:index + 1 + AFTER] if isinstance(text, str)]
        failed = next((n for n, text in enumerate(window) if _LINK_FAILED.search(text)), None)
        if failed is None:
            continue                       # this link went through: a warning only
        between = [text for text in window[:failed] if not _WARNING.search(text)]
        if any(_OTHER_CAUSE.search(text) for text in between):
            continue
        first = index
    if first is None or not read(worktree, "input/debian/rules"):
        return None
    return Match(ID, evidence=[log_lines[first].strip()[:300],
                               f"({count} such warnings, for {', '.join(libraries[:4])}; the link then fails)"],
                 params={"arch": "arm64", "libraries": libraries[:4]}, line=first)


def expand(worktree, match: Match, params: dict) -> list:
    path = "input/debian/rules"
    text = read(worktree, path)
    if not text:
        raise Refusal("input/debian/rules cannot be read.")
    present = list(_ASSIGNED.finditer(text))
    if present:
        # efibootmgr: the maintainer's line is there but make never exports it.
        bare = [found for found in present if not found.group("export")]
        exported = re.search(r"^[ \t]*export[ \t]+(?:[\w ]*\s)?DEB_LDFLAGS_MAINT_APPEND\b(?![ \t]*[:+?]?=)",
                             text, re.MULTILINE) or re.search(r"^[ \t]*export[ \t]*$", text, re.MULTILINE)
        if not bare or exported:
            raise Refusal(f"debian/rules already exports {OPTION}; the failure has another cause.")
        if len(bare) > 1 or text.count(bare[0].group(0)) != 1:
            raise Refusal("debian/rules assigns the option on more than one line; add `export` yourself.")
        old = bare[0].group(0)
        new = f"{bare[0].group('indent')}export {old[len(bare[0].group('indent')):]}"
        match.params["_report"] = "exported the existing DEB_LDFLAGS_MAINT_APPEND line (it was assigned but not exported)"
        return [Edit(path, old, new)]
    add = ("# A dependency lacks the GCS marking; ld's warning about it is fatal with --fatal-warnings\n"
           "ifeq ($(DEB_HOST_ARCH),arm64)\n"
           f"export DEB_LDFLAGS_MAINT_APPEND += {OPTION}\n"
           "endif\n")
    old, new = rules_mk.insertion(text, add, ("DEB_LDFLAGS_MAINT_APPEND", "DEB_BUILD_MAINT_OPTIONS"))
    after = text.replace(old, new, 1)
    tail = after[after.index(new) + len(new):]
    if re.search(r"^\s*(?:export\s+)?DEB_LDFLAGS_MAINT_APPEND\s*:?=", tail, re.MULTILINE):
        raise Refusal("debian/rules assigns DEB_LDFLAGS_MAINT_APPEND with = further down, which would "
                      f"drop the new entry; add {OPTION} to that line yourself.")
    rules_mk.checked(text, old, new, tokens=(OPTION,))
    match.params["_report"] = f"exported DEB_LDFLAGS_MAINT_APPEND += {OPTION} for arm64"
    return [Edit(path, old, new)]


def summary(edits, match, params) -> str:
    return match.params.get("_report", f"added {OPTION}")


register(Recipe(
    ID, "verified",
    "Exports DEB_LDFLAGS_MAINT_APPEND += -Wl,-z,gcs-report-dynamic=none for arm64 in debian/rules (or "
    "adds the missing `export` to a line that already assigns it), when ld's \"GCS is required by -z gcs\" "
    "warning about a dependency fails a --fatal-warnings link. Verified with real builds of efibootmgr "
    "and kdsoap. new_text: empty.",
    recognise, expand, summary))
