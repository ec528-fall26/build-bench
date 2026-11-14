"""debian/rules helpers shared by every recipe that writes make-level lines.

Three libunwind failures (docs/no-build-live-findings.md) came from the same
three mistakes, all about what GNU make does with a line: an assignment
without "export" never reaches dpkg-buildflags; an assignment or conditional
with a leading tab is a shell command in a recipe; an insert in the middle of
a backslash-continued variable swallows the next line. The helpers find a
safe place for new lines and `lint` checks the result mechanically.

Standard library only.
"""

from __future__ import annotations

import re

from .registry import Refusal

_EXPORT = re.compile(r"^export\s+(?P<name>[A-Za-z_]\w*)")
_COND_OPEN = re.compile(r"^\s*(?:ifeq|ifneq|ifdef|ifndef)\b")
_COND_CLOSE = re.compile(r"^\s*endif\b")
_MAKE_LEVEL = re.compile(r"^\t+(?:ifeq|ifneq|ifdef|ifndef|else|endif|export\s|[A-Za-z_]\w*\s*(?:[:+?]=|\s=))")
_ASSIGN = re.compile(r"^\s*(?:export\s+)?(?P<name>DEB_\w+)\s*[:+?]?=")


def _statement_end(lines: list[str], index: int) -> int:
    """Index of the last physical line of the make statement starting at ``index``."""
    while index < len(lines) - 1 and lines[index].rstrip("\r").endswith("\\"):
        index += 1
    return index


def conditional_depth(lines: list[str], index: int) -> int:
    """How many ifeq/ifdef blocks are open before line ``index``."""
    depth = 0
    for line in lines[:index]:
        if line.startswith("\t"):
            continue
        if _COND_OPEN.match(line):
            depth += 1
        elif _COND_CLOSE.match(line):
            depth = max(0, depth - 1)
    return depth


def has_assignment(text: str, name: str) -> bool:
    return any((m := _ASSIGN.match(line)) and m.group("name") == name for line in text.splitlines())


def find_export(text: str, names: tuple[str, ...]) -> int | None:
    """Index of the first make-level ``export NAME`` line for one of ``names``."""
    lines = text.split("\n")
    for wanted in names:
        for index, line in enumerate(lines):
            found = _EXPORT.match(line)
            if found and found.group("name") == wanted:
                return index
    return None


def insertion(text: str, add: str, prefer: tuple[str, ...] = ()) -> tuple[str, str]:
    """(old_text, new_text) that put the make-level block ``add`` (ends with a
    newline) after the preferred export line, else after the top export block,
    else before the first make-level line. old_text is unique in ``text``.

    Raises Refusal when the spot is inside a conditional or no unique anchor
    exists, instead of choosing a doubtful one.
    """
    lines = text.split("\n")
    anchor = None
    after = True
    index = find_export(text, prefer) if prefer else None
    if index is not None:
        anchor = _statement_end(lines, index)
    else:
        exports = [i for i, line in enumerate(lines) if _EXPORT.match(line)]
        if exports:
            end = exports[0]
            while True:
                end = _statement_end(lines, end)
                if end + 1 < len(lines) and _EXPORT.match(lines[end + 1]):
                    end += 1
                    continue
                break
            anchor = end
        else:
            for i, line in enumerate(lines):
                if line.strip() and not line.lstrip().startswith("#") and not line.startswith("#!"):
                    anchor, after = i, False
                    break
    if anchor is None:
        raise Refusal("debian/rules has no make-level line to put the new lines next to.")
    if conditional_depth(lines, anchor) or lines[anchor].startswith("\t"):
        raise Refusal("the place for the new lines is inside a conditional or a recipe; "
                      "add them yourself at the top of debian/rules.")
    block = [anchor]
    while True:
        old = "\n".join(lines[i] for i in block) + ("\n" if block[-1] < len(lines) - 1 else "")
        if text.count(old) == 1:
            break
        if block[-1] + 1 >= len(lines) or len(block) > 4:
            raise Refusal("no unique anchor line in debian/rules for the new lines.")
        block.append(block[-1] + 1)
    if after:
        if old.endswith("\n"):
            return old, old + add
        return old, old + "\n" + add.rstrip("\n")
    return old, add + old


_RULE = re.compile(r"^(?![\t #])[^\s:=#][^=#\n]*?(?<!:):(?![:=])")


def before_first_rule(text: str, add: str) -> tuple[str, str] | None:
    """(old_text, new_text) that put the make-level block ``add`` immediately
    before the first rule of debian/rules (usually ``%:``), or None when that
    line is inside a conditional or is not unique. Lines placed there come
    after every assignment in the header, whatever conditional it sits in."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not _RULE.match(line) or line.startswith(("export ", "include ", "-include ", "override ", "define ")):
            continue
        if conditional_depth(lines, index):
            return None
        old = line + "\n"
        if text.count(old) != 1 or not text.count("\n" + old):
            return None
        return old, add + old
    return None


def assigned_after(text: str, marker: str, names: tuple[str, ...]) -> bool:
    """True when a plain (= or :=) assignment to one of ``names`` follows
    ``marker`` outside every conditional, which would discard an earlier +=."""
    lines = text.split("\n")
    start = text[:text.index(marker) + len(marker)].count("\n")
    for index in range(start, len(lines)):
        found = _ASSIGN.match(lines[index])
        if (found and found.group("name") in names and not lines[index].startswith("\t")
                and re.search(r"(?<![+?])=", lines[index].split(found.group("name"), 1)[1][:4])
                and not conditional_depth(lines, index)):
            return True
    return False


def lint(text: str, added: str, tokens: tuple[str, ...] = ()) -> list[str]:
    """Problems with the make-level lines ``added`` to debian/rules ``text``
    (already containing them). Empty means the lines are well formed."""
    problems = []
    opened = 0
    for line in added.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _MAKE_LEVEL.match(line):
            problems.append(f"leading tab on a make-level line: {stripped}")
        if line.startswith("\t"):
            continue  # a recipe line, judged by its own rules
        found = _ASSIGN.match(line)
        if found and re.match(r"^\s*DEB_\w*(?:FLAGS_MAINT_\w+|BUILD_MAINT_OPTIONS)\b", line) \
                and not re.match(r"^\s*export\s", line):
            problems.append(f"missing export on {found.group('name')}")
        if _COND_OPEN.match(line):
            opened += 1
        elif _COND_CLOSE.match(line):
            opened -= 1
    if opened:
        problems.append("ifeq/ifneq without a matching endif in the added lines")
    for token in tokens:
        if token not in text:
            problems.append(f"expected token missing: {token}")
    return problems


def checked(text_before: str, old: str, new: str, tokens: tuple[str, ...] = ()) -> None:
    """Refuse unless the edit old -> new yields well-formed make-level lines."""
    after = text_before.replace(old, new, 1)
    added = new[len(old):] if new.startswith(old) else (new[:-len(old)] if new.endswith(old) else new)
    problems = lint(after, added, tokens)
    if problems:
        raise Refusal("the computed debian/rules lines failed the linter (" + "; ".join(problems) + ").")


def check_recipe_lines(edits_new_text: str) -> None:
    """Refuse unless every command line of a written recipe starts with a tab
    (a space-indented command is a make syntax error: "missing separator")."""
    for line in edits_new_text.split("\n"):
        if re.match(r"^ +(?:@|-|\+)?(?:dh_\w+|\$\(MAKE\)|\./)", line):
            raise Refusal("the computed recipe line is not tab-indented: " + line.strip())
