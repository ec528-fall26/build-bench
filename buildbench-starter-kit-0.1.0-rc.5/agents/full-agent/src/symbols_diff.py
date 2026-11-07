"""Turn dpkg-gensymbols's diff in the build log into ready edits of the symbols file.

When a library's exported symbols differ from debian/<pkg>.symbols, dh_makeshlibs
fails with "dpkg-gensymbols: error: some symbols or patterns disappeared" and the
log carries a unified diff from the symbols file to what was built:

    --- debian/libbpp-core4.symbols.amd64 (libbpp-core4_2.4.1-6_amd64)
    +++ dpkg-gensymbolsXOuVXk
    @@ -74,7 +74,7 @@
      <context symbol>
    - <symbol that disappeared>
    +#MISSING: 2.4.1-6# <symbol that disappeared>
    + <symbol that appeared>

Updating the symbols file from that diff is the documented Debian procedure
(dpkg-gensymbols(1), "Using symbols files"). Cross-architecture builds hit it
for C++ libraries, whose template instantiations differ per compiler and arch:
13 of the 200 public cases (libbpp-*, jsonnet, libleidenalg, libkcddb, ...).

Without builds the model has to copy hundreds of mangled names exactly, and in
the 7 November local run (libbpp-core) it instead proposed marking every symbol
(optional) seven times, which the rules guard refuses as suppressing the check.
This module prepares one edit per hunk: a symbol that disappeared is marked
(optional) (the C++ guidance in dpkg-gensymbols(1): template instantiations may
legitimately vanish), a symbol that appeared is added, and every other line is
kept. Only the reported symbols change, so the check itself stays on.

Standard library only; every scan is bounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from .types import Edit

MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EDITS = 60
MAX_BLOCK_CHARS = 14_000

TITLE = "=== Symbols-file mismatch (dpkg-gensymbols diff from the log) ==="
END = "=== End of symbols-file mismatch ==="

_PREFIX = re.compile(r"^\[\s*\d+s\] ")
_ERROR = re.compile(r"dpkg-gensymbols: error:|dh_makeshlibs: error:")
_FROM = re.compile(r"^--- (debian/\S+\.symbols(?:\.[\w-]+)?)(?:\s|$)")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
_TAGS = re.compile(r"^(\s*)\(([^)]*)\)")


@dataclass
class Hunk:
    old: list[str] = field(default_factory=list)  # file lines before
    new: list[str] = field(default_factory=list)  # file lines after the repair
    appeared: int = 0
    disappeared: int = 0


def _optional(line: str) -> str:
    """The symbols-file line with the (optional) tag added."""
    tags = _TAGS.match(line)
    if tags:
        names = tags.group(2).split("|")
        if "optional" in names:
            return line
        return f"{tags.group(1)}({tags.group(2)}|optional){line[tags.end():]}"
    stripped = line.lstrip()
    return line[:len(line) - len(stripped)] + "(optional)" + stripped


def parse(lines: list[str]) -> dict[str, list[Hunk]]:
    """The diff's hunks per symbols file (paths relative to the package root)."""
    files: dict[str, list[Hunk]] = {}
    path = None
    hunk = None
    for raw in lines:
        line = _PREFIX.sub("", raw.rstrip("\n"))
        start = _FROM.match(line)
        if start:
            path, hunk = start.group(1), None
            continue
        if path is None:
            continue
        if line.startswith("+++ "):
            continue
        if _HUNK.match(line):
            hunk = Hunk()
            files.setdefault(path, []).append(hunk)
            continue
        if hunk is None:
            continue
        if line.startswith(" "):
            hunk.old.append(line[1:])
            hunk.new.append(line[1:])
        elif line.startswith("+#MISSING"):
            continue
        elif line.startswith("-"):
            text = line[1:]
            hunk.old.append(text)
            if "(optional" in text.split("@", 1)[0] or text.lstrip().startswith("#"):
                hunk.new.append(text)          # already allowed to disappear
            else:
                hunk.new.append(_optional(text))
                hunk.disappeared += 1
        elif line.startswith("+"):
            hunk.new.append(line[1:])
            hunk.appeared += 1
        else:
            path = hunk = None                 # the diff ended
    return files


def _read_log(log_path: Path) -> list[str]:
    with Path(log_path).open("rb") as handle:
        data = handle.read(MAX_LOG_BYTES)
    return data.decode("utf-8", errors="replace").splitlines()


def prepare(log_path: Path | None, worktree: Path | None) -> list[Edit]:
    """One edit per hunk whose text is in the worktree's symbols file exactly once."""
    if log_path is None or worktree is None:
        return []
    try:
        lines = _read_log(log_path)
    except OSError:
        return []
    if not any(_ERROR.search(line) for line in lines):
        return []
    edits: list[Edit] = []
    for relative, hunks in parse(lines).items():
        path = f"input/{relative}"
        target = Path(worktree) / path
        try:
            if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for hunk in hunks:
            if hunk.old == hunk.new or not hunk.old:
                continue
            old = "\n".join(hunk.old) + "\n"
            if text.count(old) != 1:
                continue
            edits.append(Edit(path=path, old_text=old, new_text="\n".join(hunk.new) + "\n"))
            if len(edits) >= MAX_EDITS:
                return edits
    return edits


def render(edits: list[Edit]) -> str:
    """The evidence block for the opening message, or "" without prepared edits."""
    if not edits:
        return ""
    files = sorted({e.path for e in edits})
    head = [TITLE,
            "The build failed in dh_makeshlibs: the library's exported symbols differ from "
            f"{', '.join(files)}. dpkg-gensymbols printed the difference; updating the "
            "symbols file from it is the standard Debian repair. The controller turned that "
            f"diff into {len(edits)} prepared edit(s): a symbol that disappeared is marked "
            "(optional), a symbol that appeared is added, every other line is unchanged. "
            "Submit them as they are (copy this JSON into your edits); do not mark the whole "
            "file optional, which the rules forbid.",
            "Prepared edits:"]
    body = json.dumps([{"path": e.path, "old_text": e.old_text, "new_text": e.new_text}
                       for e in edits], indent=1)
    if len(body) > MAX_BLOCK_CHARS:
        body = body[:MAX_BLOCK_CHARS] + (
            "\n... [truncated: the controller applies all prepared edits if you submit the "
            "first ones or propose a symbols edit it refuses]")
    return "\n".join(head + [body, END])
