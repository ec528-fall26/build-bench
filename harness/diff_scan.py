"""Classify a repair diff, and flag added build-time commands that modify files.

Part 4 checks each edit to a build file, but not what the commands it adds do
when the build runs: a build-time `sed -i` or `rm` can change any file. All three
first live rcran repairs worked this way. This scan narrows human review to the
runs that need it; it is a heuristic, not a guarantee — commands hidden behind
variables, `eval` or scripts are not detected.

Fix types, most review-worthy first:
    build_commands  an added build-time command modifies files
    source          upstream files changed (outside input/debian/)
    packaging       only Debian packaging files changed (input/debian/)
    none            no change
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re


_BUILD_FILE_NAMES = {"CMakeLists.txt", "meson.build", "configure", "configure.ac", "configure.in"}
_BUILD_FILE_SUFFIXES = {".mk", ".cmake", ".sh", ".spec"}

# Shell commands that change files. Matched on added lines of build files only.
_FILE_MODIFYING = re.compile(
    r"""(?:^|[\s;&|(`])(?:
          sed\b[^;&|]*?\s(?:-[a-zA-Z]*i|--in-place)   # sed -i / -ri / --in-place
        | perl\b[^;&|]*?\s-[a-zA-Z]*i                  # perl -i / -pi
        | (?:patch|rm|mv|cp|ln|truncate|tee|dd)\s      # commands that write or delete
        )""",
    re.VERBOSE,
)
# Output redirection into a file, e.g. `echo x > src/a.h`. Requires a space or
# separator before ">", so `#include <a.h>` and `=>`/`->` don't match; skips >&2,
# 2>&1 and /dev/null. (`cmd>file` without a space is not detected.)
_REDIRECT = re.compile(r"(?:^|[\s;&|(])>>?\s*(?!&|/dev/null)\S")


def _is_build_file(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        p.parts[-2:] == ("debian", "rules")
        or p.name.lower().startswith(("makefile", "gnumakefile"))
        or p.name in _BUILD_FILE_NAMES
        or p.suffix.lower() in _BUILD_FILE_SUFFIXES
    )


def _changed_files(diff: str) -> dict[str, list[str]]:
    """Map each changed path to its added lines (without the leading '+')."""
    files: dict[str, list[str]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].split("\t", 1)[0]
            if target == "/dev/null":
                current = None
                continue
            current = target[2:] if target.startswith(("a/", "b/")) else target
            files.setdefault(current, [])
        elif line.startswith("--- "):
            source = line[4:].split("\t", 1)[0]
            if source != "/dev/null":  # a deletion still names the file here
                current = source[2:] if source.startswith(("a/", "b/")) else source
                files.setdefault(current, [])
        elif current is not None and line.startswith("+"):
            files[current].append(line[1:])
    return files


def scan_diff(diff: str) -> dict:
    """Return fix_type, changed_files, build_commands and needs_review."""
    files = _changed_files(diff)
    commands = [
        f"{path}: {line.strip()}"
        for path, added in files.items() if _is_build_file(path)
        for line in added
        if not line.lstrip().startswith("#")
        and (_FILE_MODIFYING.search(line.lstrip(" \t@-+")) or _REDIRECT.search(line))
    ]
    if commands:
        fix_type = "build_commands"
    elif any(not path.startswith("input/debian/") for path in files):
        fix_type = "source"
    elif files:
        fix_type = "packaging"
    else:
        fix_type = "none"
    return {
        "fix_type": fix_type,
        "changed_files": sorted(files),
        "build_commands": commands,
        "needs_review": fix_type == "build_commands",
    }
