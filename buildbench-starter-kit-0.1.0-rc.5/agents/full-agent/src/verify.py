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

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from .arch_model import Architecture
from .predict import Tri, cpp_active_lines, define_locations, make_active_lines
from . import patterns

_MISSING_HEADER = re.compile(r"fatal error: (?P<header>[\w./+-]+): No such file or directory")
_RUST_SIGNED = re.compile(r"expected `\*(?:const|mut) (?P<want>[iu]8)`, found `\*(?:const|mut) (?P<have>[iu]8)`")
_RUST_CSTRING = re.compile(r"\*(?:const|mut)\s+(?P<type>[iu]8)\b")
_OUTLINE = re.compile(r"__aarch64_\w+")
_SYMBOL_LINE = re.compile(r"^\s*(?:\(.*?\))?\s*\"?(?P<name>[^\s\"@]+)")
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
            what = f"the include of {header.group('header')}" if header else "the failing line"
            return Verdict(True, f"{path}: {what} is no longer in the file.")
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
    return Verdict(False, f"{path}: {what} is unchanged and still compiled on {target.dpkg}; the same error "
                          "will recur unless its cause was elsewhere.")


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
    if "__aarch64_" in log_text and target.outline_atomics:
        flags = state.flags.get("DEB_CFLAGS_MAINT_APPEND") or ""
        if "-mno-outline-atomics" in flags:
            out.append(Verdict(True, "debian/rules: -mno-outline-atomics reaches CFLAGS on arm64; the "
                                     "__aarch64_* helper references will no longer be emitted."))
    return out


def _symbols(worktree: Path, log_lines: list[str], target: Architecture, changed: set[str]) -> list[Verdict]:
    edited = [p for p in changed if ".symbols" in PurePosixPath(p).name]
    if not edited:
        return []
    missing = patterns.missing_symbols(log_lines)
    if not missing:
        return []
    out = []
    for path in edited:
        text = _read(worktree, path) or ""
        misplaced = [l for l in text.split("\n") if re.search(r"@\S+\s+\S*\((?:optional|arch=)[^)]*\)", l)]
        if misplaced:
            out.append(Verdict(False, f"{path}: {len(misplaced)} lines carry a tag after the symbol name, for example "
                                      f"{misplaced[0].strip()[:90]!r}; dpkg-gensymbols reads that as the version "
                                      "(\"(optional) is not a valid version\"). Tags go in parentheses before the name."))
        still = []
        for name in missing:
            for line in text.split("\n"):
                if name in line and "(arch=" not in line and "(optional" not in line and "(arch-bits" not in line:
                    still.append(name)
                    break
        if still:
            out.append(Verdict(False, f"{path}: {len(still)} of the {len(missing)} symbols the log reports missing "
                                      f"are still listed without an (arch=...) or (optional) tag, for example "
                                      f"{still[0][:80]}; dpkg-gensymbols will fail on them again."))
        else:
            out.append(Verdict(True, f"{path}: every symbol the log reports missing is now tagged or removed."))
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
    out.extend(_symbols(worktree, list(log_lines), target, changed))
    out.extend(_cmake(worktree, list(log_lines), changed))
    out.extend(_sphinx(worktree, list(log_lines), changed))
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
