"""Predict, without a build, what the edited files do on the target.

After a batch is applied in a run without build feedback, each initial
diagnostic is re-examined in the edited worktree with the exact evaluators
in predict.py: is the failing include still reached, is the failing line
still compiled or changed, is the flag exported on the path make takes for
the target, are the missing symbols now tagged, does any C-string pointer
still carry the other architecture's type. The verdicts are the feedback the
hosted run otherwise lacks. They are predictions about constructs, not a
build: a verdict says so, and says "unknown" where the evaluators cannot tell.

Standard library only, read-only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from .arch_model import Architecture
from .predict import Tri, cpp_active_lines, define_locations, make_active_lines
from . import patterns, rules_guard, syntax_guard

_MISSING_HEADER = re.compile(r"fatal error: (?P<header>[\w./+-]+): No such file or directory")
_RUST_SIGNED = re.compile(r"expected `\*(?:const|mut) (?P<want>[iu]8)`, found `\*(?:const|mut) (?P<have>[iu]8)`")
_RUST_CSTRING = re.compile(r"\*(?:const|mut)\s+(?P<type>[iu]8)\b")
_OUTLINE = re.compile(r"__aarch64_\w+")
_TAG_PREFIX = re.compile(r"^\s*(?:\([^()]*\))?")
_PATCH_DIR = ("input/debian/patches/",)


@dataclass
class Verdict:
    ok: Tri                 # True: the construct no longer produces the error; False: it still does
    text: str


def _yes(ok: Tri) -> str:
    return {True: "fixed", False: "NOT fixed", None: "unknown"}[ok]


def _read(worktree: Path, relative: str) -> str | None:
    try:
        return (Path(worktree) / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _c_diagnostic(worktree: Path, item, target: Architecture, originals: dict[str, str]) -> Verdict | None:
    if len(item.resolved) != 1 or not item.line:
        return None
    path = item.resolved[0]
    if PurePosixPath(path).suffix not in (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".m", ".mm"):
        return None
    text = _read(worktree, path)
    if text is None:
        return None
    lines = text.split("\n")
    original = originals.get(path, "").split("\n")
    quoted = (item.source_line or "").strip()
    # Where did the failing line go? Same number if its text still matches, else search.
    number = item.line
    if not (1 <= number <= len(lines) and (not quoted or lines[number - 1].strip() == quoted)):
        candidates = [i + 1 for i, l in enumerate(lines) if quoted and l.strip() == quoted]
        number = candidates[0] if len(candidates) == 1 else None
    wanted = {m for m in re.findall(r"[A-Za-z_]\w*", text) if m.startswith(("HAVE_", "USE_", "ENABLE_", "WITH_", "CONFIG_"))}
    located = define_locations(worktree, wanted, target) if wanted else {}
    defines = {name: value for name, (value, _) in located.items()}
    header = _MISSING_HEADER.search(item.message)
    if number is None:
        if quoted and not any(l.strip() == quoted for l in lines):
            # A line that merely disappears proves nothing: it may have been moved or
            # replaced by code with the same fault, and what it declared may still be used.
            what = f"the include of {header.group('header')}" if header else "the failing line"
            return Verdict(None, f"{path}: {what} is no longer in the file, but removing a line does not "
                                 "show the error is gone (what it provided may still be used, or it may "
                                 "have moved); this check cannot tell.")
        return Verdict(None, f"{path}: the failing line could not be located after the edit.")
    reached = cpp_active_lines(text, target, defines).active[number - 1]
    what = f"the include of {header.group('header')} at line {number}" if header else f"line {number} ({lines[number-1].strip()[:80]})"
    changed = (number - 1 < len(original) and original[number - 1] != lines[number - 1]) or quoted != lines[number - 1].strip()
    if reached is False:
        return Verdict(True, f"{path}: {what} is no longer compiled on {target.dpkg} "
                             "(a preprocessor conditional above it is now false there).")
    if reached is None:
        return Verdict(None, f"{path}: whether {what} is compiled on {target.dpkg} depends on a macro this "
                             "check cannot evaluate; the guard above it is not an architecture test.")
    if header:
        guards = [m for i, l in enumerate(lines[:number - 1]) for m in re.findall(r"[A-Za-z_]\w*", l)
                  if l.lstrip().startswith("#") and m in located]
        where = "".join(f" {m} is defined at {located[m][1]} (value {located[m][0]} on {target.dpkg})."
                        for m in dict.fromkeys(guards[-3:]))
        return Verdict(False, f"{path}: {what} is still compiled on {target.dpkg}, so the header will still be "
                              "missing. Guard it with an architecture conditional or turn off the macro that "
                              f"enables it.{where}")
    if changed:
        return Verdict(None, f"{path}: {what} changed and is compiled on {target.dpkg}; the error may or may not "
                             "recur (no compiler is available to check).")
    return Verdict(None, f"{path}: {what} is unchanged and still compiled on {target.dpkg}. Whether the error "
                         "recurs depends on code this check cannot evaluate (no compiler is available), so "
                         "this is not a verdict.")


def _rust(worktree: Path, diagnostics, target: Architecture, changed: set[str]) -> Verdict | None:
    text = "\n".join(f"{d.message}\n{getattr(d, 'compiler_details', '')}" for d in diagnostics)
    if not _RUST_SIGNED.search(text):
        return None
    wrong = "u8" if target.c_char == "i8" else "i8"
    from .tools import collect_files
    root = Path(worktree) / "input"
    remaining: dict[str, int] = {}
    try:
        files, _ = collect_files(root)
    except OSError:
        return None
    for path in files:
        if path.suffix != ".rs" or "/.pc/" in path.as_posix():
            continue
        source = _read(worktree, path.relative_to(worktree).as_posix())
        if source is None:
            continue
        n = sum(1 for m in _RUST_CSTRING.finditer(source) if m.group("type") == wrong)
        if n:
            remaining[path.relative_to(worktree).as_posix()] = n
    if not remaining:
        return Verdict(True, f"Rust: no `*const {wrong}` or `*mut {wrong}` C-string pointer types remain in the "
                             f"crate; every FFI signature now agrees with C char on {target.dpkg}.")
    listed = ", ".join(f"{p} ({n})" for p, n in sorted(remaining.items(), key=lambda kv: -kv[1])[:8])
    return Verdict(False, f"Rust: {sum(remaining.values())} `{wrong}` C-string pointer type(s) remain: {listed}. "
                          "Each will keep producing the mismatched-types error; replace them with "
                          "std::ffi::c_char too.")


def _rules(worktree: Path, log_text: str, target: Architecture, changed: set[str], before: str | None) -> list[Verdict]:
    path = "input/debian/rules"
    if path not in changed:
        return []
    text = _read(worktree, path)
    if text is None:
        return []
    state = make_active_lines(text, target)
    out = []
    old_flags = make_active_lines(before, target).flags if before is not None else {}
    for name, value in state.flags.items():
        if old_flags.get(name) != value:
            out.append(Verdict(True, f"debian/rules: {name} is exported with value {value!r} on {target.dpkg} "
                                     "(it reaches dpkg-buildflags and every compile)."))
    # Flag assignments that do not reach the build on the target
    for number, line in enumerate(text.split("\n"), start=1):
        match = re.match(r"^\s*(?:export\s+)?(DEB_\w+_MAINT_\w+|DEB_BUILD_MAINT_OPTIONS)\s*[:+?]?=\s*(.*)$", line)
        if not match or line.startswith("\t"):
            continue
        name = match.group(1)
        if state.active[number - 1] is False:
            out.append(Verdict(False, f"debian/rules:{number}: {name} is inside a conditional that is false on "
                                      f"{target.dpkg}, so it has no effect there."))
        elif name not in state.exported and state.active[number - 1] is True:
            out.append(Verdict(False, f"debian/rules:{number}: {name} is assigned but never exported, so "
                                      "dpkg-buildflags never sees it."))
    for number, message in rules_guard.buildflags_problems(text):
        if not (state.active[number - 1] is False if number - 1 < len(state.active) else False):
            out.append(Verdict(False, f"debian/rules:{number}: {message}."))
    if "__aarch64_" in log_text and target.outline_atomics:
        flags = state.flags.get("DEB_CFLAGS_MAINT_APPEND") or ""
        if "-mno-outline-atomics" in flags:
            out.append(Verdict(True, "debian/rules: -mno-outline-atomics reaches CFLAGS on arm64; the "
                                     "__aarch64_* helper references will no longer be emitted."))
    return out


def _symbol_key(line: str) -> str | None:
    """The 'name@Base' a symbols line is about, tags, quotes and version removed."""
    body = _TAG_PREFIX.sub("", line.rstrip("\r")).strip()
    if not body or body.startswith("#"):
        return None
    m = re.match(r'("(?:[^"\\]|\\.)*"(?:@\S*)?|\S+)', body)
    return m.group(1).replace('"', "") if m else None


def _tagged(line: str) -> bool:
    return bool(re.match(r"^\s*\((?:[^()]*\|)?(?:optional|arch=|arch-bits|arch-endian|ignore-blacklist)", line))


MAX_LISTED = 12   # remaining items a verdict names, each in full


def _named(items) -> str:
    """Up to MAX_LISTED items, each whole (a truncated symbol name cannot be searched for)."""
    items = list(items)
    text = "; ".join(items[:MAX_LISTED])
    return text + (f"; and {len(items) - MAX_LISTED} more" if len(items) > MAX_LISTED else "")


def _overtagging(original: str, text: str, missing: list[str]) -> str | None:
    """Why the lines that changed are not exactly 'a tag inserted on each reported symbol'."""
    old, new = original.split("\n"), text.split("\n")
    wanted = {m.replace('"', "") for m in missing}
    gone = Counter(l for l in old if l.strip()) - Counter(l for l in new if l.strip())
    came = Counter(l for l in new if l.strip()) - Counter(l for l in old if l.strip())
    gone_keys = Counter(_symbol_key(l) for l in gone.elements())
    came_keys = Counter(_symbol_key(l) for l in came.elements())
    if not gone and not came:
        return None
    extra = sorted(k for k in came_keys if k and k not in wanted)
    other = sorted(k for k in gone_keys if k and k not in wanted)
    not_tag = [k for k in came_keys if gone_keys.get(k) != came_keys[k]] + \
              [k for k in gone_keys if k not in came_keys]
    if extra or other:
        names = list(dict.fromkeys(extra + other))
        return (f"{len(names)} changed symbol lines are "
                f"not in the log's list of {len(wanted)} missing symbols: {_named(names)}. "
                "Tag only the reported symbols and leave every other line as it was")
    if not_tag:
        names = sorted(set(not_tag))
        return (f"{len(names)} reported symbols were removed or retyped instead of getting a tag "
                f"inserted: {_named(names)}; change them only by inserting a "
                "(tag) before the name")
    return None


def _symbols(worktree: Path, log_lines: list[str], target: Architecture, changed: set[str],
             originals: dict[str, str] | None = None) -> list[Verdict]:
    edited = [p for p in changed if ".symbols" in PurePosixPath(p).name]
    if not edited:
        return []
    missing = patterns.missing_symbols(log_lines)
    if not missing:
        return []
    originals = originals or {}
    out = []
    for path in edited:
        text = _read(worktree, path) or ""
        bad = syntax_guard.symbols_lint(text)
        if bad:
            numbers = ", ".join(str(n) for n, _, _ in bad[:MAX_LISTED]) + (" ..." if len(bad) > MAX_LISTED else "")
            fix = next((f for _, _, f in bad if f), None)
            out.append(Verdict(False, f"{path}: file has {len(bad)} unparseable lines: {numbers}; "
                                      "dpkg-gensymbols drops them, so they tag nothing"
                                      + (f" (for example write {fix!r})" if fix else "") + "."))
        lines = text.split("\n")
        misplaced = [(n, l) for n, l in enumerate(lines, 1)
                     if re.search(r"@\S+\s+(?:\S+\s+)?\((?:optional|arch=)[^)]*\)", l)]
        if misplaced:
            out.append(Verdict(False, f"{path}: {len(misplaced)} lines carry a tag after the symbol name: "
                                      + _named(f"line {n}: {l.strip()}" for n, l in misplaced)
                                      + "; dpkg-gensymbols reads that as the version "
                                      "(\"(optional) is not a valid version\"). Tags go in parentheses before the name."))
        still = []   # (line number, the line) of each reported symbol that is still untagged
        for name in missing:
            for number, line in enumerate(lines, 1):
                if name in line and "(arch=" not in line and "(optional" not in line and "(arch-bits" not in line:
                    still.append((number, line.strip(), name))
                    break
        if still:
            out.append(Verdict(False, f"{path}: {len(still)} of the {len(missing)} symbols the log reports missing "
                                      f"are still listed without an (arch=...) or (optional) tag: "
                                      + _named(f"line {n}: {l}" for n, l, _ in still)
                                      + "; dpkg-gensymbols will fail on them again."))
            continue
        original = originals.get(path)
        if original is None:
            if not bad and not misplaced:
                out.append(Verdict(True, f"{path}: every reported-missing symbol is tagged ({len(missing)}); the "
                                         "original file was not available, so whether anything else changed "
                                         "was not checked."))
            continue
        over = _overtagging(original, text, missing)
        if over:
            out.append(Verdict(False, f"{path}: over-tagging: {over}."))
        elif not bad and not misplaced:
            out.append(Verdict(True, f"{path}: every reported-missing symbol tagged ({len(missing)}); "
                                     "nothing else changed."))
    return out


_CMAKE_SUFFIXES = ("CMakeLists.txt", ".cmake")
_SET_PROPERTY = re.compile(r"set_target_properties\s*\(\s*(?P<target>[\w${}.-]+)[^)]*?INTERFACE_INCLUDE_DIRECTORIES\s+(?P<value>\"[^\"]*\"|\S+)", re.S)
_PROPERTY_LOOP = re.compile(r"foreach\s*\(\s*(?P<var>\w+)\s+(?P<values>[^)]*)\)", re.I)


def _cmake(worktree: Path, log_lines: list[str], changed: set[str]) -> list[Verdict]:
    """Imported targets with a non-existent include directory: is every target of
    the package covered by a set_target_properties after find_package, and is the
    directory replaced rather than cleared?"""
    edited = [p for p in changed if p.endswith(_CMAKE_SUFFIXES)]
    if not edited:
        return []
    facts = patterns.imported_targets(log_lines, worktree)
    if not facts:
        return []
    targets = facts["named"] + list(facts["siblings"])
    find_call = facts["find_package"]
    covered: dict[str, tuple[str, bool]] = {}   # target -> (path, cleared)
    early: dict[str, str] = {}                  # target -> "path:line" of a repair that runs too soon
    for path in edited:
        text = _read(worktree, path)
        if text is None:
            continue
        loops = {}
        for m in _PROPERTY_LOOP.finditer(text):
            loops[m.group("var")] = [v for v in m.group("values").split() if "${" not in v]
        for m in _SET_PROPERTY.finditer(text):
            value = m.group("value")
            cleared = value in ('""', "''")
            names = [m.group("target")]
            var = re.fullmatch(r"\$\{(\w+)\}", m.group("target"))
            if var:
                names = loops.get(var.group(1), [])
            line = text.count("\n", 0, m.start()) + 1
            if find_call and path == find_call[0] and line < find_call[1]:
                for name in names:  # runs before the targets are imported: TARGET is false then
                    early.setdefault(name, f"{path}:{line}")
                continue
            for name in names:
                covered.setdefault(name, (path, cleared))
    out = []
    for target in targets:
        hit = covered.get(target)
        linked = facts["siblings"].get(target)
        if hit is None:
            if target in early:
                out.append(Verdict(False, f"CMake: `{target}` is repaired at {early[target]}, before "
                                          f"{find_call[0]}:{find_call[1]} imports it, so `if(TARGET ...)` is false "
                                          "there and nothing changes. Move the repair below the find_package call."))
            elif target in facts["named"]:
                out.append(Verdict(False, f"CMake: imported target `{target}` still carries the non-existent include "
                                          "directory; no edit sets its INTERFACE_INCLUDE_DIRECTORIES after it is imported."))
            else:
                out.append(Verdict(False, f"CMake: imported target `{target}` comes from the same package and is linked "
                                          f"at {linked}; CMake will report the same non-existent path for it next. "
                                          "Add it to the repaired targets."))
        elif hit[1]:
            out.append(Verdict(None, f"CMake: `{target}`'s INTERFACE_INCLUDE_DIRECTORIES is cleared in {hit[0]}, so "
                                     "configure passes it, but the Fortran modules (or headers) in that directory are "
                                     "then not on the compile line; replace the missing directory instead of clearing it."))
        else:
            out.append(Verdict(True, f"CMake: `{target}`'s INTERFACE_INCLUDE_DIRECTORIES is rewritten in {hit[0]} after "
                                     "the package is imported."))
    return out


def _sphinx(worktree: Path, log_lines: list[str], changed: set[str]) -> list[Verdict]:
    """setup.py build_sphinx removed: is the step replaced by a Sphinx call that
    still writes the directory the -doc package's install file expects?"""
    if "input/debian/rules" not in changed or not any(patterns._BUILD_SPHINX.search(l) for l in log_lines):
        return []
    rules = _read(worktree, "input/debian/rules") or ""
    source, build = patterns._setup_cfg_sphinx(worktree)
    html = build.rstrip("/") + "/html"
    expects = patterns._install_expectations(worktree, build)
    out = []
    if "build_sphinx" in rules:
        for number, line in enumerate(rules.splitlines(), start=1):
            if "build_sphinx" in line and not line.lstrip().startswith("#"):
                out.append(Verdict(False, f"debian/rules:{number}: still runs `setup.py build_sphinx`, a command "
                                          "Sphinx 7 removed; it fails the same way."))
                break
    produces = re.search(r"^\t.*(?:sphinx-build|-m sphinx)\b.*\b" + re.escape(html.rstrip("/")) + r"\b", rules, re.M)
    if produces:
        out.append(Verdict(True, f"debian/rules: Sphinx is invoked directly and writes `{html}`"
                                 + (", which " + expects[0].split(": ", 1)[0] + " installs." if expects else ".")))
    elif expects:
        out.append(Verdict(False, f"{expects[0].split(': ', 1)[0]} installs `{build}` but no line in debian/rules "
                                  f"now writes it (the build_sphinx step was removed or its target renamed): "
                                  "dh_install will fail with \"missing files\". Keep the override target and run "
                                  f"`python3 -m sphinx -b html {source} {html}` in it."))
    return out


_BYPASS_SYMBOLIC = re.compile(r"-Wl,-Bsymbolic(?![-\w])")
_CONDITIONAL_LIBS = re.compile(r"^\s*(?:libs|libraries)\s*[+]?=|\blibs\.(?:append|extend)\(|libraries\s*=", re.M)


def _links_library(text: str, name: str) -> list[str]:
    """Lines (stripped) of a setup.py that still link -l<name> unconditionally."""
    flag = re.compile(r"""["']-l""" + re.escape(name) + r"""["']""")
    quoted = re.compile(r"""["']""" + re.escape(name) + r"""["']""")
    found = []
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            continue
        if flag.search(line):
            found.append(line.strip())
            continue
        if not quoted.search(line) or not _CONDITIONAL_LIBS.search(line):
            continue
        head, sep, tail = line.partition(" else ")
        if sep and " if " in head and not quoted.search(head) and quoted.search(tail):
            continue   # `libs = [...] if _extra_objs else ["X", ...]`: X only when nothing was unpacked
        found.append(line.strip())
    return found


def _static_shim(worktree: Path, log_lines: list[str], changed: set[str]) -> list[Verdict]:
    """A non-PIC static archive pulled into a Python extension (pybigwig class):
    the one repair that passed the validator needs four pieces, and a setup.py
    that has some of them still fails the link (replays/pybigwig-verified-static-shim.json)."""
    edited = [p for p in sorted(changed) if PurePosixPath(p).name == "setup.py"]
    if not edited:
        return []
    hits = [m for line in log_lines for m in [patterns._NONPIC.search(line)] if m]
    if not hits:
        return []
    name = hits[0].group("name")
    symbols = list(dict.fromkeys(re.sub(r"@.*$", "", m.group("symbol")) for m in hits))
    out = []
    for path in edited:
        text = _read(worktree, path)
        if text is None:
            continue
        missing = []
        still = _links_library(text, name)
        if still:
            missing.append(f"(a) setup.py still links lib{name} unconditionally ({still[0]}); the library has "
                           f"to be dropped from the link when its objects are linked directly")
        if not (re.search(r"extra_objects", text) or re.search(r"""["']ar["']\s*,\s*["']x["']|\bar x\b""", text)):
            missing.append(f"(b) no step links the archive's objects (unpack lib{name}.a with `ar x` and pass the "
                           "objects as extra_objects)")
        unshimmed = [sym for sym in symbols
                     if not re.search(r"visibility\W{0,6}hidden\W{0,12}[^;]{0,40}\*\s*" + re.escape(sym) + r"\b", text)]
        if unshimmed:
            missing.append("(c) no hidden shim defines " + ", ".join(unshimmed)
                           + " (a `FILE *" + unshimmed[0] + "` with __attribute__((visibility(\"hidden\"))) "
                           "in a small C file added to sources), so the PC32 relocations against it stay illegal "
                           "in a shared object")
        if not _BYPASS_SYMBOLIC.search(text):
            missing.append("(d) -Wl,-Bsymbolic is not passed (extra_link_args), so the references are not bound "
                           "inside the extension")
        if not missing:
            out.append(Verdict(True, f"{path}: lib{name}.a is unpacked and its objects linked, lib{name} is no "
                                     f"longer linked, {'/'.join(symbols)} is defined by a hidden shim and "
                                     "-Wl,-Bsymbolic is passed: the four pieces of the verified static-archive repair."))
            continue
        from .patterns import invocation_paragraph
        out.append(Verdict(False, f"{path}: the non-PIC archive lib{name}.a will still fail to link into the "
                                  "extension; the verified repair (recipe static-archive-shim) needs four pieces "
                                  "and this edit lacks " + "; ".join(missing) + ". "
                                  + invocation_paragraph("static-archive-shim", path,
                                                         f"lib{name}.so is missing so the non-PIC archive is "
                                                         "linked into the extension")
                                  + " (The recipe refuses a setup.py that already sets extra_objects: restore "
                                    "the original line first, or add the missing pieces to your own edit.)"))
    return out


def predict(worktree: Path, diagnostics, target: Architecture | None, *, changed: set[str],
            originals: dict[str, str] | None = None, log_lines: list[str] = ()) -> list[Verdict]:
    """Verdicts for the initial diagnostics against the edited worktree."""
    if target is None:
        return []
    originals = originals or {}
    log_text = "\n".join(log_lines)
    out: list[Verdict] = []
    for path in changed:
        if path.startswith(_PATCH_DIR):
            out.append(Verdict(False, f"{path}: a file under debian/patches was edited by hand; dpkg-source will "
                                      "refuse the source package. Revert it and edit the patched source instead."))
    seen = set()
    for item in list(diagnostics)[:12]:
        verdict = _c_diagnostic(worktree, item, target, originals)
        if verdict and verdict.text not in seen:
            seen.add(verdict.text)
            out.append(verdict)
    rust = _rust(worktree, diagnostics, target, changed)
    if rust:
        out.append(rust)
    out.extend(_rules(worktree, log_text, target, changed, originals.get("input/debian/rules")))
    out.extend(_symbols(worktree, list(log_lines), target, changed, originals))
    out.extend(_cmake(worktree, list(log_lines), changed))
    out.extend(_sphinx(worktree, list(log_lines), changed))
    out.extend(_static_shim(worktree, list(log_lines), changed))
    return out


def render(verdicts: list[Verdict], target: Architecture) -> str:
    if not verdicts:
        return ""
    lines = [f"=== Static prediction for {target.dpkg} after this attempt (the edited files were evaluated "
             "under the target's architecture facts; no build was run) ==="]
    for v in verdicts:
        lines.append(f"- [{_yes(v.ok)}] {v.text}")
    if all(v.ok is True for v in verdicts):
        lines.append("Every construct the errors came from is predicted fixed. If no other cause remains, reply stop.")
    elif any(v.ok is False for v in verdicts):
        lines.append("At least one construct still produces its error on the target: address it in the next edit.")
    lines.append("=== End of prediction ===")
    return "\n".join(lines)
