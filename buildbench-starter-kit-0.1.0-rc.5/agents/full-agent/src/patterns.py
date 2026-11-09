"""Recognise a known cross-architecture failure in the evidence and say what
the repair is, next to the errors rather than in the playbook.

The live runs of 3 November (docs/no-build-live-findings.md) showed the model
reading "expected *const u8, found *const i8" and casting every call site to
*const i8, the opposite of the portable fix the playbook describes, twice in a
row; the playbook is a page of system prompt, the errors are what it reads.
Each recogniser here matches one signature in the diagnostics or the log,
gathers the facts from the worktree that the fix needs (which files, which
macro, which lines of the symbols diff), and renders one block for the
opening message. A block is advice with evidence; it never edits anything.

Standard library only; every scan is bounded.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath
import re

from .tools import collect_files
from . import archnames

MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES_LISTED = 12
MAX_DIFF_LINES = 40
_SKIP = ("/.pc/", "/.git/", "/debian/.debhelper/", "/target/", "/obj-")

TITLE = "=== Recognised cross-architecture pattern (evidence, with the usual repair) ==="
END = "=== End of pattern ==="


def _text(diagnostics: Sequence) -> str:
    return "\n".join(f"{d.message}\n{getattr(d, 'compiler_details', '')}" for d in diagnostics)


def _log_lines(log_path: Path | None) -> list[str]:
    if log_path is None:
        return []
    try:
        with open(log_path, "rb") as handle:
            data = handle.read(MAX_LOG_BYTES)
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()


def _source_files(worktree: Path, suffixes: tuple[str, ...]) -> list[tuple[str, str]]:
    root = Path(worktree) / "input"
    if not root.is_dir():
        return []
    try:
        files, _ = collect_files(root)
    except OSError:
        return []
    found = []
    for path in files:
        relative = path.relative_to(worktree).as_posix()
        if any(part in "/" + relative + "/" for part in _SKIP):
            continue
        if PurePosixPath(relative).suffix not in suffixes:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            found.append((relative, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            continue
    return found


# --- Rust: C strings typed as *const i8 / *const u8 ------------------------

_RUST_SIGNED = re.compile(r"expected `\*(?:const|mut) (?P<want>[iu]8)`, found `\*(?:const|mut) (?P<have>[iu]8)`")
_I8_POINTER = re.compile(r"\*(?:const|mut) i8\b|<i8>|\bas i8\b")
_U8_POINTER = re.compile(r"\*(?:const|mut) u8\b")


def rust_char_signedness(diagnostics: Sequence, worktree: Path, target_arch: str) -> str:
    """The crate hard-codes i8 (or u8) for C char, and the target disagrees."""
    pairs = _RUST_SIGNED.findall(_text(diagnostics))
    if not pairs:
        return ""
    # Which side is "expected" depends on whether the mismatch is an argument
    # or a return value (rust-subversion has both), so the target decides
    # which type the crate hard-coded; without a known target, the majority.
    arch = (target_arch or "").lower()
    if arch in ("arm64", "aarch64", "armhf", "armel", "ppc64el", "powerpc", "s390x", "riscv64"):
        right = "u8"
    elif arch in ("amd64", "i386", "x86_64", "x86", "mips", "mipsel"):
        right = "i8"
    else:
        found = [h for _, h in pairs]
        right = "u8" if found.count("i8") >= found.count("u8") else "i8"
    wrong = "i8" if right == "u8" else "u8"  # the type the crate wrote
    pattern = _I8_POINTER if wrong == "i8" else _U8_POINTER
    counts = []
    for relative, text in _source_files(worktree, (".rs",)):
        n = sum(1 for line in text.splitlines() if pattern.search(line))
        if n:
            counts.append((relative, n))
    lines = [
        TITLE,
        f"Rust C-string pointer signedness: the compiler reports `*const {right}` expected "
        f"where the crate wrote `*const {wrong}` ({len(pairs)} such errors). C `char` is "
        f"{'unsigned (u8)' if right == 'u8' else 'signed (i8)'} on the target "
        f"({target_arch or 'this architecture'}) and {wrong} on the architecture the crate "
        "was written for; bindgen's generated signatures already use the target's type.",
        "Repair: replace the hard-coded `" + wrong + "` C-string pointer types with "
        "`std::ffi::c_char` (`*const i8` -> `*const std::ffi::c_char`, `*mut i8` -> "
        "`*mut std::ffi::c_char`, and the same for `<i8>` generic parameters and `as *const "
        "i8` casts), in every file of the crate, with replace_all: true, one edit per file "
        "and pattern. Do NOT cast call sites to `*const " + right + "` or `*const " + wrong
        + "`: that fixes nothing and breaks the other architecture, and the errors then "
        "move to the call sites (this happened on 3 November, twice).",
    ]
    if counts:
        shown = sorted(counts, key=lambda item: -item[1])[:MAX_FILES_LISTED]
        lines.append(f"Files under input/ with `{wrong}` pointer types (line counts): "
                     + ", ".join(f"{path} ({n})" for path, n in shown)
                     + (f", and {len(counts) - len(shown)} more" if len(counts) > len(shown) else "")
                     + ".")
    lines.append(END)
    return "\n".join(lines)


# --- x86 intrinsic header on another architecture ----------------------------

_X86_HEADERS = ("xmmintrin", "emmintrin", "pmmintrin", "tmmintrin", "smmintrin", "nmmintrin",
                "wmmintrin", "immintrin", "x86intrin", "ia32intrin", "cpuid", "mm_malloc",
                "mmintrin", "ammintrin", "avxintrin", "avx2intrin")
_MISSING_HEADER = re.compile(r"fatal error: (?P<header>[\w/]+)\.h: No such file or directory")
_IF_LINE = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b(?P<cond>.*)$")
_MACRO = re.compile(r"\b(?P<name>HAVE_\w+|USE_\w+|ENABLE_\w+|WITH_\w+)\b")
_DEFINE = re.compile(r"^\s*#\s*define\s+(?P<name>\w+)\b(?P<value>.*)$")


def x86_intrinsic_header(diagnostics: Sequence, worktree: Path, target_arch: str) -> str:
    """#include of an x86-only intrinsics header fails on a non-x86 target."""
    for item in diagnostics:
        match = _MISSING_HEADER.search(item.message)
        if not match or match.group("header") not in _X86_HEADERS:
            continue
        header = match.group("header") + ".h"
        lines = [
            TITLE,
            f"x86-only intrinsics header on {target_arch or 'a non-x86 target'}: {header} "
            "exists only for x86 compilers, so the include (and the code that uses the "
            "intrinsics) must not be compiled on this architecture.",
        ]
        guard = macro = None
        if len(item.resolved) == 1 and item.line:
            try:
                text = (Path(worktree) / item.resolved[0]).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                text = ""
            source = text.splitlines()
            # The nearest enclosing conditional that tests a feature macro
            # (rcran: "#if HAVE_MM_PREFETCH" two levels above the include,
            # behind an "#if defined(_MSC_VER) ... #else"); failing that, the
            # nearest conditional of any kind.
            for number in range(min(item.line, len(source)) - 1, max(-1, item.line - 40), -1):
                found = _IF_LINE.match(source[number])
                if not found:
                    continue
                named = _MACRO.search(found.group("cond"))
                if named:
                    guard = (number + 1, source[number].strip())
                    macro = named.group("name")
                    break
                if guard is None:
                    guard = (number + 1, source[number].strip())
        if guard:
            lines.append(f"The include at {item.resolved[0]}:{item.line} is already inside "
                         f"a conditional, line {guard[0]}: {guard[1]}")
        if macro:
            definitions = []
            for relative, text in _source_files(worktree, (".h", ".hpp", ".hh", ".in", ".cmake",
                                                           ".ac", ".am", ".txt", ".mk", ".pro")):
                for number, line in enumerate(text.splitlines(), start=1):
                    define = _DEFINE.match(line)
                    if define and define.group("name") == macro:
                        definitions.append(f"{relative}:{number}: {line.strip()}")
                    if len(definitions) >= 6:
                        break
            if definitions:
                lines.append(f"{macro} is defined here: " + "; ".join(definitions) + ".")
                lines.append("Repair: make that definition false on this architecture (set it "
                             "to 0, or define it only under `#if defined(__x86_64__) || "
                             "defined(__i386__)`), so the existing conditional skips the x86 "
                             "code; the portable fallback the conditional already guards then "
                             "compiles. This is the smallest change and was the successful "
                             "repair of r-cran-digest.")
            else:
                lines.append(f"Repair: make {macro} false on this architecture where it is "
                             "set (a configuration header, configure check or CMake option), "
                             "so the existing conditional skips the x86 code.")
        else:
            lines.append("Repair: guard the include and the code using the intrinsics with "
                         "`#if defined(__x86_64__) || defined(__i386__)` and keep (or add) "
                         "the portable fallback for other architectures.")
        lines.append(END)
        return "\n".join(lines)
    return ""


# --- ARM outline atomics helpers from libgcc ---------------------------------

_OUTLINE = re.compile(r"hidden symbol `(?P<symbol>__aarch64_\w+)' in (?P<archive>\S+) is referenced by DSO"
                      r"|undefined reference to `(?P<undefined>__aarch64_\w+)'")
_DSO_OBJECT = re.compile(r"^/usr/bin/ld: (?P<object>\S+): hidden symbol")


def arm_outline_atomics(log_lines: Sequence[str], worktree: Path, target_arch: str) -> str:
    symbols, objects = [], []
    for line in log_lines:
        match = _OUTLINE.search(line)
        if not match:
            continue
        symbols.append(match.group("symbol") or match.group("undefined"))
        obj = _DSO_OBJECT.match(line)
        if obj:
            objects.append(obj.group("object"))
    if not symbols:
        return ""
    names = list(dict.fromkeys(symbols))[:6]
    lines = [
        TITLE,
        "ARM outline-atomics helper from libgcc: " + ", ".join(f"`{s}`" for s in names)
        + " is an outline-atomics helper that GCC emits for atomic operations on aarch64. "
        "It lives in libgcc (not libatomic) with hidden visibility, so a shared library "
        "linked without libgcc (for example with -nostdlib, or a libtool library with "
        "-Wl,--no-undefined) cannot export it, and every program that links that library "
        "fails as shown."
        + (" Failing links: " + ", ".join(dict.fromkeys(objects[:6])) + "." if objects else ""),
        "Repair: compile the package with -mno-outline-atomics on arm64, which makes the "
        "compiler inline the atomics instead. In debian/rules, at make level (not inside a "
        "recipe), add:",
        "ifeq ($(DEB_HOST_ARCH),arm64)",
        "export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics",
        "export DEB_CXXFLAGS_MAINT_APPEND += -mno-outline-atomics",
        "endif",
        "\"export VAR += value\" on one line is valid GNU make and is the form dpkg-buildflags "
        "needs; do not split it, and do not add -latomic or -lgcc_s to LDFLAGS for this "
        "symbol. This was the successful repair of libunwind.",
        END,
    ]
    return "\n".join(lines)


# --- Debian symbols files ----------------------------------------------------

_SYMBOLS_ERROR = re.compile(r"dpkg-gensymbols: (?:error|warning): some (?:new )?symbols")
_SYMBOLS_FILE = re.compile(r"doesn't match completely (?P<path>\S+)")
_DIFF_LINE = re.compile(r"^[-+](?![-+]{2})")


SYMBOLS_MACRO = "@@tag-missing-symbols@@"
_SYMBOL_NAME = re.compile(r'^\s*(?:\(.*?\))?\s*"?(?P<name>[^\s"@]+@\S+)')


def missing_symbols(log_lines: Sequence[str]) -> list[str]:
    """The symbols dpkg-gensymbols reports missing (name@Base), from the diff in the log."""
    start = next((i for i, line in enumerate(log_lines) if _SYMBOLS_ERROR.search(line)), 0)
    names: list[str] = []
    for line in log_lines[start:start + 20000]:
        if line.startswith("+#MISSING:"):
            body = line[1:]
        elif line.startswith("-") and not line.startswith("---"):
            body = line[1:]
        else:
            continue
        body = re.sub(r"^\s*#MISSING:[^#]*#", "", body)
        match = _SYMBOL_NAME.match(body)
        if match and match.group("name") not in names:
            names.append(match.group("name"))
    return names


def debian_symbols(log_lines: Sequence[str], worktree: Path, target_arch: str) -> str:
    start = next((i for i, line in enumerate(log_lines) if _SYMBOLS_ERROR.search(line)), None)
    if start is None:
        return ""
    files = []
    diff = []
    for line in log_lines[start:start + 4000]:
        found = _SYMBOLS_FILE.search(line)
        if found:
            files.append(found.group("path"))
        if _DIFF_LINE.match(line):
            diff.append(line.rstrip())
    missing = [line for line in diff if line.startswith("-")]
    added = [line for line in diff if line.startswith("+")]
    lines = [
        TITLE,
        "Debian symbols file mismatch (dpkg-gensymbols): the symbols the built library "
        f"exports on {target_arch or 'this architecture'} differ from debian/*.symbols, which "
        f"was written for another one. {len(missing)} listed symbols are missing from the "
        f"build and {len(added)} new ones appeared (the diff is in the log).",
    ]
    if files:
        lines.append("Symbols files named by dpkg-gensymbols: " + ", ".join(dict.fromkeys(files))
                     + "; the source file to edit is the matching input/debian/*.symbols"
                     + " (or *.symbols.<arch>).")
    if missing:
        lines.append("Missing symbols, as the diff lists them (first lines):")
        lines.extend(missing[:MAX_DIFF_LINES])
        if len(missing) > MAX_DIFF_LINES:
            lines.append(f"... and {len(missing) - MAX_DIFF_LINES} more in the log.")
    # Symbols tags take Debian names (arm64, amd64): (arch=!aarch64) never matches.
    tag_arch = archnames.debian(target_arch) or "arm64"
    target_file = ("input/debian/" + PurePosixPath(files[0]).name) if files else "input/debian/<package>.symbols"
    lines.append("`#MISSING: <version>#` is dpkg's diff notation for a symbol the build did not export; "
                 "it does not appear in the file, and must not be written into it.")
    lines.append("Repair: each missing symbol's line gets a tag in parentheses before the symbol name, "
                 "`(arch=!" + tag_arch + ")` if the symbol exists on every other architecture or `(optional)` "
                 "otherwise, following the file's existing tag style (`(c++)\"...\"` becomes "
                 "`(c++|optional)\"...\"`). The tag never goes after the version. The other "
                 f"{'lines stay' if len(missing) < 50 else 'thousands of lines stay'} as they are; do not delete the "
                 "file, do not change debian/control, and do not retype the file.")
    lines.append("The agent makes these line edits itself from the diff above when you send this single edit "
                 "(it reports how many lines it tagged):")
    lines.append('{"action": "edit", "hypothesis": "<why these symbols are absent on ' + tag_arch + '>", '
                 '"edits": [{"path": "' + target_file + '", "old_text": "' + SYMBOLS_MACRO
                 + '", "new_text": "optional"}]}')
    lines.append("Use \"new_text\": \"arch=!" + tag_arch + "\" instead of \"optional\" to tag them as "
                 "architecture-specific. Then reply stop once the prediction reads fixed.")
    lines.append(END)
    return "\n".join(lines)


# --- CMake: an imported target whose include directory does not exist -------
#
# ectrans on arm64 (docs/no-build-live-findings.md): libfiat-dev's exported
# CMake targets name a Fortran module directory that the package did not
# install on this architecture. CMake reports the first imported target it
# checks; the others from the same package fail the same way once that one is
# patched, and clearing the property drops the module directory the compile
# needs. The repair is a loop over every imported target of the package that
# replaces each missing directory with the one that exists.

_IMPORTED_TARGET = re.compile(
    r'Imported target "(?P<target>[^"\s]+)" includes non-existent path\s+"(?P<path>[^"]+)"\s+in its '
    r'(?P<property>INTERFACE_INCLUDE_DIRECTORIES|INTERFACE_LINK_LIBRARIES|IMPORTED_LOCATION\w*)')
_CMAKE_ERROR_FILE = re.compile(r'CMake Error (?:in|at) (?P<file>[\w./+-]+)')
_FIND_PACKAGE = re.compile(r'(?:ecbuild_)?find_package\s*\(\s*(?:NAME\s+)?(?P<name>[\w.+-]+)', re.IGNORECASE)
_FOREACH = re.compile(r'foreach\s*\(\s*(?P<var>\w+)\s+(?P<values>[^)]*)\)', re.IGNORECASE)
_LINK_LISTS = re.compile(
    r'(?:target_link_libraries\s*\(\s*(?P<linked>[\w${}.-]+)|\b(?:PUBLIC_LIBS|PRIVATE_LIBS|LIBS)\b)'
    r'(?P<args>(?:[^()]|\((?:[^()]|\([^()]*\))*\))*?)(?=\)|\b(?!(?:PUBLIC|PRIVATE|INTERFACE)\b)[A-Z_]{3,}\b)')
_PACKAGE_TARGET = re.compile(r'(?:add_library|add_executable|ecbuild_add_library|ecbuild_add_executable)'
                             r'\s*\(\s*(?:TARGET\s+)?(?P<name>[\w${}.-]+)', re.IGNORECASE)
_CMAKE_KEYWORDS = {"PUBLIC", "PRIVATE", "INTERFACE", "LINK_PUBLIC", "LINK_PRIVATE", "debug", "optimized",
                   "general"}
_CMAKE_FILES = (".txt", ".cmake")


def _cmake_sources(worktree: Path) -> list[tuple[str, str]]:
    return [(rel, text) for rel, text in _source_files(worktree, _CMAKE_FILES)
            if PurePosixPath(rel).name == "CMakeLists.txt" or rel.endswith(".cmake")]


def _expand(token: str, bindings: dict[str, list[str]]) -> list[str]:
    """Every value of ``token`` under the file's foreach bindings (one level)."""
    match = re.search(r"\$\{(\w+)\}", token)
    if not match:
        return [token]
    values = bindings.get(match.group(1))
    if not values:
        return []
    return [token.replace(match.group(0), value) for value in values]


def imported_targets(log_lines: Sequence[str], worktree: Path) -> dict:
    """The imported targets the log names, the ones linked beside them, and the
    find_package call that imports them; {} when the log has no such error."""
    text = "\n".join(log_lines)
    errors = [(m.group("target"), m.group("path"), m.group("property")) for m in _IMPORTED_TARGET.finditer(text)]
    if not errors:
        return {}
    named = list(dict.fromkeys(target for target, _, _ in errors))
    paths = list(dict.fromkeys(path for _, path, _ in errors))
    error_files = list(dict.fromkeys(m.group("file") for m in _CMAKE_ERROR_FILE.finditer(text)))
    sources = _cmake_sources(worktree)
    package_targets: set[str] = set()
    bindings_by_file: dict[str, dict[str, list[str]]] = {}
    for rel, src in sources:
        bindings: dict[str, list[str]] = {}
        for m in _FOREACH.finditer(src):
            values = [v for v in m.group("values").split() if "${" not in v and v.upper() not in ("IN", "LISTS", "ITEMS", "RANGE")]
            if values:
                bindings.setdefault(m.group("var"), values)
        bindings_by_file[rel] = bindings
        for m in _PACKAGE_TARGET.finditer(src):
            package_targets.update(_expand(m.group("name"), bindings))
    siblings: dict[str, str] = {}   # target -> "file:line" where it is linked beside a named one
    for rel, src in sources:
        bindings = bindings_by_file[rel]
        for m in _LINK_LISTS.finditer(src):
            tokens = []
            for raw in m.group("args").split():
                if raw in _CMAKE_KEYWORDS or raw.startswith("$<"):
                    continue
                tokens.extend(_expand(raw, bindings))
            if not any(t in named for t in tokens):
                continue
            line = src.count("\n", 0, m.start()) + 1
            for t in tokens:
                if t in named or t in package_targets or "${" in t or t in siblings:
                    continue
                siblings[t] = f"{rel}:{line}"
    find_call = None
    for rel, src in sources:
        for m in _FIND_PACKAGE.finditer(src):
            name = m.group("name")
            if name.lower() in {n.lower() for n in named} or any(n.lower().startswith(name.lower()) for n in named):
                find_call = (rel, src.count("\n", 0, m.start()) + 1, src[m.start():src.find("\n", m.start()) if src.find("\n", m.start()) != -1 else None].strip())
                break
        if find_call:
            break
    return {"named": named, "paths": paths, "siblings": siblings, "find_package": find_call,
            "error_files": error_files}


def cmake_imported_target(log_lines: Sequence[str], worktree: Path, target_arch: str) -> str:
    facts = imported_targets(log_lines, worktree)
    if not facts:
        return ""
    named, paths, siblings = facts["named"], facts["paths"], facts["siblings"]
    arch = archnames.debian(target_arch) or target_arch or "this architecture"
    all_targets = named + list(siblings)
    lines = [
        TITLE,
        "Imported CMake target with a non-existent include directory: the build's dependency "
        f"exports target{'s' if len(named) > 1 else ''} " + ", ".join(f"`{n}`" for n in named)
        + " whose INTERFACE_INCLUDE_DIRECTORIES names " + ", ".join(f"`{p}`" for p in paths[:3])
        + f", a directory the dependency did not install on {arch} (its exported .cmake file was "
        "written for another compiler or layout). The exported file is outside input/ and cannot be "
        "edited; the package must repair the imported target after find_package imports it.",
        "Two facts decide the repair:",
        "1. CMake checks every imported target that any target links and reports only the first; "
        + ("the same package also provides " + ", ".join(f"`{t}` (linked at {where})" for t, where in list(siblings.items())[:8])
           + ", which will fail with the same error once the named one is patched. Cover all of them."
           if siblings else "cover every imported target of the same package, not only the one named."),
        "2. Clearing the property is not enough: the directory holds the Fortran .mod files (or headers) "
        "the compile needs, so a `use` statement then fails with 'Cannot open module file'. Replace each "
        "missing directory with the one that exists. On Debian/Ubuntu, dh-fortran-mod installs modules "
        "under /usr/include/<triplet>/fortran/gfortran-mod-NN/<name> or /usr/lib/<triplet>/fortran/"
        "gfortran-mod-NN/<name>; a glob over fortran/*/<name> under both prefixes finds it without "
        "naming the compiler version.",
    ]
    where = facts["find_package"]
    if where:
        lines.append(f"Place the repair in {where[0]} immediately after line {where[1]} "
                     f"(`{where[2]}`), so it runs after the targets are imported and before any "
                     "add_subdirectory. One edit, in that file; do not edit the subdirectories' "
                     "CMakeLists.txt.")
    else:
        lines.append("Place the repair in the top-level input/CMakeLists.txt immediately after the "
                     "find_package call that imports the target.")
    lines.append("Repair (adjust only the target list):")
    lines.append(cmake_repair_snippet(all_targets))
    lines.append(END)
    return "\n".join(lines)


def cmake_repair_snippet(targets: Sequence[str]) -> str:
    names = " ".join(targets) or "<imported targets>"
    return "\n".join([
        "# The dependency's exported targets name include directories that do not exist",
        "# on this architecture; point each at the directory that does.",
        f"foreach( _dep_target {names} )",
        "  if( TARGET ${_dep_target} )",
        "    get_target_property( _dep_dirs ${_dep_target} INTERFACE_INCLUDE_DIRECTORIES )",
        "    if( _dep_dirs )",
        "      set( _dep_fixed )",
        "      foreach( _dep_dir ${_dep_dirs} )",
        "        if( NOT EXISTS \"${_dep_dir}\" )",
        "          get_filename_component( _dep_leaf \"${_dep_dir}\" NAME )",
        "          string( REGEX REPLACE \"^/usr/(lib|include)/([^/]+)/.*$\" \"\\\\2\" _dep_triplet \"${_dep_dir}\" )",
        "          file( GLOB _dep_found \"/usr/include/${_dep_triplet}/fortran/*/${_dep_leaf}\"",
        "                               \"/usr/lib/${_dep_triplet}/fortran/*/${_dep_leaf}\" )",
        "          if( _dep_found )",
        "            list( GET _dep_found 0 _dep_dir )",
        "          else()",
        "            set( _dep_dir \"\" )",
        "          endif()",
        "        endif()",
        "        if( _dep_dir )",
        "          list( APPEND _dep_fixed \"${_dep_dir}\" )",
        "        endif()",
        "      endforeach()",
        "      set_target_properties( ${_dep_target} PROPERTIES INTERFACE_INCLUDE_DIRECTORIES \"${_dep_fixed}\" )",
        "    endif()",
        "  endif()",
        "endforeach()",
    ])


# --- setup.py build_sphinx no longer exists ---------------------------------
#
# python-intbitset on amd64 (round 10): Sphinx 7 removed its setup.py command,
# so `python3 setup.py build_sphinx` in debian/rules fails with "invalid
# command"; the arm64 build never ran that step (it is -indep). The agent's
# first repair disabled the override, and dh_install then failed because the
# -doc package's .install file expects the HTML the step produced.

_BUILD_SPHINX = re.compile(r"error: invalid command 'build_sphinx'")
_SETUP_CFG_SECTION = re.compile(r"^\[build_sphinx\]\s*$(?P<body>(?:\n(?!\[).*)*)", re.M)


def _setup_cfg_sphinx(worktree: Path) -> tuple[str, str]:
    """(source-dir, build-dir) from setup.cfg's [build_sphinx], with Sphinx's defaults."""
    source, build = "", "build/sphinx"
    try:
        text = (Path(worktree) / "input" / "setup.cfg").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    match = _SETUP_CFG_SECTION.search(text)
    if match:
        for line in match.group("body").splitlines():
            key, _, value = line.partition("=")
            key = key.strip().replace("_", "-")
            if key == "source-dir":
                source = value.strip()
            elif key == "build-dir":
                build = value.strip()
    if not source:
        for candidate in ("doc", "docs"):
            if (Path(worktree) / "input" / candidate / "conf.py").is_file():
                source = candidate
                break
    return source or "doc", build


def _install_expectations(worktree: Path, needle: str) -> list[str]:
    """debian/*.install, *.docs, *.links lines that name ``needle``."""
    found = []
    debian = Path(worktree) / "input" / "debian"
    if not debian.is_dir():
        return found
    for path in sorted(debian.iterdir()):
        if path.suffix not in (".install", ".docs", ".links") or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for number, line in enumerate(lines, start=1):
            if needle and needle.strip("./") in line:
                found.append(f"input/debian/{path.name}:{number}: {line.strip()}")
    return found


def sphinx_build_command(log_lines: Sequence[str], worktree: Path, target_arch: str) -> str:
    if not any(_BUILD_SPHINX.search(line) for line in log_lines):
        return ""
    rules = _read_text(Path(worktree) / "input" / "debian" / "rules")
    where = [f"input/debian/rules:{n}: {line.strip()}" for n, line in enumerate(rules.splitlines(), start=1)
             if "build_sphinx" in line]
    source, build = _setup_cfg_sphinx(worktree)
    html = build.rstrip("/") + "/html"
    expects = _install_expectations(worktree, build)
    lines = [
        TITLE,
        "`setup.py build_sphinx` no longer exists: Sphinx 7 removed its setuptools command, so the "
        "documentation step fails with \"invalid command 'build_sphinx'\" on any architecture that runs "
        "it. It is in an architecture-independent (-indep) target, which the other architecture's build "
        "did not run; that is the whole difference between the two builds."
        + (" The call: " + "; ".join(where[:2]) + "." if where else ""),
        f"setup.cfg's [build_sphinx] section says source-dir `{source}` and build-dir `{build}`, so the "
        f"step used to write HTML to `{html}`."
        + (" That directory is an output contract: " + "; ".join(expects[:3])
           + ". Disabling, renaming or deleting the step makes dh_install fail with \"missing files\" "
           "instead." if expects else ""),
        "Repair: keep the override target exactly as named and replace only its command with the "
        "equivalent direct Sphinx invocation (python3-sphinx is already in Build-Depends):",
        f"\tpython3 -m sphinx -b html {source} {html}",
        "(a recipe line starts with a tab). Nothing else needs to change.",
        END,
    ]
    return "\n".join(lines)


# --- a non-PIC static archive linked into a shared object -------------------
#
# pybigwig on amd64 (round 10): the extension links -lBigWig; the runtime
# package libbigwig0t64 is 3 KB on amd64 and ships no shared library, so the
# -dev package's libBigWig.so symlink dangles and ld falls back to the static
# archive, whose objects are not PIC. Nothing in the package can fix that; the
# agent should say so instead of adding -fPIC to its own flags (round 10 did).

_NONPIC = re.compile(r"(?P<archive>/usr/lib/[\w./+-]*?/lib(?P<name>[\w+-]+)\.a)\([\w.+-]+\): relocation "
                     r"R_(?P<reloc>\w+) against (?:symbol )?[`'](?P<symbol>[^'`]+)['`]? can not be used when making a shared object")
_GET_PACKAGE = re.compile(r"Get:\d+ \S+ \S+ (?P<arch>\w+) (?P<pkg>\S+) \w+ (?P<version>\S+) \[(?P<size>[\d.,]+) (?P<unit>k?B)\]")


def nonpic_static_archive(log_lines: Sequence[str], worktree: Path, target_arch: str) -> str:
    match = next((m for line in log_lines for m in [_NONPIC.search(line)] if m), None)
    if match is None:
        return ""
    name = match.group("name")
    packages = []
    for line in log_lines:
        got = _GET_PACKAGE.search(line)
        if got and name.lower() in got.group("pkg").lower():
            size = float(got.group("size").replace(",", "")) * (1000 if got.group("unit") == "kB" else 1)
            packages.append((got.group("pkg"), got.group("version"), int(size)))
    runtime = [p for p in packages if not p[0].endswith("-dev")]
    arch = archnames.debian(target_arch) or target_arch or "this architecture"
    lines = [
        TITLE,
        f"Non-PIC static archive in a shared object: the link pulled `{match.group('archive')}` and its "
        f"objects carry {match.group('reloc')} relocations against `{match.group('symbol')}`, which a shared "
        f"object (here a Python extension) cannot contain on {arch}. The linker only uses a static archive "
        f"when no shared `lib{name}.so.N` resolves in the build root.",
    ]
    if runtime:
        pkg, version, size = runtime[0]
        lines.append(f"The runtime package `{pkg}` {version} downloaded for {arch} is {size:,} bytes"
                     + (": a shared-library package that small ships no library, so the -dev package's "
                        f"`lib{name}.so` symlink points at nothing and ld fell back to the archive."
                        if size < 20_000 else "."))
    lines.append("Adding -fPIC to this package's CFLAGS or LDFLAGS does not recompile the archive, so the error "
                 "recurs unchanged. The repair belongs in the library's package (ship the shared library or build "
                 "the archive with -fPIC), not here; the only in-package route is compiling the library's sources "
                 "into the extension, which works only if they are in input/ (check for a bundled copy).")
    lines.append("If no bundled sources exist, make no edit and reply stop with that reason; if asked to "
                 "reconsider, reply stop again.")
    lines.append(END)
    return "\n".join(lines)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def recognise(diagnostics: Sequence, log_path: Path | None, worktree: Path,
              target_arch: str = "", *, skip_rust: bool = False) -> str:
    """One rendered block for the first recognised pattern, or ""."""
    for recogniser in (rust_char_signedness, x86_intrinsic_header):
        if skip_rust and recogniser is rust_char_signedness:
            continue
        try:
            block = recogniser(diagnostics, worktree, target_arch)
        except Exception:  # advice must never break a run
            block = ""
        if block:
            return block
    log_lines = _log_lines(log_path)
    for recogniser in (arm_outline_atomics, debian_symbols, cmake_imported_target,
                       sphinx_build_command, nonpic_static_archive):
        try:
            block = recogniser(log_lines, worktree, target_arch)
        except Exception:
            block = ""
        if block:
            return block
    return ""
