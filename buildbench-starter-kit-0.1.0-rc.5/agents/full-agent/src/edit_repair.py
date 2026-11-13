"""Repair an edit batch mechanically before asking the model to correct it.

Without build feedback a batch is applied untested, and the no-build runs of
2-3 November (docs/no-build-live-findings.md) showed the controller's failure
handling costing more than the model's reasoning:

  - rust-subversion proposed 26 edits; one was a no-op, so after 12 failed
    corrections none of the 26 was written;
  - libunwind sent the same old_text ten times because the real line had three
    tabs where the model wrote two;
  - libunwind wrote "DEB_CFLAGS_MAINT_APPEND += ..." without "export", so the
    flag reached no compile; on 19 October it inserted a line into the middle
    of a backslash-continued Makefile variable.

Everything here is deterministic and conservative: an edit is changed only
when the intended result is unambiguous (a unique match that differs in
whitespace alone; an export the flag needs to take effect), and an edit is
dropped only when it can do nothing (no change, an exact duplicate, a change
an earlier edit already made). Anything else still goes back to the model.

Standard library only.
"""

from __future__ import annotations

from copy import copy
from pathlib import Path, PurePosixPath
import re

from .edit_applier import EditRejected, replacement
from .edit_preflight import _covered, _is_makefile, inspect_batch, window_matches


# dpkg-buildflags reads these from the environment: a plain make assignment in
# debian/rules has no effect on any compile. Only make-level lines qualify: a
# tab-indented line is a shell recipe, where "VAR=value command" is valid and
# "export VAR=value command" would run no command.
_MAINT_FLAG = re.compile(
    r"^(?P<indent> *)(?P<name>DEB_(?:[A-Z]+FLAGS_MAINT_(?:APPEND|PREPEND|STRIP|SET)"
    r"|BUILD_MAINT_OPTIONS))\s*[:+?]?=[^\n]*$")
_EXPORTED = re.compile(r"^[ \t]*export\s+")
_INDENT = re.compile(r"^[ \t]*")


def _is_rules(path: str) -> bool:
    return PurePosixPath(path).parts[-2:] == ("debian", "rules")


def exported_maint_flags(edit):
    """The edit with "export" before any DEB_*_MAINT_* line it adds to debian/rules.

    Returns (edit, [names]); the edit is the same object when nothing changed.
    Lines the edit merely keeps (present in its old_text) are left alone.
    """
    if not _is_rules(edit.path) or not isinstance(edit.new_text, str):
        return edit, []
    kept = set(edit.old_text.splitlines()) if isinstance(edit.old_text, str) else set()
    lines = edit.new_text.split("\n")
    names = []
    for index, line in enumerate(lines):
        match = _MAINT_FLAG.match(line.rstrip("\r"))
        if match and not _EXPORTED.match(line) and line.rstrip("\r") not in kept:
            lines[index] = f"{match.group('indent')}export {line[len(match.group('indent')):]}"
            names.append(match.group("name"))
    if not names:
        return edit, []
    fixed = copy(edit)
    fixed.new_text = "\n".join(lines)
    return fixed, names


def relax_whitespace(before: str, edit):
    """The edit with its whitespace corrected to the file's, or None.

    Applies only to the one mistake it was written for (libunwind, 3
    November: the real lines had three tabs, the model wrote two, ten times):
    old_text is not in the file, exactly one run of lines matches it apart
    from leading and trailing whitespace, no indented line was written at
    column 0, each model indent stands for one real indent, and every
    non-blank line of new_text uses an indent seen in old_text. The
    mapping is then applied to new_text so the replacement keeps the file's
    indentation. Any other difference (lines the model wrote at column 0, an
    inserted line with its own indentation, CRLF, a whitespace-only line,
    mixed units, a new line of a Makefile that would gain a leading tab and so
    read as a recipe line) is left to the model, which is shown the exact text
    instead (edit_preflight.whitespace_window). Never changes anything but
    whitespace.
    """
    old = edit.old_text
    if (not isinstance(old, str) or not old or old in before
            or getattr(edit, "replace_all", False) or not isinstance(edit.new_text, str)):
        return None
    wanted = [line.strip() for line in old.split("\n")]
    trailing_newline = old.endswith("\n")
    if trailing_newline:
        wanted = wanted[:-1]
    if not wanted or not all(wanted):
        return None  # a blank line's indentation means nothing
    file_lines = before.split("\n")
    starts = window_matches(file_lines, wanted)
    if len(starts) != 1:
        return None
    start = starts[0]
    actual_lines = file_lines[start:start + len(wanted)]
    if any(line.endswith("\r") for line in actual_lines) or "\r" in old or "\r" in edit.new_text:
        return None
    if trailing_newline and start + len(wanted) >= len(file_lines):
        return None  # the file's last line has no newline to match
    exact = "\n".join(actual_lines) + ("\n" if trailing_newline else "")
    if before.count(exact) != 1:
        return None
    mapping: dict[str, str] = {}
    for written, actual in zip(old.split("\n"), actual_lines):
        model_indent = _INDENT.match(written).group(0)
        real_indent = _INDENT.match(actual).group(0)
        if mapping.setdefault(model_indent, real_indent) != real_indent:
            return None
        if not model_indent and real_indent:
            return None  # an indented line written at column 0: see the docstring
    kept = set(wanted)
    makefile = _is_makefile(edit.path)
    new_lines = []
    for line in edit.new_text.split("\n"):
        indent = _INDENT.match(line).group(0)
        if line.strip() and indent not in mapping:
            return None
        if (makefile and line.strip() and line.strip() not in kept
                and mapping[indent].startswith("\t") and not indent.startswith("\t")):
            return None
        new_lines.append(mapping.get(indent, "") + line[len(indent):] if line.strip() else line)
    fixed = copy(edit)
    fixed.old_text = exact
    fixed.new_text = "\n".join(new_lines)
    return fixed


_SPACE_RUN = re.compile(r"[ \t]+")
INTERIOR_MIN_CHARS = 8


def _collapsed(line: str) -> str:
    return _SPACE_RUN.sub(" ", line.strip())


def relax_interior_whitespace(before: str, edit):
    """The edit with its old_text replaced by the file's own lines, or None.

    For the mistake relax_whitespace does not cover (xmbmon, rc.5 and rc.8 runs
    of the public cases: the file has `#ifdef LINUX` followed by four tabs and
    a comment, the model wrote three, four attempts in a row, and the case went
    in with no change): old_text is not in the file, but exactly one run of
    lines equals it once every run of spaces and tabs counts as one space, and
    each line's indentation is as the model wrote it. old_text becomes the
    file's text; a line of new_text that is one of those lines, whitespace
    aside, is replaced by the file's line too, so only what the model changed
    changes. Indentation differences stay with relax_whitespace; nothing but
    spaces and tabs is ever altered.
    """
    old = edit.old_text
    if (not isinstance(old, str) or not old or old in before or "\r" in old
            or getattr(edit, "replace_all", False) or not isinstance(edit.new_text, str)):
        return None
    written = old.split("\n")
    trailing_newline = old.endswith("\n")
    if trailing_newline:
        written = written[:-1]
    keys = [_collapsed(line) for line in written]
    if not keys or not keys[0] or not keys[-1] or max(len(key) for key in keys) < INTERIOR_MIN_CHARS:
        return None
    file_lines = before.split("\n")
    width = len(keys)
    starts = [start for start in range(len(file_lines) - width + 1)
              if all(_collapsed(file_lines[start + k]) == keys[k] for k in range(width))]
    if len(starts) != 1:
        return None
    start = starts[0]
    actual = file_lines[start:start + width]
    if any(line.endswith("\r") for line in actual):
        return None
    for mine, real in zip(written, actual):
        if mine.strip() and _INDENT.match(mine).group(0) != _INDENT.match(real).group(0):
            return None            # an indentation difference: relax_whitespace's case
    if trailing_newline and start + width >= len(file_lines):
        return None
    exact = "\n".join(actual) + ("\n" if trailing_newline else "")
    if exact == old or before.count(exact) != 1:
        return None
    by_key: dict[str, str | None] = {}
    for key, real in zip(keys, actual):
        if key:
            by_key[key] = real if by_key.get(key, real) == real else None
    new_lines = []
    for line in edit.new_text.split("\n"):
        real = by_key.get(_collapsed(line)) if line.strip() else None
        same_indent = real is not None and _INDENT.match(line).group(0) == _INDENT.match(real).group(0)
        new_lines.append(real if same_indent else line)
    fixed = copy(edit)
    fixed.old_text = exact
    fixed.new_text = "\n".join(new_lines)
    if fixed.new_text == fixed.old_text:
        return None                # the edit changed nothing but whitespace
    return fixed


ALREADY_MADE_MIN_CHARS = 12


def already_made(original: str, edit) -> bool:
    """Whether the edit's change is in the file as it is on disk: old_text
    occurs nowhere and new_text (specific enough to mean something) does."""
    old, new = getattr(edit, "old_text", None), getattr(edit, "new_text", None)
    if not isinstance(old, str) or not isinstance(new, str):
        return False
    if len(new.strip()) < ALREADY_MADE_MIN_CHARS or old in original:
        return False
    return new in original


_TAGGED = re.compile(r"^(?P<indent>\s*)\((?P<tags>[^)]*)\)\s*(?P<rest>\S.*)$")


def expand_macros(edits, worktree, log_lines, target_arch: str = "") -> tuple[list, list[str]]:
    """Replace macro edits with the concrete edits a recipe computes itself.

    An edit whose old_text is ``@@recipe:<id>@@`` (or the legacy alias
    ``@@tag-missing-symbols@@``, the symbols-tag recipe) invokes the recipe of
    that id (recipes/registry.py); its new_text carries optional plain-text
    parameters. The recipe re-checks its signature in the log and its
    preconditions in the tree and either expands to edits ("Recipe <id>
    expanded: ...") or refuses with a note and no edit; it never guesses.
    libbpp-core (round 10): the model retyped 4,131 lines in a syntax dpkg
    rejects when 13 needed a tag. Never raises: a failure is a refusal note.

    ``target_arch`` is the case's target when the caller knows it; recipes read
    the host architecture from the log otherwise.
    """
    out, notes = [], []
    try:
        from .recipes import registry
    except Exception:
        return list(edits), []
    expanded: set[tuple[str, str]] = set()
    for edit in edits:
        recipe_id = registry.macro_id(getattr(edit, "old_text", None))
        if recipe_id is None:
            out.append(edit)
            continue
        path = getattr(edit, "path", "")
        if (recipe_id, path) in expanded:
            notes.append(f"Recipe {recipe_id} on {path} was already expanded earlier in this batch; "
                         "the repeat was dropped.")
            continue
        params = registry.parse_params(getattr(edit, "new_text", ""))
        params["path"] = path
        made, note = registry.run(recipe_id, worktree, log_lines, target_arch, params)
        notes.append(note)
        if made:
            expanded.add((recipe_id, path))
            out.extend(made)
    if not expanded:  # a macro's edits carry the model's parameters; they are not copies
        out, restored = restore_retyped(out, worktree, log_lines, target_arch)
        notes.extend(restored)
    return out, notes


RETYPE_MIN_LINES = 5     # a block of code; one symbols line hand-tagged is not a copy of the recipe
RETYPE_SHARE = 0.8       # of the recipe's lines that appear in the hand edits


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _retyped_paths(edits, made) -> list[str]:
    """Paths where the hand edits hold most of a recipe's code block but are not the recipe's edits."""
    own_keys = {(own.path, own.old_text, own.new_text) for own in made}
    paths = []
    for path in dict.fromkeys(own.path for own in made):
        blocks = [own for own in made if own.path == path and own.new_text.count("\n") >= RETYPE_MIN_LINES]
        hand = [edit for edit in edits if edit.path == path]
        if not blocks or not hand or {(e.path, e.old_text, e.new_text) for e in hand} == {
                key for key in own_keys if key[0] == path}:
            continue
        typed = {line for edit in hand for line in _lines(getattr(edit, "new_text", "") or "")}
        wanted = [line for own in blocks for line in _lines(own.new_text)]
        if sum(line in typed for line in wanted) >= RETYPE_SHARE * len(wanted):
            paths.append(path)
    return paths


def restore_retyped(edits, worktree, log_lines, target_arch: str = "") -> tuple[list, list[str]]:
    """Swap a hand-typed copy of a verified recipe for the recipe's own edits.

    The model is shown a bounded preview of a verified recipe and told to send
    the macro; sometimes it retypes the preview instead. pybigwig, 9 November:
    in 0.6.0-rc.6 the copy wrote `labs =` for `libs =` and left out the second
    edit, which the 40-line preview had cut off; in rc.5 a rearranged copy
    built but no longer loaded. Hosted runs get one validation, so edits of a
    file that are a copy of the recipe's code block are replaced by the recipe.
    Only a verified recipe whose signature is in the log and whose edits still
    compute on this tree is used; other files' edits stay. Never raises.
    """
    try:
        from . import completion, patterns
        from .recipes import registry
        if not any((getattr(edit, "new_text", "") or "").count("\n") >= RETYPE_MIN_LINES for edit in edits):
            return list(edits), []
        out, notes = list(edits), []
        for recipe, match in registry.menu(log_lines, worktree, target_arch):
            if recipe.tier not in patterns.PREVIEW_TIERS:
                continue
            made, _ = registry.run(recipe.id, worktree, log_lines, target_arch,
                                   {"path": completion.recipe_target(recipe.id, match)})
            for path in _retyped_paths(out, made):
                first = next(index for index, edit in enumerate(out) if edit.path == path)
                own = [edit for edit in made if edit.path == path]
                out = ([e for e in out[:first]] + own + [e for e in out[first:] if e.path != path])
                notes.append(f"The edits of {path} were a retyped copy of the verified recipe {recipe.id}; the "
                             f"controller applied the recipe's own {len(own)} edit(s) instead. Do not retype it.")
        return out, notes
    except Exception:  # a safety net must never break a run
        return list(edits), []


def prune_batch(snapshot, edits, missing_symbol_count=None):
    """Mechanically repair a batch; return (edits, notes, dropped).

    Each note is one sentence for the model. ``dropped`` is the number of
    edits removed. Repeats until the first remaining issue is one only the
    model can resolve; the loop decides what to do with that one.
    """
    edits = list(edits)
    notes: list[str] = []
    dropped = 0
    originals = {saved.path: saved.content.decode("utf-8") for saved in snapshot.files}
    # Notes number edits as the model listed them, whatever was dropped since.
    positions = [getattr(edit, "_position", index + 1) for index, edit in enumerate(edits)]
    relaxed: set[int] = set()
    while edits:
        issues = inspect_batch(snapshot, edits, missing_symbol_count=missing_symbol_count)
        if not issues:
            break
        index, edit, reason, duplicate, before, _ = issues[0]
        position = f"edit {positions[index]} ({edit.path})"
        if reason == "no_change":
            notes.append(f"Dropped {position}: its old_text and new_text are identical, "
                         "so it changes nothing.")
        elif duplicate is not None and reason == "old_text_not_found":
            notes.append(f"Dropped {position}: it repeats edit {positions[duplicate]} exactly.")
        elif (reason == "old_text_not_found" and before is not None
              and _covered(originals[edit.path], edits[:index], edit, before)):
            notes.append(f"Dropped {position}: an earlier edit in the batch had already "
                         "made its change.")
        elif (reason == "old_text_not_found" and before is not None
              and already_made(originals[edit.path], edit)):
            # rust-subversion, 0.3.2 live: the first batch replaced every i8
            # with c_char; the next 58 turns proposed the same replace_all
            # again, were told old_text_not_found, and searched for it.
            notes.append(f"Dropped {position}: its old_text occurs nowhere in the file any "
                         "more and its new_text already does, so this change has already "
                         "been made (by an earlier attempt). Do not propose it again.")
        elif (reason == "old_text_not_found" and before is not None and index not in relaxed
              and (fixed := (relax_whitespace(before, edit)
                             or relax_interior_whitespace(before, edit)))):
            interior = relax_whitespace(before, edit) is None
            # The same text later in the batch is the same mistake: fix it too,
            # so a repeat is still recognised as a duplicate.
            same = (edit.path, edit.old_text, edit.new_text, getattr(edit, "replace_all", False))
            for later in range(index, len(edits)):
                item = edits[later]
                if (item.path, item.old_text, item.new_text,
                        getattr(item, "replace_all", False)) == same:
                    edits[later] = fixed if later == index else copy(fixed)
                    relaxed.add(later)
            if interior:
                notes.append(f"Corrected the whitespace of {position}: its old_text matched one "
                             "place in the file apart from the number of spaces and tabs inside "
                             "the lines, so the file's own lines were used for it.")
            else:
                notes.append(f"Corrected the whitespace of {position}: its old_text matched one "
                             "place in the file apart from indentation, so the file's indentation "
                             "was used for it and for new_text.")
            continue
        else:
            break
        del edits[index]
        del positions[index]
        relaxed = {i - 1 if i > index else i for i in relaxed if i != index}
        dropped += 1
    # Only an edit whose old_text matches as it stands can be judged make-level:
    # a space-indented line is a recipe if the model mis-wrote a tab (review).
    failing = {issue[0] for issue in inspect_batch(snapshot, edits, missing_symbol_count=missing_symbol_count)}
    exported = []
    for index in reversed(range(len(edits))):
        if index in failing:
            continue
        fixed, names = exported_maint_flags(edits[index])
        if names and fixed.new_text == fixed.old_text:
            # The model took "export" off an exported line (libunwind, 3
            # November: 37 turns of "export VAR += value is invalid", each
            # re-exported into a no-op and rejected as no_change). The line
            # is right as it stands, so the edit is dropped, with the reason.
            notes.append(f"Dropped edit {positions[index]} ({edits[index].path}): it would "
                         "only remove \"export\" from " + ", ".join(dict.fromkeys(names))
                         + ". \"export VAR += value\" is valid GNU make, and without the "
                         "export dpkg-buildflags never sees the flag, so the line was left "
                         "as it is. Do not propose this change again.")
            del edits[index]
            del positions[index]
            dropped += 1
            continue
        edits[index] = fixed
        exported.extend(names)
    if exported:
        notes.append("Added \"export\" before " + ", ".join(dict.fromkeys(exported))
                     + " in debian/rules: dpkg-buildflags reads these from the environment, "
                     "so an unexported assignment reaches no compile. \"export VAR += "
                     "value\" is valid GNU make; do not split it into two lines.")
    return edits, notes, dropped


# A wrong path (rust-subversion, 3 November: 16 edits named client.rs for
# functions that live in delta.rs; each cost one to three correction turns and
# was then given up). The text is moved to the one file that contains it.
RETARGET_MIN_CHARS = 24
RETARGET_MAX_FILES = 4000
RETARGET_MAX_BYTES = 48 * 1024 * 1024
_RETARGET_SKIP = ("/.pc/", "/.git/", "/debian/.debhelper/", "/target/")


def retarget_path(worktree, edit, *, patch_policy=None):
    """The one file under input/ that contains the edit's old_text exactly once,
    when the stated file does not contain it (or does not exist), or None.

    Conservative: the text must be specific (RETARGET_MIN_CHARS), the candidate
    must share the stated file's extension and pass the path policy, and no
    other file may contain the text at all.
    """
    from .candidates import CandidateSnapshot
    from .tools import collect_files
    old = getattr(edit, "old_text", None)
    path = getattr(edit, "path", None)
    if (not isinstance(old, str) or len(old.strip()) < RETARGET_MIN_CHARS
            or not isinstance(path, str) or not path.startswith("input/")):
        return None
    stated = Path(worktree) / path
    try:
        if stated.is_file() and old in stated.read_text(encoding="utf-8"):
            return None
    except (OSError, UnicodeError):
        return None
    root = Path(worktree) / "input"
    if not root.is_dir():
        return None
    try:
        files, _ = collect_files(root)
    except OSError:
        return None
    suffix = PurePosixPath(path).suffix
    found = None
    scanned = 0
    for candidate in files:
        relative = candidate.relative_to(worktree).as_posix()
        if relative == path or any(part in "/" + relative + "/" for part in _RETARGET_SKIP):
            continue
        if suffix and PurePosixPath(relative).suffix != suffix:
            continue
        try:
            size = candidate.stat().st_size
        except OSError:
            continue
        if size > 1024 * 1024 or scanned + size > RETARGET_MAX_BYTES:
            continue
        scanned += size
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        count = text.count(old)
        if not count:
            continue
        if count > 1 or found is not None:
            return None
        found = relative
    if found is None:
        return None
    try:
        CandidateSnapshot.capture(worktree, [found], patch_policy=patch_policy)
    except (OSError, ValueError):
        return None
    return found


_AM_CONDITIONAL = re.compile(r"^\s*(?:ifeq|ifneq|ifdef|ifndef)\b")


def static_warnings(path: str, before: str, after: str) -> list[str]:
    """Advice on an edit that will be applied; never blocks it."""
    warnings = []
    if PurePosixPath(path).name == "Makefile.am":
        added = set(after.split("\n")) - set(before.split("\n"))
        if any(_AM_CONDITIONAL.match(line) for line in added):
            warnings.append(
                f"{path}: automake does not know ifeq/ifdef in Makefile.am and fails on "
                "the matching endif (\"endif without if\") when the build regenerates the "
                "Makefile, which dh_autoreconf does in most Debian packages. Put a "
                "per-architecture flag in debian/rules (export "
                "DEB_CFLAGS_MAINT_APPEND inside an ifeq on DEB_HOST_ARCH) or behind an "
                "automake conditional (AM_CONDITIONAL in configure.ac).")
    return warnings


def simulate(snapshot, edits) -> dict[str, str]:
    """Each file's text after the batch; failures leave that edit out."""
    current = {saved.path: saved.content.decode("utf-8") for saved in snapshot.files}
    for edit in edits:
        try:
            current[edit.path], _ = replacement(current[edit.path], edit)
        except (EditRejected, KeyError, UnicodeError):
            continue
    return current
