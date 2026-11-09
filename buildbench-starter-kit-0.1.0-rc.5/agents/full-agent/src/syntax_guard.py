"""Catch an edit that breaks a file's syntax before it is written (0.5.1).

Without builds nothing tells the agent that its own edit does not compile. In
the 0.3.3 Development Validation run (111 small public cases, 6 November) about
29 of the 69 failed cases ended on an error different from the original one,
and several were errors the agent's edit introduced: "void value not ignored"
in xmbmon's edited io_cpu.h (both xmbmon cases), "expected primary-expression"
in sphde's edited header, a libtool error in pam-tmpdir, and seven cases that
now fail at an earlier stage than the original log (configure instead of build
or test: obs-time-source, ocaml). Every one of those runs was a certain
failure that a cheap check of the edited file could have sent back.

Each check compares the file after the batch with the same file before it and
reports only problems the batch introduced, so a file that already fails a
check (missing generated headers, Python 2, a stray brace in a macro) is judged
against itself and is never blamed on the edit:

- Python: compile() succeeds before and fails after.
- Shell scripts: sh -n / bash -n passes before and fails after (when present).
- C and C++ (C, headers, C++): the change in brace, parenthesis and bracket
  balance and in #if/#endif balance, outside comments and literals; and, when
  a compiler is on PATH, -fsyntax-only on the file before and after, with
  empty stand-ins for headers that are missing (config.h before configure),
  reporting only errors that are new and lie on a changed line.
- Rust and Go: the brace, parenthesis and bracket balance.
- Makefiles and debian/rules: ifeq/endif and define/endef balance, and an
  added recipe line indented with spaces instead of a tab ("missing separator").
- CMake: parenthesis balance and if/endif, foreach/endforeach, function/
  endfunction, macro/endmacro and while/endwhile balance.
- JSON: json.loads succeeds before and fails after.

A finding is advice to the loop, not a verdict: the loop sends the item back
once, and an identical resend is applied (``allow``), so a false alarm costs one
correction turn and never blocks a repair.

Standard library only; the compiler check runs only when cc/gcc/g++ exists.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from time import monotonic
import warnings

from .archnames import debian
from .edit_repair import simulate

C_SUFFIXES = {".c", ".h"}
CXX_SUFFIXES = {".cc", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tcc", ".C", ".H"}
BRACE_SUFFIXES = {".rs", ".go", ".java", ".cs", ".js", ".ts"}
MAKE_NAMES = {"Makefile", "GNUmakefile", "makefile", "rules"}
MAKE_SUFFIXES = {".mk", ".make"}
MAX_FILE_BYTES = 512 * 1024
COMPILE_TIMEOUT = 15          # seconds per compiler call
COMPILE_BUDGET = 45           # seconds of compiler time per batch
RUN_BUDGET = 120              # seconds of checking per run (hosted runs get 17 min)
MAX_STUBS = 12                # missing headers replaced by empty files, per file
EXCERPT_LINES = 6


@dataclass
class Problem:
    path: str
    message: str
    line: int | None = None          # 1-based, in the text after the batch
    hard: bool = False               # an unchanged resend does not bypass it


# --- helpers -----------------------------------------------------------------

def _kind(path: str, text: str) -> str | None:
    pure = PurePosixPath(path)
    name, suffix = pure.name, pure.suffix
    if pure.parent.name == "debian" and re.fullmatch(r"(?:.+\.)?symbols(?:\.[a-z0-9-]+)?", name):
        return "symbols"
    if name in MAKE_NAMES and (name != "rules" or pure.parent.name == "debian"):
        return "make"
    if suffix in MAKE_SUFFIXES or name.startswith("Makefile."):
        return "make"
    if name == "CMakeLists.txt" or suffix == ".cmake":
        return "cmake"
    if suffix == ".py":
        return "python"
    if suffix == ".json":
        return "json"
    if suffix in CXX_SUFFIXES:
        return "c++"
    if suffix in C_SUFFIXES:
        return "c"
    if suffix in BRACE_SUFFIXES:
        return "braces"
    if suffix in {".sh", ".bash"}:
        return "shell"
    first = text.split("\n", 1)[0]
    if first.startswith("#!"):
        if re.search(r"\b(?:ba|da|k)?sh\b", first):
            return "shell"
        if "python" in first:
            return "python"
    return None


def _strip_c(text: str, *, backtick: bool = False, keep_preprocessor: bool = False) -> str:
    """Code without comments, string and character literals, keeping newlines.

    Preprocessor lines (with their continuations) are blanked unless asked for,
    because a macro body may hold half a brace pair.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if two == "/*":
            j = text.find("*/", i + 2)
            chunk = text[i:n if j < 0 else j + 2]
            out.append("\n" * chunk.count("\n"))
            i = n if j < 0 else j + 2
            continue
        if c == '"' or (c == "`" and backtick):
            j = i + 1
            while j < n and text[j] != c and not (c == '"' and text[j] == "\n"):
                j += 2 if (text[j] == "\\" and c == '"') else 1
            chunk = text[i:j + 1]
            out.append('""' + "\n" * chunk.count("\n"))
            i = j + 1
            continue
        if c == "'":
            # A C character literal; Rust lifetimes ('a) have no closing quote
            # within three characters and are left alone.
            m = re.match(r"'(?:\\.[^']{0,8}|[^'\\\n])'", text[i:i + 12])
            if m:
                out.append("''")
                i += m.end()
                continue
        out.append(c)
        i += 1
    code = "".join(out)
    if keep_preprocessor:
        return code
    lines, blank = code.split("\n"), False
    for k, line in enumerate(lines):
        if blank or line.lstrip().startswith("#"):
            blank = line.rstrip().endswith("\\")
            lines[k] = ""
    return "\n".join(lines)


def _balance(code: str) -> dict[str, int]:
    return {pair: code.count(pair[0]) - code.count(pair[1]) for pair in ("{}", "()", "[]")}


_NAMES = {"{}": "braces { }", "()": "parentheses ( )", "[]": "brackets [ ]"}


def _balance_problems(path: str, before: str, after: str, *, backtick=False) -> list[str]:
    old, new = (_balance(_strip_c(t, backtick=backtick)) for t in (before, after))
    found = []
    for pair, value in new.items():
        if value != old[pair]:
            delta = value - old[pair]
            found.append(f"the edit leaves {abs(delta)} unmatched "
                         f"{'opening' if delta > 0 else 'closing'} {_NAMES[pair]} "
                         f"(the file's balance went from {old[pair]} to {value})")
    return found


_PP_OPEN = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef)\b", re.MULTILINE)
_PP_CLOSE = re.compile(r"^\s*#\s*endif\b", re.MULTILINE)


def _preprocessor_problems(before: str, after: str) -> list[str]:
    def depth(text):
        code = _strip_c(text, keep_preprocessor=True)
        return len(_PP_OPEN.findall(code)) - len(_PP_CLOSE.findall(code))
    old, new = depth(before), depth(after)
    if old == new:
        return []
    word = "#endif without its #if" if new < old else "#if/#ifdef without its #endif"
    return [f"the edit leaves an unmatched {word} (open conditionals went from {old} to {new})"]


# --- per-language checks -----------------------------------------------------

def _compiles(text: str, path: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")    # invalid escapes etc. are not our business
        compile(text, path, "exec", dont_inherit=True)


def _python(before: str, after: str, path: str) -> list[Problem]:
    try:
        _compiles(before, path)
    except (SyntaxError, ValueError):
        return []                      # Python 2 or already broken: no baseline
    try:
        _compiles(after, path)
    except SyntaxError as exc:
        return [Problem(path, f"Python syntax error: {exc.msg}", exc.lineno)]
    except ValueError as exc:
        return [Problem(path, f"Python cannot compile the file: {exc}")]
    return []


def _json(before: str, after: str, path: str) -> list[Problem]:
    try:
        json.loads(before)
    except ValueError:
        return []
    try:
        json.loads(after)
    except ValueError as exc:
        return [Problem(path, f"invalid JSON: {exc.msg}", getattr(exc, "lineno", None))]
    return []


def _shell(before: str, after: str, path: str) -> list[Problem]:
    first = after.split("\n", 1)[0]
    shell = shutil.which("bash" if "bash" in first or path.endswith(".bash") else "sh")
    if shell is None:
        return []

    def check(text):
        try:
            done = subprocess.run([shell, "-n"], input=text, capture_output=True, text=True,
                                  timeout=10, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                   "LC_ALL": "C"})
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.returncode, done.stderr.strip()

    old, new = check(before), check(after)
    if old is None or new is None or old[0] != 0 or new[0] == 0:
        return []
    line = re.search(r"line (\d+)", new[1])
    return [Problem(path, f"shell syntax error ({PurePosixPath(shell).name} -n): "
                    f"{new[1].splitlines()[-1][:300]}", int(line.group(1)) if line else None)]


_MAKE_IF = re.compile(r"^ *(?:ifeq|ifneq|ifdef|ifndef)\b")
_MAKE_ENDIF = re.compile(r"^ *endif\b")
_MAKE_DIRECTIVE = re.compile(
    r"^\s*(?:ifeq|ifneq|ifdef|ifndef|else|endif|include|-include|sinclude|export|unexport|"
    r"override|define|endef|vpath|undefine|private)\b")
_MAKE_ASSIGN = re.compile(r"^\s*[^\s:#=]+\s*(?::{1,3}|\+|\?|!)?=")
_MAKE_RULE = re.compile(r"^[^\t#\s][^=#]*?(?<!:):(?!=)")


def _make(before: str, after: str, path: str) -> list[Problem]:
    found = []

    def counts(text):
        lines = text.split("\n")
        ifs = sum(1 for l in lines if _MAKE_IF.match(l))
        ends = sum(1 for l in lines if _MAKE_ENDIF.match(l))
        defs = sum(1 for l in lines if re.match(r"^\s*define\b", l))
        endefs = sum(1 for l in lines if re.match(r"^\s*endef\b", l))
        return ifs - ends, defs - endefs

    (old_if, old_def), (new_if, new_def) = counts(before), counts(after)
    if new_if != old_if:
        found.append(Problem(path, f"the edit leaves an unmatched "
                     f"{'ifeq/ifdef without endif' if new_if > old_if else 'endif without ifeq/ifdef'} "
                     f"(open conditionals went from {old_if} to {new_if}); make stops with "
                     "\"missing endif\" or \"extraneous 'endif'\""))
    if new_def != old_def:
        found.append(Problem(path, "the edit leaves an unmatched define/endef"))

    old_lines = Counter(before.split("\n"))
    lines = after.split("\n")
    in_recipe = False
    for number, line in enumerate(lines, 1):
        previous = lines[number - 2] if number > 1 else ""
        continued = previous.rstrip().endswith("\\")
        if line.startswith("\t"):
            in_recipe = in_recipe or not continued
            continue
        if not line.strip() or line.lstrip().startswith("#") or continued:
            continue
        added = old_lines[line] <= 0
        if (added and in_recipe and line[:1] == " " and not _MAKE_DIRECTIVE.match(line)
                and not _MAKE_ASSIGN.match(line)):
            found.append(Problem(path, "an added recipe line starts with spaces; make "
                                 "requires a TAB before each command in a rule and stops "
                                 "with \"missing separator\"", number))
            break
        if old_lines[line] > 0:
            old_lines[line] -= 1
        in_recipe = bool(_MAKE_RULE.match(line)) and not _MAKE_ASSIGN.match(line) \
            and not _MAKE_DIRECTIVE.match(line)
    return found


# dpkg-gensymbols (Dpkg::Shlibs::SymbolFile) reads
#   ' [(tag|tag2=value)]symbol[@Base] minimal-version [dependency-id]'
# with the tag group glued to the symbol. A space after the tag group makes the
# group the symbol and the real symbol the version, so dpkg warns "failed to
# parse line" and drops the line (libbpp-qt, 8 November: ' (optional) sym' was
# accepted and tagged nothing). libbpp-core rc3 wrote the tag after the version
# and rc3b put it in column 0, which tagged all 4,130 lines to no effect.
_QUOTED = r"\"(?:[^\"\\]|\\.)*\"(?:@\S*)?"
_SYMBOL_LINE = re.compile(
    r"^[ \t]+(?:\((?P<tags>[^()]*)\))?(?P<symbol>" + _QUOTED + r"|(?!\()\S+)"
    r"[ \t]+(?P<version>\S+)(?:[ \t]+(?P<depid>\d+))?[ \t]*$")
_DEB_VERSION = re.compile(r"^(?:\d+:)?[0-9][A-Za-z0-9.+~-]*$")
_TAG_GROUP = re.compile(r"\([^()]*\)")
_SYMBOL_TAIL = re.compile(r"^\s*(?P<symbol>" + _QUOTED + r"|\S+)\s+(?P<version>\S+)\s*(?P<depid>\d+)?\s*$")
_HEADER_LINE = re.compile(r"^[^\s@(|*#]\S*[ \t]+\S+(?:[ \t]+\S.*)?$")
MAX_SYMBOL_LINES = 200_000


def _symbol_line_ok(line: str) -> re.Match | None:
    m = _SYMBOL_LINE.match(line)
    if m is None:
        return None
    version = m.group("version")
    if version != "#MINVER#" and not _DEB_VERSION.match(version):
        return None
    return m


def _glued(line: str) -> str | None:
    """The line as dpkg wants it (one glued tag group before the symbol), when
    that is a mechanical rewrite of ``line``; None when it is not."""
    quoted = re.findall(r'"(?:[^"\\]|\\.)*"', line)       # "f()@Base" holds "()" itself
    masked = re.sub(r'"(?:[^"\\]|\\.)*"', "\0", line)
    inner = [g[1:-1] for g in _TAG_GROUP.findall(masked)]
    rest = _TAG_GROUP.sub(" ", masked).strip()
    m = _SYMBOL_TAIL.match(rest)
    if m is None or ("@" not in m.group("symbol") and "\0" not in m.group("symbol")):
        return None
    symbol = m.group("symbol")
    for text in quoted[:1]:
        symbol = symbol.replace("\0", text, 1)
    tags = "|".join(dict.fromkeys(t.strip() for t in "|".join(inner).split("|") if t.strip()))
    candidate = " " + (f"({tags})" if tags else "") + symbol + " " + m.group("version")
    if m.group("depid"):
        candidate += " " + m.group("depid")
    return candidate if _symbol_line_ok(candidate) else None


def symbols_lint(text: str) -> list[tuple[int, str, str | None]]:
    """(line number, line, corrected line or None) for every line of a symbols
    file that dpkg's parser would reject or drop. The whole file, every time.

    Accepted: the library header (' soname package minver' in column 0), '|'
    alternative-dependency and '* Field: value' lines, comments, '#include' and
    '#DEPRECATED:' lines, and symbol lines. '#MISSING:' markers are what
    dpkg-gensymbols writes into its own output; they do not belong in a source
    symbols file and are refused.
    """
    found = []
    for number, line in enumerate(text.split("\n")[:MAX_SYMBOL_LINES], 1):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("#"):
            if line.startswith("#MISSING:"):
                body = line.split("#", 2)[-1].strip()
                found.append((number, line.strip()[:200], (" " + body) if body else None))
            continue
        if line[0] in " \t":
            if _symbol_line_ok(line) is None:
                found.append((number, line.strip()[:200], _glued(line)))
            continue
        if line.startswith("|") or re.match(r"^\*\s+[\w-]+:", line):
            continue
        if "@" in line or _HEADER_LINE.match(line) is None:
            found.append((number, line.strip()[:200], _glued(line)))
    return found


def _symbols_issues(text: str) -> Counter:
    """Tags that can never match: arch= names that are not Debian architectures."""
    from .archnames import DEBIAN_NAMES
    issues = Counter()
    for line in text.split("\n")[:MAX_SYMBOL_LINES]:
        if not line.startswith((" ", "\t")) or not line.strip():
            continue
        m = _symbol_line_ok(line)
        if m is None:
            continue
        for tag in (m.group("tags") or "").split("|"):
            key, _, value = tag.partition("=")
            if key.strip() != "arch":
                continue
            for name in value.split():
                bare = name.lstrip("!")
                if bare not in DEBIAN_NAMES and not re.fullmatch(r"[a-z0-9]+-any|any-[a-z0-9]+", bare):
                    issues[("arch", bare)] += 1
    return issues


def _symbols(before: str, after: str, path: str) -> list[Problem]:
    """Whole-file grammar lint (a hard block) plus new impossible arch tags."""
    found = []
    bad = symbols_lint(after)
    if bad:
        numbers = ", ".join(str(n) for n, _, _ in bad[:20]) + (" ..." if len(bad) > 20 else "")
        shown = []
        for number, line, fix in bad[:3]:
            shown.append(f"line {number} {line[:120]!r}" + (f" should be {fix[:140]!r}" if fix else
                                                           " has no valid form"))
        found.append(Problem(
            path, f"file has {len(bad)} unparseable lines: {numbers}. dpkg-gensymbols cannot parse "
            "them and silently drops them, so they tag nothing. The form is "
            "' [(tag|tag2)]symbol@Base minimal-version': one space, the tag group glued to the "
            "symbol (no space after the ')'), tags before the version, never after. "
            + "; ".join(shown), bad[0][0], hard=True))
    old = _symbols_issues(before)
    for (kind, detail), count in _symbols_issues(after).items():
        if count > old.get((kind, detail), 0):
            found.append(Problem(path, f"the tag arch={detail} names no Debian architecture, "
                                 "so it never matches: symbols tags take Debian names such as "
                                 "arm64, amd64, riscv64 (not aarch64 or x86_64)", hard=True))
            break
    return found


_CMAKE_BLOCKS = (("if", "endif"), ("foreach", "endforeach"), ("while", "endwhile"),
                 ("function", "endfunction"), ("macro", "endmacro"))


def _cmake_code(text: str) -> str:
    text = re.sub(r"#\[(=*)\[.*?\]\1\]", "", text, flags=re.S)   # bracket comments
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text, flags=re.S)
    return re.sub(r"#[^\n]*", "", text)


def _cmake(before: str, after: str, path: str) -> list[Problem]:
    old, new = _cmake_code(before), _cmake_code(after)
    found = []
    od, nd = old.count("(") - old.count(")"), new.count("(") - new.count(")")
    if od != nd:
        found.append(Problem(path, f"the edit leaves unmatched parentheses "
                             f"(balance went from {od} to {nd})"))
    for start, end in _CMAKE_BLOCKS:
        def depth(code):
            return (len(re.findall(rf"(?im)^\s*{start}\s*\(", code))
                    - len(re.findall(rf"(?im)^\s*{end}\s*\(", code)))
        if depth(old) != depth(new):
            found.append(Problem(path, f"the edit leaves an unmatched {start}()/{end}() "
                                 f"(open blocks went from {depth(old)} to {depth(new)})"))
    return found


# --- the compiler check ------------------------------------------------------

_DIAG = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?:\d+:)? (?P<kind>fatal error|error): "
                   r"(?P<msg>.*)$", re.MULTILINE)


_PARSE_ERROR = re.compile(
    r"^expected |stray |unterminated|missing terminating|without #if|#else after #else|"
    r"#elif after #else|invalid preprocessing directive|void value not ignored|"
    r"'else' without a previous 'if'|‘else’ without a previous ‘if’|"
    r"label at end of compound statement|break statement not within|"
    r"continue statement not within|case label not within|"
    r"redefinition of|conflicting types for|lvalue required|"
    r"return-statement with a value, in function returning|"
    r"macro names must be identifiers|no macro name given|"
    r"extra tokens at end of|unbalanced|invalid suffix")


def _compiler(language: str) -> str | None:
    # BB_SYNTAX_GUARD_COMPILER=0: no compiler check, as in a sandbox without one.
    # The kit's managed runtime (python:3.11 slim) has no compiler, so a local or
    # Development Validation run on a host with gcc measures more than the
    # submitted agent does unless this is off.
    if os.environ.get("BB_SYNTAX_GUARD_COMPILER", "1").strip() == "0":
        return None
    names = ("g++", "c++", "clang++") if language == "c++" else ("gcc", "cc", "clang")
    return next((found for found in map(shutil.which, names) if found), None)


_X86_MACROS = ("__x86_64__", "__x86_64", "__amd64__", "__amd64", "__i386__", "__i386", "i386",
               "__SSE__", "__SSE2__", "__SSE_MATH__", "__SSE2_MATH__", "__MMX__", "__FXSR__",
               "__SSE3__", "__SSSE3__", "__SSE4_1__", "__SSE4_2__", "__AVX__", "__AVX2__",
               "__POPCNT__", "__MMX_WITH_SSE__", "__SEG_FS", "__SEG_GS", "__k8", "__k8__")
_ARM_MACROS = ("__aarch64__", "__ARM_64BIT_STATE", "__ARM_ARCH", "__ARM_NEON", "__arm__",
               "__ARM_ARCH_8A", "__ARM_FP", "__AARCH64EL__", "__AARCH64_CMODEL_SMALL__")
_RISCV_MACROS = ("__riscv", "__riscv_xlen", "__riscv_flen", "__riscv_atomic")
_TARGET_FLAGS = {
    "amd64": ["-D__x86_64__=1", "-D__x86_64=1", "-D__amd64__=1", "-D__amd64=1",
              "-D__SSE__=1", "-D__SSE2__=1", "-fsigned-char"],
    "arm64": ["-D__aarch64__=1", "-D__ARM_64BIT_STATE=1", "-D__ARM_ARCH=8",
              "-D__AARCH64EL__=1", "-D__CHAR_UNSIGNED__=1", "-funsigned-char"],
    "riscv64": ["-D__riscv=1", "-D__riscv_xlen=64", "-D__CHAR_UNSIGNED__=1", "-funsigned-char"],
}
_OWN = {"amd64": _X86_MACROS, "arm64": _ARM_MACROS, "riscv64": _RISCV_MACROS}


def _arch_flags(target: str) -> list[str]:
    """Flags that make this host's compiler take the target's #if branches.

    Not a cross compiler: the system headers stay the host's, so their errors
    are ignored and only the edited file and the files including it count.
    """
    import platform
    from .archnames import debian
    host = debian(platform.machine())
    if target not in _TARGET_FLAGS or target == host:
        return []
    undef = [f"-U{m}" for name, macros in _OWN.items() if name != target for m in macros]
    return undef + _TARGET_FLAGS[target]


LOG_SCAN_BYTES = 64 * 1024 * 1024
_COMPILERS = re.compile(rb"(?:^|[\s/'\"])(?:[\w.-]*-)?(?:gcc|g\+\+|cc|c\+\+|clang|clang\+\+)(?:-\d+)?\s")
_flag_cache: dict[tuple, list[str]] = {}


def _log_flags(log_path: Path | None, name: str, worktree: Path, source: Path) -> list[str]:
    """-D/-U/-std flags (and resolvable -I) of the failing build's compile of ``name``.

    The build's own macros decide which #if branch is compiled (xmbmon's
    LINUX); without them the check reads a branch the build never compiles.
    """
    if log_path is None:
        return []
    key = (str(log_path), name)
    if key not in _flag_cache:
        # The file's own compile command; failing that (the build stopped
        # before reaching it), the build's first one, whose defines most
        # packages share.
        _flag_cache[key] = []
        needle, first = name.encode(), None
        try:
            with open(log_path, "rb") as handle:
                scanned = 0
                for raw in handle:
                    scanned += len(raw)
                    if scanned > LOG_SCAN_BYTES:
                        break
                    if (b" -c " not in raw and not raw.rstrip().endswith(b" -c")) \
                            or not _COMPILERS.search(raw):
                        continue
                    if needle in raw:
                        first = raw
                        break
                    if first is None:
                        first = raw
        except OSError:
            pass
        if first is not None:
            _flag_cache[key] = _parse_flags(first.decode("utf-8", "replace"), name)
    flags = []
    for flag in _flag_cache[key]:
        if flag.startswith("-I"):
            relative = flag[2:]
            for base in (source.parent, worktree / "input"):
                candidate = (base / relative).resolve()
                if candidate.is_dir() and (worktree / "input").resolve() in (candidate, *candidate.parents):
                    flags.append(f"-I{candidate}")
                    break
        else:
            flags.append(flag)
    return flags


def _parse_flags(line: str, name: str) -> list[str]:
    import shlex
    line = re.sub(r"^\[\s*\d+s\]\s*", "", line.strip())
    try:
        words = shlex.split(line)
    except ValueError:
        words = line.split()
    kept, pending = [], None
    for word in words:
        if pending:
            kept.append(pending + word)
            pending = None
        elif word in ("-D", "-U", "-I"):
            pending = word
        elif word.startswith(("-D", "-U")) or re.fullmatch(r"-std=\S+|-f(?:un)?signed-char", word):
            kept.append(word)
        elif word.startswith("-I") and not word[2:].startswith("/"):
            kept.append(word)
    return kept[:200]


def _for_language(flags: list[str], language: str) -> list[str]:
    """Drop a -std= that belongs to the other language (a C++ line borrowed for C)."""
    def fits(flag):
        if not flag.startswith("-std="):
            return True
        cxx = "++" in flag
        return cxx == (language == "c++")
    return [f for f in flags if fits(f)]


_INCLUDE = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]', re.MULTILINE)
_UNIT_SUFFIXES = {".c": "c", ".cc": "c++", ".cpp": "c++", ".cxx": "c++", ".C": "c++"}
MAX_INCLUDERS = 2
INCLUDER_SCAN_FILES = 4000


def _includers(worktree: Path, path: str) -> list[tuple[Path, str, str]]:
    """Up to MAX_INCLUDERS source files that #include the header: (file, spelling, language)."""
    header = worktree / path
    name = header.name
    root = worktree / "input"
    found, scanned = [], 0
    candidates = sorted(header.parent.iterdir()) if header.parent.is_dir() else []
    seen = set()
    def consider(file):
        if file in seen or file.suffix not in _UNIT_SUFFIXES or not file.is_file():
            return None
        seen.add(file)
        try:
            if file.stat().st_size > MAX_FILE_BYTES:
                return None
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for m in _INCLUDE.finditer(text):
            spelled = m.group(2)
            if PurePosixPath(spelled).name == name:
                return (file, spelled, _UNIT_SUFFIXES[file.suffix])
        return None
    for file in candidates:
        hit = consider(file)
        if hit:
            found.append(hit)
            if len(found) >= MAX_INCLUDERS:
                return found
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in {".pc", "debian", ".git"})
        for filename in sorted(files):
            scanned += 1
            if scanned > INCLUDER_SCAN_FILES:
                return found
            hit = consider(Path(directory, filename))
            if hit:
                found.append(hit)
                if len(found) >= MAX_INCLUDERS:
                    return found
    return found


def _compile_errors(compiler, language, name, text, include_dirs, deadline, *,
                    overlay: dict[str, str] | None = None, flags=(), watch=()):
    """Errors from -fsyntax-only, with missing headers stubbed; None if unknown.

    ``overlay`` maps include spellings to header texts that shadow the
    worktree's (the edited header, before or after). Errors are kept for the
    compiled file and for any file named in ``watch``.
    """
    with tempfile.TemporaryDirectory(prefix="bb-syntax-") as tmp:
        src_dir, stub_dir, over_dir = Path(tmp, "src"), Path(tmp, "stubs"), Path(tmp, "over")
        for directory in (src_dir, stub_dir, over_dir):
            directory.mkdir()
        source = src_dir / name
        source.write_text(text, encoding="utf-8")
        for spelled, body in (overlay or {}).items():
            target = (over_dir / spelled).resolve()
            if over_dir.resolve() not in target.parents:
                return None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        header = name.endswith((".h", ".hh", ".hpp", ".hxx", ".h++", ".H"))
        lang = {"c": "c-header" if header else "c",
                "c++": "c++-header" if header else "c++"}[language]
        args = [compiler, "-fsyntax-only", "-w", "-fmax-errors=5000", "-x", lang,
                "-iquote", str(over_dir), "-I", str(over_dir), *flags,
                "-iquote", str(stub_dir), "-I", str(stub_dir)]
        for directory in include_dirs:
            args += ["-I", str(directory)]
        for _ in range(MAX_STUBS + 1):
            remaining = deadline - monotonic()
            if remaining <= 1:
                return None
            try:
                done = subprocess.run(args + [str(source)], capture_output=True, text=True,
                                      timeout=min(COMPILE_TIMEOUT, remaining),
                                      env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                           "LC_ALL": "C"})
            except (OSError, subprocess.TimeoutExpired):
                return None
            missing = re.search(r"fatal error: ([^:\n]+): No such file or directory",
                                done.stderr)
            if missing is None:
                break
            stub = (stub_dir / missing.group(1)).resolve()
            if stub_dir.resolve() not in stub.parents or stub.exists():
                return None
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text("", encoding="utf-8")
        else:
            return None
        if re.search(r"fatal error:", done.stderr):
            return None
        names = {name, *watch}
        errors = []
        for m in _DIAG.finditer(done.stderr):
            if Path(m.group("file")).name in names:
                errors.append((Path(m.group("file")).name, int(m.group("line")),
                               m.group("msg").strip()))
        return errors


def _line_map(before: str, after: str) -> tuple[set[int], dict[int, tuple[int, int]]]:
    """Changed lines of ``after`` (1-based), and for every line of ``after`` the
    range of ``before`` lines it corresponds to."""
    import difflib
    changed, where = set(), {}
    matcher = difflib.SequenceMatcher(None, before.split("\n"), after.split("\n"),
                                      autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                where[j1 + k + 1] = (i1 + k + 1, i1 + k + 1)
        else:
            changed.update(range(j1 + 1, max(j2, j1 + 1) + 1))
            for j in range(j1 + 1, max(j2, j1 + 1) + 1):
                where[j] = (i1 + 1, max(i2, i1 + 1))
    return changed, where


def _changed_lines(before: str, after: str) -> set[int]:
    return _line_map(before, after)[0]


def _new_parse_error(old, new, edited: str, before: str, after: str):
    """The first new parser-level error the edit causes, or None.

    In the edited file it must be near a changed line; in an unchanged file
    that includes an edited header it may be anywhere. Either way, a line that
    already had an error before the edit (often an unknown type from a
    dependency header this check cannot see) does not count, so files that
    never parse cleanly here can still be judged on the lines the edit touched.
    """
    if old is None or new is None:
        return None
    changed, where = _line_map(before, after)
    near = {k for line in changed for k in range(line - 2, line + 3)}
    baseline = Counter((f, msg) for f, _, msg in old)
    erring = {}
    for f, line, _ in old:
        erring.setdefault(f, set()).update(range(line - 3, line + 4))
    for file, line, msg in new:
        if baseline[(file, msg)] > 0:
            baseline[(file, msg)] -= 1
            continue
        if not _PARSE_ERROR.search(msg):
            continue
        if file == edited:
            if line not in near:
                continue
            low, high = where.get(line, (line, line))
            if any(k in erring.get(file, ()) for k in range(low, high + 1)):
                continue
        elif line in erring.get(file, ()):
            continue
        return file, line, msg
    return None


def _compiled(worktree: Path, path: str, language: str, before: str, after: str,
              deadline: float, *, target: str = "", log_path: Path | None = None) -> list[Problem]:
    """Compile the edited file, and the files including an edited header, for the target.

    Semantic errors (undeclared names, implicit declarations, wrong types)
    depend on headers, macros and flags this check does not fully have, so
    only parser-level errors are trusted, and only in a unit that parses
    cleanly here before the edit.
    """
    compiler = _compiler(language)
    if compiler is None or deadline - monotonic() < 2:
        return []
    full = worktree / path
    root = worktree / "input"
    base_dirs = [d for d in (full.parent, root, root / "include", root / "src") if d.is_dir()]
    name = PurePosixPath(path).name
    arch = _arch_flags(target)
    is_header = PurePosixPath(path).suffix in {".h", ".hh", ".hpp", ".hxx", ".h++", ".H"}

    units = []   # (main name, main text, language, overlay before, overlay after, flags, dirs)
    if is_header:
        for unit, spelled, unit_lang in _includers(worktree, path):
            try:
                text = unit.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            dirs = [d for d in (unit.parent, *base_dirs) if d.is_dir()]
            flags = arch + _for_language(_log_flags(log_path, unit.name, worktree, unit),
                                         unit_lang)
            units.append((unit.name, text, _compiler(unit_lang) or compiler, unit_lang,
                          {spelled: before}, {spelled: after}, flags, dirs))
    units.append((name, None, compiler, language, None, None,
                  arch + _for_language(_log_flags(log_path, name, worktree, full), language),
                  base_dirs))

    for unit_name, text, unit_compiler, unit_lang, over_old, over_new, flags, dirs in units:
        if deadline - monotonic() < 2:
            break
        if text is None:          # the edited file itself
            old = _compile_errors(unit_compiler, unit_lang, unit_name, before, dirs, deadline,
                                  flags=flags)
            new = None if old is None else _compile_errors(
                unit_compiler, unit_lang, unit_name, after, dirs, deadline, flags=flags)
        else:                     # an unchanged file that includes the edited header
            old = _compile_errors(unit_compiler, unit_lang, unit_name, text, dirs, deadline,
                                  overlay=over_old, flags=flags, watch=(name,))
            new = None if old is None else _compile_errors(
                unit_compiler, unit_lang, unit_name, text, dirs, deadline,
                overlay=over_new, flags=flags, watch=(name,))
        hit = _new_parse_error(old, new, name, before, after)
        if hit:
            file, line, msg = hit
            where = "" if file == name else f" (in {file}, which includes it)"
            arch_note = f" compiled for {target}" if arch else ""
            return [Problem(path, f"{PurePosixPath(unit_compiler).name} -fsyntax-only{arch_note} "
                            f"reports a new error{where}: {msg[:300]}",
                            line if file == name else None)]
    return []


# --- the batch ---------------------------------------------------------------

def file_problems(path: str, before: str, after: str, *, worktree: Path | None = None,
                  deadline: float | None = None, target: str = "",
                  log_path: Path | None = None) -> list[Problem]:
    """Problems that ``after`` has and ``before`` does not, for one file."""
    if before == after or len(after.encode("utf-8", "replace")) > MAX_FILE_BYTES:
        return []
    kind = _kind(path, after)
    if kind is None:
        return []
    if kind == "python":
        return _python(before, after, path)
    if kind == "json":
        return _json(before, after, path)
    if kind == "shell":
        return _shell(before, after, path)
    if kind == "make":
        return _make(before, after, path)
    if kind == "cmake":
        return _cmake(before, after, path)
    if kind == "symbols":
        return _symbols(before, after, path)
    found = [Problem(path, text) for text in _balance_problems(
        path, before, after, backtick=PurePosixPath(path).suffix == ".go")]
    if kind in {"c", "c++"}:
        found += [Problem(path, text) for text in _preprocessor_problems(before, after)]
        if not found and worktree is not None and deadline is not None:
            found += _compiled(worktree, path, kind, before, after, deadline,
                               target=target, log_path=log_path)
    return found


def edit_signature(edit) -> tuple:
    return (edit.path, edit.old_text, edit.new_text, bool(getattr(edit, "replace_all", False)))


def _culprit(snapshot, edits, path, originals, problems, after) -> int:
    """The index of the edit to send back for a problem in ``path``."""
    touching = [k for k, edit in enumerate(edits) if edit.path == path]
    if len(touching) == 1:
        return touching[0]
    line = next((p.line for p in problems if p.line), None)
    lines = after.split("\n")
    if line is not None and 1 <= line <= len(lines) and lines[line - 1].strip():
        holder = [k for k in touching if lines[line - 1].strip() in edits[k].new_text]
        if len(holder) == 1:
            return holder[0]
    for k in reversed(touching):
        without = edits[:k] + edits[k + 1:]
        text = simulate(snapshot, without).get(path, originals.get(path, ""))
        if not file_problems(path, originals.get(path, ""), text):
            return k
    return touching[-1]


def check_batch(snapshot, edits, *, worktree: Path | None = None,
                allow: set | frozenset = frozenset(), seconds: float = COMPILE_BUDGET,
                target_arch: str = "", log_path: Path | None = None):
    """The first edit whose file the batch breaks, with its problems, or None.

    Returns (index, problems, after_text). ``allow`` holds edit signatures the
    model has resent unchanged after a finding: those are applied as they are.
    """
    originals = {saved.path: saved.content.decode("utf-8") for saved in snapshot.files}
    finals = simulate(snapshot, edits)
    deadline = monotonic() + seconds
    for path in dict.fromkeys(edit.path for edit in edits):
        if path not in originals:
            continue
        problems = file_problems(path, originals[path], finals[path],
                                 worktree=worktree, deadline=deadline,
                                 target=debian(target_arch), log_path=log_path)
        if not problems:
            continue
        index = _culprit(snapshot, edits, path, originals, problems, finals[path])
        if edit_signature(edits[index]) in allow and not any(p.hard for p in problems):
            continue
        return index, problems, finals[path]
    return None


def _excerpt(text: str, line: int | None) -> str:
    lines = text.split("\n")
    if line is None or not 1 <= line <= len(lines):
        return ""
    low, high = max(1, line - EXCERPT_LINES), min(len(lines), line + EXCERPT_LINES)
    return "\n".join(f"{k}: {lines[k - 1][:300]}" for k in range(low, high + 1))


def feedback(index: int, edits, problems: list[Problem], after: str, hypothesis: str) -> str:
    """The focused correction request for an edit that breaks its file."""
    from .edit_preflight import FOCUSED_PREFIX
    edit = edits[index]
    blocks = [
        FOCUSED_PREFIX,
        f"The original batch has {len(edits)} edits and remains pending.",
        f"Original repair hypothesis: {str(hypothesis or '')[:1000]}",
        f"FOCUSED INDEX: {index}",
        "No pending edits have been written.",
        f"Reason: syntax_check. After this batch, {edit.path} would no longer parse, and "
        "nothing builds the package before it is submitted, so the edit would make the "
        "build fail. The file was checked as it is before the batch and after it; only "
        "problems the batch adds are reported:",
    ]
    blocks += [f"- {problem.message}" + (f" (line {problem.line})" if problem.line else "")
               for problem in problems[:5]]
    excerpt = _excerpt(after, next((p.line for p in problems if p.line), None))
    if excerpt:
        blocks.append(f"\n=== {edit.path} after the batch, around that line ===\n{excerpt}")
    hard = any(problem.hard for problem in problems)
    blocks += [
        "",
        f"Proposed NEW text of edit {index}: {edit.new_text[:1600]}",
        "Return exactly ONE replacement edit for this item, with path, old_text and "
        "new_text, that keeps the file well-formed (close every brace, #if and ifeq you "
        "open; use a TAB before recipe commands). Do not resend the other edits.",
    ]
    if hard:
        blocks.append("This is a format error, not a judgement call: the same edit sent "
                      "again is refused again. Send the corrected line.")
    else:
        blocks.append("If you are certain the edit is correct as it is (for example the check "
                      "cannot see a macro), resend it unchanged and it will be applied.")
    blocks.append("To remove this item, return action edit with edits: [].")
    return "\n".join(blocks)
