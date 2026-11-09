"""symbols-tag: tag the symbols dpkg-gensymbols reports missing.

libbpp-core (round 10): the model retyped 4,131 lines of a symbols file where
13 needed a tag. libbpp-qt (9 Nov, validator): `(optional=templinst)` glued to
the symbol passed with zero parse warnings, while the space-separated form the
model chose took no effect. The tag is chosen here from the file name and the
symbol, so the model has nothing to get wrong:

  - template instantiations and std/Qt internals: ``optional=templinst``
    (such a symbol is emitted only where a compiler instantiates it);
  - anything else in an architecture-specific file (``*.symbols.amd64``):
    ``optional``;
  - anything else in a generic file: ``arch=!<failing arch>``.

Standard library only.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re

from .. import archnames, patterns
from ..types import Edit
from .registry import Match, Recipe, Refusal, find_line, host_arch, read, register

ID = "symbols-tag"
_DISAPPEARED = re.compile(r"dpkg-gensymbols: error: some symbols or patterns disappeared")
_TAGGED = re.compile(r"^(?P<indent>\s*)\((?P<tags>[^)]*)\)\s*(?P<rest>\S.*)$")
_ARCH_SUFFIX = re.compile(r"\.symbols\.(?P<arch>[\w-]+)$")
# Itanium-mangled names: std, libstdc++ internals and Qt containers, or a
# template argument list (an I after an identifier, followed by a type).
_MANGLED_INTERNAL = re.compile(r"^_ZN?K?(?:St|9__gnu_cxx|N?\d+Qt|\d+Q[A-Z]\w*)")
_MANGLED_TEMPLATE = re.compile(r"[A-Za-z_]\d*[A-Za-z_]I(?:[NPRKS]|\d|[bcahstijlmxynofdev]E)")
_DEMANGLED_TEMPLATE = re.compile(r"[<>]|\bstd::|\b__gnu_cxx::|\bQt[A-Z]|\bQ[A-Z]\w+<")
MAX_MISSING = 5000


def is_template_or_internal(name: str, line: str = "") -> bool:
    """Whether the symbol is a template instantiation or std/Qt internal."""
    bare = name.strip('"')
    if bare.startswith("_Z"):
        return bool(_MANGLED_INTERNAL.match(bare) or _MANGLED_TEMPLATE.search(bare.split("@")[0]))
    return bool(_DEMANGLED_TEMPLATE.search(bare) or (line.lstrip().startswith("(c++") and _DEMANGLED_TEMPLATE.search(line)))


def arch_specific(path: str) -> str:
    """The architecture in a ``*.symbols.<arch>`` file name, else ""."""
    found = _ARCH_SUFFIX.search(PurePosixPath(path).name)
    if found and (archnames.debian(found.group("arch")) or found.group("arch") in archnames.DEBIAN_NAMES):
        return found.group("arch")
    return ""


def choose_tag(name: str, line: str, path: str, arch: str) -> str:
    if is_template_or_internal(name, line):
        return "optional=templinst"
    if arch_specific(path) or not arch:
        return "optional"
    return f"arch=!{arch}"


def _find_line(text_lines: list[str], name: str) -> int | None:
    """The one line naming the symbol as a whole token, or None (also when ambiguous)."""
    hits = []
    for index, line in enumerate(text_lines):
        at = line.find(name)
        while at != -1:
            before = line[at - 1] if at else " "
            after = line[at + len(name)] if at + len(name) < len(line) else " "
            if before in " \t)\"" and after in " \t\"":
                hits.append(index)
                break
            at = line.find(name, at + 1)
    return hits[0] if len(hits) == 1 else None


def recognise(log_lines, worktree, target_arch="") -> Match | None:
    at = find_line(log_lines, _DISAPPEARED)
    if at is None:
        return None  # the "some new symbols appeared" warning alone is harmless
    missing = patterns.missing_symbols(log_lines)
    if not missing:
        return None
    files = []
    for line in log_lines[at:at + 4000]:
        found = patterns._SYMBOLS_FILE.search(line)
        if found:
            files.append("input/debian/" + PurePosixPath(found.group("path")).name)
    return Match(ID, evidence=[log_lines[at]] + [f"missing: {name}" for name in missing[:6]],
                 params={"file": files[0] if files else "", "missing": missing,
                         "arch": host_arch(log_lines, target_arch)}, line=at)


def expand(worktree, match: Match, params: dict) -> list:
    path = params.get("path") or match.params.get("file") or ""
    if ".symbols" not in PurePosixPath(path).name:
        path = match.params.get("file") or path
    if ".symbols" not in PurePosixPath(path).name:
        raise Refusal(f"{path or 'the edit'} is not a symbols file (debian/*.symbols); name it.")
    text = read(worktree, path)
    if not text:
        raise Refusal(f"{path} cannot be read.")
    explicit = (params.get("_raw") or "").strip().strip("()")
    if explicit.lower() in ("", "auto"):
        explicit = ""
    lines = text.split("\n")
    missing = match.params["missing"][:MAX_MISSING]
    arch = match.params.get("arch", "")
    edits, tags, already, absent = [], {}, 0, 0
    for name in missing:
        index = _find_line(lines, name)
        if index is None:
            absent += 1
            continue
        line = lines[index]
        tag = explicit or choose_tag(name, line, path, arch)
        found = _TAGGED.match(line)
        if found:
            if tag.split("=")[0] in found.group("tags"):
                already += 1
                continue
            new = f"{found.group('indent')}({found.group('tags')}|{tag}){found.group('rest')}"
        else:
            stripped = line.lstrip()
            new = f"{line[:len(line) - len(stripped)]}({tag}){stripped}"
        if text.count(line) != 1:
            absent += 1
            continue
        edits.append(Edit(path, line, new))
        tags[tag] = tags.get(tag, 0) + 1
    if not edits and not already:
        raise Refusal(f"none of the {len(missing)} missing symbols is a line of {path}.")
    match.params["_report"] = (len(edits), len(missing), tags, already, absent)
    return edits


def summary(edits, match: Match, params: dict) -> str:
    made, total, tags, already, absent = match.params["_report"]
    used = ", ".join(f"({tag}) x{count}" for tag, count in tags.items())
    return (f"tagged {made} of the {total} symbols the log reports missing with {used or 'no tag'}"
            + (f"; {already} were tagged already" if already else "")
            + (f"; {absent} are not in the file" if absent else ""))


register(Recipe(
    ID, "verified",
    "Tags each missing symbol line glued to the name: (optional=templinst) for templates/std/Qt, "
    "(optional) in a *.symbols.<arch> file, (arch=!<arch>) in a generic file. new_text: auto, "
    "or a tag to force.",
    recognise, expand, summary))
