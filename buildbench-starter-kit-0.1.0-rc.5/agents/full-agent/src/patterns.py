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
    lines.append("Repair: in the symbols file, mark each missing entry as architecture-"
                 "specific or optional, for example `(arch=!" + tag_arch + ")`"
                 " or `(optional)` before the symbol name, following the file's existing "
                 "tag style; use a wildcard pattern such as `(c++)\"typeinfo for ...@Base\" "
                 "1.0` only if the file already uses them. Do not delete the file, and do "
                 "not change debian/control.")
    lines.append(END)
    return "\n".join(lines)


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
    for recogniser in (arm_outline_atomics, debian_symbols):
        try:
            block = recogniser(log_lines, worktree, target_arch)
        except Exception:
            block = ""
        if block:
            return block
    return ""
