"""Where a package's build depends on the CPU architecture.

A cross-architecture failure is caused by a construct that assumes the source
architecture's value of some fact (arch_model), or by the lack of a guard
around something only one architecture has. This module finds every such
construct in the package (the "architecture-dependence slice"), evaluates
whether each is reached on the target (predict), and relates a diagnostic to
the constructs nearest to it. The result goes to the model next to the
errors: not advice, but the places where the build decides by architecture.

Bounded, standard library only, read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from .arch_model import ALL_ARCH_MACROS, Architecture
from .predict import Tri, cpp_active_lines, define_locations, make_active_lines

MAX_FILES = 4000
MAX_FILE_BYTES = 1024 * 1024
MAX_NEAR = 8          # constructs shown per diagnostic
MAX_PACKAGE = 8       # package-level constructs shown
_SKIP = ("/.pc/", "/.git/", "/debian/.debhelper/", "/target/", "/obj-", "/node_modules/")

C_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".in", ".m", ".mm")
RUST_SUFFIXES = (".rs",)
_X86_HEADERS = r"(?:[xepstnwa]mmintrin|immintrin|x86intrin|x86gprintrin|ia32intrin|cpuid|mm_malloc|avx\w*intrin)\.h"
_X86_INCLUDE = re.compile(r"^\s*#\s*include\s*[<\"](?P<header>" + _X86_HEADERS + r")[>\"]")
_X86_BUILTIN = re.compile(r"\b(?:__builtin_ia32_\w+|_mm_\w+|_mm256_\w+|_mm512_\w+|__cpuid\w*|__rdtsc)\s*\(")
_ARM_INCLUDE = re.compile(r"^\s*#\s*include\s*[<\"](?P<header>arm_neon\.h|arm_acle\.h|arm_sve\.h)[>\"]")
_INLINE_ASM = re.compile(r"\b(?:__asm__|asm)\s*(?:volatile\s*)?\(")
_CPP_COND = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b(?P<rest>.*)$")
_RUST_CFG = re.compile(r"cfg\s*\(.*?target_(?:arch|pointer_width|endian)\s*=\s*\"(?P<value>\w+)\"")
_RUST_CSTRING = re.compile(r"\*(?:const|mut)\s+(?P<type>[iu]8)\b")
_MAKE_ARCH = re.compile(r"\$\((?:DEB_HOST_ARCH\w*|DEB_BUILD_ARCH\w*|DEB_HOST_MULTIARCH|DEB_HOST_GNU_TYPE)\)|uname\s+-m|\barch\b.*\$\(shell")
_MAKE_COND_LINE = re.compile(r"^\s*(?:else\s+)?if(?:n?eq|n?def)\b|\$\((?:filter|findstring|if)\b")
SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".m", ".mm")   # not headers: their inclusion is often guarded
_PAYLOAD_KINDS = ("x86-intrinsics-include", "arm-intrinsics-include", "x86-intrinsic-call", "inline-asm",
                  "rust-cstring-pointer")
_ARCH_NAME = re.compile(r"\b(?:x86_64-linux-gnu|aarch64-linux-gnu|i386-linux-gnu|arm-linux-gnueabihf|"
                        r"powerpc64le-linux-gnu|s390x-linux-gnu|riscv64-linux-gnu|lib64)\b|"
                        r"(?<![\w-])(?:amd64|arm64|i386|armhf|ppc64el|s390x|riscv64|x86_64|aarch64)(?![\w-])")
_CONTROL_ARCH = re.compile(r"^Architecture:\s*(?P<value>.+)$")
_SYMBOLS_FILE = re.compile(r"^(?P<pkg>[^/]+)\.symbols\.(?P<arch>amd64|arm64|i386|armhf|ppc64el|s390x|riscv64)$")
_MAKEFILE_NAMES = ("makefile", "gnumakefile", "rules")


@dataclass
class Dependence:
    path: str
    line: int                 # 1-based; 0 for a whole-file fact
    kind: str
    text: str
    reached: Tri = None       # compiled/active on the target, when known
    note: str = ""


def _is_make(relative: str) -> bool:
    name = PurePosixPath(relative)
    return (name.parts[-2:] == ("debian", "rules") or name.name.lower() in _MAKEFILE_NAMES
            or name.suffix in (".mk", ".am", ".in") and name.name.lower().startswith(("makefile", "gnumakefile")))


def _files(worktree: Path) -> list[tuple[str, Path]]:
    from .tools import collect_files
    root = Path(worktree) / "input"
    if not root.is_dir():
        return []
    try:
        files, _ = collect_files(root)
    except OSError:
        return []
    out = []
    for path in files[:MAX_FILES]:
        relative = path.relative_to(worktree).as_posix()
        if any(part in "/" + relative + "/" for part in _SKIP):
            continue
        out.append((relative, path))
    return out


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _macros_in(expression: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_]\w*", expression)) & ALL_ARCH_MACROS


def _project_macros(expression: str, located: dict) -> str:
    """"; HAVE_X is defined at path:line: text" for the project macros in a guard."""
    names = [m for m in re.findall(r"[A-Za-z_]\w*", expression) if m in located]
    return "".join(f"; {name} is defined at {located[name][1]}" for name in dict.fromkeys(names))


def _scan_c(relative: str, text: str, target: Architecture, defines: dict, located: dict | None = None) -> list[Dependence]:
    out = []
    located = located or {}
    state = cpp_active_lines(text, target, defines)
    guards: list[tuple[int, str]] = []   # open #if lines above the current line (number, condition)
    for number, line in enumerate(text.split("\n"), start=1):
        reached = state.active[number - 1]
        cond = _CPP_COND.match(line)
        directive = re.match(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b", line)
        if cond and _macros_in(cond.group("rest")):
            out.append(Dependence(relative, number, "cpp-arch-guard", line.strip(), reached))
        if directive:
            name = directive.group(1)
            if name in ("if", "ifdef", "ifndef"):
                guards.append((number, line.strip()))
            elif name == "endif" and guards:
                guards.pop()
            elif name in ("elif", "else") and guards:
                guards[-1] = (number, line.strip())
            if cond and _macros_in(cond.group("rest")):
                continue
        include = _X86_INCLUDE.match(line)
        if include:
            if reached is False:
                guard = "guarded"
            elif reached:
                guard = "unguarded on this architecture"
            else:
                guard = "guard unknown"
            enclosing = "".join(f"; inside {g[1]} (line {g[0]})" + _project_macros(g[1], located) for g in guards[-2:])
            out.append(Dependence(relative, number, "x86-intrinsics-include", line.strip(), reached,
                                  f"{include.group('header')} exists only on x86; {guard}{enclosing}"))
            continue
        if _ARM_INCLUDE.match(line):
            out.append(Dependence(relative, number, "arm-intrinsics-include", line.strip(), reached))
            continue
        if _X86_BUILTIN.search(line) and not line.lstrip().startswith("//"):
            out.append(Dependence(relative, number, "x86-intrinsic-call", line.strip()[:160], reached))
        elif _INLINE_ASM.search(line):
            out.append(Dependence(relative, number, "inline-asm", line.strip()[:160], reached))
    return out


def _scan_rust(relative: str, text: str, target: Architecture) -> list[Dependence]:
    out = []
    count = {"i8": 0, "u8": 0}
    first = {}
    for number, line in enumerate(text.split("\n"), start=1):
        cfg = _RUST_CFG.search(line)
        if cfg:
            out.append(Dependence(relative, number, "rust-cfg", line.strip()[:160],
                                  cfg.group("value") in (target.rust, str(target.pointer_bits),
                                                         "little" if target.little_endian else "big")))
        for match in _RUST_CSTRING.finditer(line):
            count[match.group("type")] += 1
            first.setdefault(match.group("type"), number)
    wrong = "u8" if target.c_char == "i8" else "i8"   # the other architecture's char
    if count[wrong]:
        out.append(Dependence(relative, first[wrong], "rust-cstring-pointer", f"*const {wrong} / *mut {wrong}", True,
                              f"{count[wrong]} C-string pointer type(s) written as {wrong}; C char is "
                              f"{target.c_char} on {target.dpkg}; the portable type is std::ffi::c_char"))
    return out


def _scan_make(relative: str, text: str, target: Architecture) -> list[Dependence]:
    out = []
    state = make_active_lines(text, target)
    for number, line in enumerate(text.split("\n"), start=1):
        if _MAKE_ARCH.search(line) or (_MAKE_COND_LINE.match(line) and "ARCH" in line):
            # a conditional on the architecture decides a path; a plain use of
            # $(DEB_HOST_MULTIARCH) in a value only names it
            kind = "make-arch-conditional" if _MAKE_COND_LINE.search(line) else "arch-variable-use"
            out.append(Dependence(relative, number, kind, line.strip()[:160], state.active[number - 1]))
        elif _ARCH_NAME.search(line) and not line.lstrip().startswith("#"):
            out.append(Dependence(relative, number, "hard-coded-arch-name", line.strip()[:160], state.active[number - 1]))
    for name, value in state.flags.items():
        out.append(Dependence(relative, 0, "exported-build-flag", f"{name} = {value!r}", True,
                              f"reaches dpkg-buildflags on {target.dpkg}"))
    return out


def _scan_debian(relative: str, path: Path, text: str, target: Architecture) -> list[Dependence]:
    out = []
    name = PurePosixPath(relative).name
    if name == "control":
        seen_values: dict[str, int] = {}
        for number, line in enumerate(text.split("\n"), start=1):
            match = _CONTROL_ARCH.match(line)
            if match and match.group("value").strip() not in ("any", "all", "linux-any"):
                value = match.group("value").strip()
                if value in seen_values:
                    seen_values[value] += 1
                    continue
                seen_values[value] = 1
                included = target.dpkg in value.split() or "any" in value
                out.append(Dependence(relative, number, "control-architecture", line.strip()[:160], included,
                                      "the target is listed" if included else "the target is NOT in this list: "
                                      "the package is not built for it"))
    symbols = _SYMBOLS_FILE.match(name)
    if symbols:
        out.append(Dependence(relative, 0, "per-arch-symbols-file", name, symbols.group("arch") == target.dpkg,
                              f"written for {symbols.group('arch')}; dpkg-gensymbols compares the {target.dpkg} build "
                              "against the matching file (or the unsuffixed one)"))
    elif name.endswith(".symbols"):
        tagged = sum(1 for line in text.split("\n") if "(arch=" in line or "(optional" in line)
        out.append(Dependence(relative, 0, "symbols-file", name, True,
                              f"{tagged} entries carry (arch=...) or (optional) tags"))
    if name in ("rules",) or name.endswith((".install", ".links", ".dirs")):
        for number, line in enumerate(text.split("\n"), start=1):
            if name != "rules" and _ARCH_NAME.search(line):
                out.append(Dependence(relative, number, "hard-coded-arch-name", line.strip()[:160], None,
                                      "use $(DEB_HOST_MULTIARCH) or a wildcard"))
    return out


def scan(worktree: Path, target: Architecture) -> list[Dependence]:
    """Every architecture-dependent construct under input/, evaluated for ``target``."""
    files = _files(worktree)
    # Macros used in C conditionals that are the project's own (HAVE_*...): resolve once.
    wanted: set[str] = set()
    texts: dict[str, str] = {}
    for relative, path in files:
        suffix = PurePosixPath(relative).suffix
        if suffix in C_SUFFIXES or suffix in RUST_SUFFIXES or _is_make(relative) or "/debian/" in "/" + relative:
            text = _read(path)
            if text is None:
                continue
            texts[relative] = text
            if suffix in C_SUFFIXES:
                for line in text.split("\n"):
                    cond = _CPP_COND.match(line)
                    if cond:
                        wanted |= {m for m in re.findall(r"[A-Za-z_]\w*", cond.group("rest"))
                                   if m.startswith(("HAVE_", "USE_", "ENABLE_", "WITH_", "CONFIG_"))}
    located = define_locations(worktree, wanted, target) if wanted else {}
    defines = {name: value for name, (value, _) in located.items()}
    out: list[Dependence] = []
    for relative, path in files:
        text = texts.get(relative)
        if text is None:
            continue
        suffix = PurePosixPath(relative).suffix
        if "/debian/" in "/" + relative:
            out.extend(_scan_debian(relative, path, text, target))
        if _is_make(relative):
            out.extend(_scan_make(relative, text, target))
        elif suffix in C_SUFFIXES:
            out.extend(_scan_c(relative, text, target, defines, located))
        elif suffix in RUST_SUFFIXES:
            out.extend(_scan_rust(relative, text, target))
    return out


_ARCH_DIRS = {"x86", "x86_64", "amd64", "i386", "i686", "ia64", "aarch64", "arm64", "arm", "armv7", "mips",
              "mips64", "ppc", "ppc64", "powerpc", "powerpc64", "s390", "s390x", "riscv", "riscv64", "sparc",
              "sparc64", "hppa", "sh", "sh4", "loongarch", "loong64", "alpha", "m68k", "tilegx"}


def _other_architecture_dir(path: str, target: Architecture) -> bool:
    """A file under a directory named for another architecture (src/ia64/...,
    arch/x86/...) is selected by the build system, not by a guard inside it."""
    mine = {target.dpkg, target.gnu.split("-")[0], target.uname, target.rust}
    return any(part.lower() in _ARCH_DIRS and part.lower() not in mine
               for part in PurePosixPath(path).parts[:-1])


def near(dependences: list[Dependence], diagnostic) -> list[Dependence]:
    """The constructs that bear on one diagnostic: those in its file, nearest
    first, or the package-level ones when it names no file."""
    files = set(getattr(diagnostic, "resolved", None) or [])
    line = getattr(diagnostic, "line", None) or 0
    if files:
        same = [d for d in dependences if d.path in files]
        same.sort(key=lambda d: (abs(d.line - line) if d.line else 10**6))
        return same[:MAX_NEAR]
    # Package-level constructs, the ones that can be a cause first: a flag or
    # conditional that decides the build, a symbols file, a missing target in
    # Architecture:; a plain $(DEB_HOST_MULTIARCH) use or an Architecture
    # list that already names the target only when room remains.
    order = {"rust-cstring-pointer": 0, "exported-build-flag": 1, "make-arch-conditional": 2,
             "per-arch-symbols-file": 3, "symbols-file": 3, "hard-coded-arch-name": 4,
             "control-architecture": 5, "arch-variable-use": 6}
    package = [d for d in dependences if d.kind in order
               and not (d.kind == "control-architecture" and d.reached is True)]
    package.sort(key=lambda d: (order[d.kind], d.path, d.line))
    return package[:MAX_PACKAGE]


def render(dependences: list[Dependence], diagnostics, target: Architecture) -> str:
    """One block for the opening message, or "" when nothing was found."""
    if not dependences:
        return ""
    yes_no = {True: "yes", False: "no", None: "unknown"}
    lines = [f"=== Where this package's build depends on the architecture (evaluated for {target.dpkg}; "
             "a construct that is reached and assumes another architecture is the usual cause) ==="]
    shown: set[tuple] = set()
    for number, item in enumerate(list(diagnostics)[:4], start=1):
        chosen = [d for d in near(dependences, item) if (d.path, d.line, d.kind) not in shown]
        if not chosen:
            continue
        where = (item.resolved[0] if len(getattr(item, "resolved", []) or []) == 1 else "the package")
        lines.append(f"Near error {number} ({where}):")
        for d in chosen:
            shown.add((d.path, d.line, d.kind))
            place = f"{d.path}:{d.line}" if d.line else d.path
            lines.append(f"  {place} [{d.kind}] reached on {target.dpkg}: {yes_no[d.reached]}: {d.text}"
                         + (f" ({d.note})" if d.note else ""))
    # Only in source files: a header's inclusion is usually guarded by the
    # file that includes it (libunwind-arm.h holds ARM asm and is included
    # only on arm), which a per-file evaluation cannot see.
    unguarded = [d for d in dependences if d.kind in ("x86-intrinsics-include", "x86-intrinsic-call", "inline-asm")
                 and d.reached is True and (d.path, d.line, d.kind) not in shown
                 and PurePosixPath(d.path).suffix in SOURCE_SUFFIXES
                 and not _other_architecture_dir(d.path, target)]
    if unguarded and not target.x86_intrinsics:
        lines.append("Reached on this architecture with no guard (each needs an architecture guard or a portable fallback):")
        for d in unguarded[:MAX_PACKAGE]:
            lines.append(f"  {d.path}:{d.line} [{d.kind}]: {d.text}")
    total = len(dependences)
    # The count alone sent the model looking for the rest (rcran, round 8: six
    # tool turns after a verdict of fixed). Say what the rest evaluate to.
    rest = [d for d in dependences if (d.path, d.line, d.kind) not in shown and d not in unguarded]
    payload = [d for d in rest if d.kind in _PAYLOAD_KINDS]   # code that exists for one architecture
    def _in_source(d):
        return PurePosixPath(d.path).suffix in SOURCE_SUFFIXES and not _other_architecture_dir(d.path, target)
    reached = [d for d in payload if d.reached is True and _in_source(d)]
    elsewhere = sum(1 for d in payload if d.reached is True and not _in_source(d))
    not_reached = sum(1 for d in payload if d.reached is False)
    unknown = len(payload) - len(reached) - elsewhere - not_reached
    summary = (f"({total} architecture-dependent construct{'s' if total != 1 else ''} in the package in all; "
               f"of the {len(rest)} not shown, {len(rest) - len(payload)} are guards, conditionals and architecture "
               f"names (nothing to fix by themselves), {not_reached} are architecture-specific includes, calls or asm "
               f"guarded and not reached on {target.dpkg}, {elsewhere} are in headers or other architectures' "
               f"directories (compiled only where included or selected), {len(reached)} are reached there"
               + (": " + ", ".join(f"{d.path}:{d.line}" for d in reached[:3]) if reached else "")
               + f", {unknown} could not be evaluated"
               + (". Nothing else is reached unguarded; no need to search the rest.)" if not reached and not unknown
                  else ".)"))
    lines.append(summary)
    lines.append("=== End ===")
    return "\n".join(lines)
