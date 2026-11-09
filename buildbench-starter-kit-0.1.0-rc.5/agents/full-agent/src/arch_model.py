"""What differs between two CPU architectures, as data.

A cross-architecture build failure is a package that built on one
architecture and fails on another; the difference between the two is a small,
enumerable set of facts. This table is that set, for the architectures Debian
and the competition use. Everything that reasons about such a failure
(arch_slice, predict, patterns) reads its facts from here instead of
hard-coding "arm64 means u8".

Standard library only. Values are the GCC/Debian ones; "unknown" is allowed
and means a predictor must not claim anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class Architecture:
    dpkg: str                      # Debian architecture name: "arm64"
    gnu: str                       # GNU triplet: "aarch64-linux-gnu" (also the multiarch dir)
    uname: str                     # uname -m
    rust: str                      # Rust target_arch
    macros: frozenset[str]         # compiler macros defined (1) for this CPU
    char_signed: bool
    long_double_bits: int
    pointer_bits: int
    page_size: int
    little_endian: bool = True
    x86_intrinsics: bool = False   # <xmmintrin.h> and friends exist
    outline_atomics: bool = False  # GCC emits __aarch64_* helpers from libgcc
    aliases: frozenset[str] = field(default_factory=frozenset)
    # Macros whose value matters, not just their presence (the rest are 1).
    macro_values: tuple[tuple[str, int], ...] = ()

    @property
    def c_char(self) -> str:
        """The Rust/C fixed type that `char` is on this architecture."""
        return "i8" if self.char_signed else "u8"


ARCHITECTURES: dict[str, Architecture] = {}


def _add(arch: Architecture) -> None:
    for name in {arch.dpkg, arch.gnu, arch.uname, arch.rust, *arch.aliases}:
        ARCHITECTURES[name.lower()] = arch


_add(Architecture("amd64", "x86_64-linux-gnu", "x86_64", "x86_64",
                  frozenset({"__x86_64__", "__amd64__", "__x86_64", "__amd64", "__SSE__", "__SSE2__",
                             "__MMX__", "__LP64__", "_LP64"}),
                  char_signed=True, long_double_bits=80, pointer_bits=64, page_size=4096,
                  x86_intrinsics=True, aliases=frozenset({"x86-64", "x64"}), macro_values=(("__SIZEOF_POINTER__", 8),)))
_add(Architecture("i386", "i386-linux-gnu", "i686", "x86",
                  frozenset({"__i386__", "__i386", "i386", "__ILP32__"}),
                  char_signed=True, long_double_bits=80, pointer_bits=32, page_size=4096,
                  x86_intrinsics=True, aliases=frozenset({"i686", "x86_32"})))
_add(Architecture("arm64", "aarch64-linux-gnu", "aarch64", "aarch64",
                  frozenset({"__aarch64__", "__ARM_64BIT_STATE", "__ARM_ARCH", "__ARM_NEON", "__LP64__", "_LP64"}),
                  char_signed=False, long_double_bits=128, pointer_bits=64, page_size=4096,
                  outline_atomics=True, macro_values=(("__ARM_ARCH", 8), ("__SIZEOF_POINTER__", 8))))
_add(Architecture("armhf", "arm-linux-gnueabihf", "armv7l", "arm",
                  frozenset({"__arm__", "__ARM_ARCH", "__ARMEL__", "__ARM_PCS_VFP", "__ILP32__"}),
                  char_signed=False, long_double_bits=64, pointer_bits=32, page_size=4096,
                  aliases=frozenset({"armv7", "armv7l"}), macro_values=(("__ARM_ARCH", 7), ("__SIZEOF_POINTER__", 4))))
_add(Architecture("ppc64el", "powerpc64le-linux-gnu", "ppc64le", "powerpc64",
                  frozenset({"__powerpc64__", "__PPC64__", "__powerpc__", "__PPC__", "_ARCH_PPC64", "__LP64__"}),
                  char_signed=False, long_double_bits=128, pointer_bits=64, page_size=65536,
                  aliases=frozenset({"ppc64le"})))
_add(Architecture("s390x", "s390x-linux-gnu", "s390x", "s390x",
                  frozenset({"__s390x__", "__s390__", "__zarch__", "__LP64__"}),
                  char_signed=False, long_double_bits=128, pointer_bits=64, page_size=4096,
                  little_endian=False))
_add(Architecture("riscv64", "riscv64-linux-gnu", "riscv64", "riscv64",
                  frozenset({"__riscv", "__riscv_xlen", "__LP64__"}),
                  char_signed=False, long_double_bits=128, pointer_bits=64, page_size=4096,
                  macro_values=(("__riscv_xlen", 64), ("__SIZEOF_POINTER__", 8))))

# Every macro any architecture here defines: on another architecture it is
# undefined (evaluates to 0 in #if), which is what the evaluator needs to know.
ALL_ARCH_MACROS: frozenset[str] = frozenset().union(*(a.macros for a in set(ARCHITECTURES.values())))

# The builds are Debian/Ubuntu on Linux with GCC: these are defined whatever
# the CPU (value unknown unless given), and these others never are. A
# conditional on anything else is "unknown", never guessed.
LINUX_GCC_DEFINED: dict[str, int | None] = {
    "__GNUC__": None, "__GNUC_MINOR__": None, "__linux__": 1, "__linux": 1, "linux": 1,
    "__unix__": 1, "__unix": 1, "unix": 1, "__ELF__": 1, "__STDC__": 1, "__gnu_linux__": 1,
    "__STDC_VERSION__": None, "__cplusplus": None, "__SIZEOF_POINTER__": None,
}
NOT_LINUX_GCC: frozenset[str] = frozenset({
    "_MSC_VER", "_WIN32", "_WIN64", "WIN32", "WIN64", "_WINDOWS", "__MINGW32__", "__MINGW64__",
    "__CYGWIN__", "__APPLE__", "__MACH__", "__ANDROID__", "__sun", "__sun__", "__FreeBSD__",
    "__NetBSD__", "__OpenBSD__", "__DragonFly__", "__EMSCRIPTEN__", "__INTEL_COMPILER",
    "__BORLANDC__", "__WATCOMC__", "__TURBOC__", "__HAIKU__", "__QNX__", "__hpux", "_AIX",
})

_CASE_ID = re.compile(r"-(?P<a>amd64|arm64|i386|armhf|ppc64el|s390x|riscv64)-(?P<b>amd64|arm64|i386|armhf|ppc64el|s390x|riscv64)-")


def lookup(name: str | None) -> Architecture | None:
    return ARCHITECTURES.get((name or "").strip().lower()) or None


def pair_from_case_id(case_id: str | None) -> tuple[str, str] | None:
    """(source, target) dpkg names encoded in a case id such as
    launchpad-plucky-amd64-arm64-libunwind-...: the package built on the first
    and fails on the second."""
    match = _CASE_ID.search(case_id or "")
    return (match.group("a"), match.group("b")) if match else None


def source_for(target: Architecture | None, case_id: str | None = None) -> Architecture | None:
    """The architecture the package was built for before: the case id's first
    name when it has one, else the usual counterpart (amd64 for ARM targets,
    arm64 for x86 ones)."""
    pair = pair_from_case_id(case_id)
    if pair and target is not None and pair[1] == target.dpkg:
        return lookup(pair[0])
    if target is None:
        return None
    return lookup("arm64" if target.x86_intrinsics else "amd64")


@dataclass(frozen=True)
class Difference:
    fact: str
    source: str
    target: str
    consequence: str


def differences(source: Architecture, target: Architecture) -> list[Difference]:
    """The facts that differ, each with what it breaks; the order is the one
    the evidence is shown in (most often the cause first)."""
    out = []
    if source.char_signed != target.char_signed:
        out.append(Difference(
            "char signedness", f"signed ({source.c_char})", f"unsigned ({target.c_char})"
            if not target.char_signed else f"signed ({target.c_char})",
            "C code comparing a char with a negative value or EOF; Rust FFI that writes "
            f"*const {source.c_char} for C strings (the portable type is std::ffi::c_char)"))
    if source.x86_intrinsics != target.x86_intrinsics:
        has, lacks = (source, target) if source.x86_intrinsics else (target, source)
        out.append(Difference(
            "x86 intrinsics headers", "present" if source.x86_intrinsics else "absent",
            "present" if target.x86_intrinsics else "absent",
            f"<xmmintrin.h>, <emmintrin.h>, <immintrin.h>, cpuid.h and __builtin_ia32_* exist only on "
            f"{has.dpkg}; code using them must be guarded by defined(__x86_64__) || defined(__i386__) "
            f"with a portable fallback on {lacks.dpkg}"))
    if source.outline_atomics != target.outline_atomics:
        out.append(Difference(
            "outline atomics", str(source.outline_atomics), str(target.outline_atomics),
            "GCC on aarch64 emits __aarch64_cas*/__aarch64_ldadd* helpers from libgcc for atomic "
            "operations; a shared library linked without libgcc (-nostdlib) cannot export them. "
            "-mno-outline-atomics on arm64 inlines them"))
    if source.long_double_bits != target.long_double_bits:
        out.append(Difference(
            "long double", f"{source.long_double_bits} bits", f"{target.long_double_bits} bits",
            "tests asserting printed precision or sizeof(long double); hand-written struct layouts"))
    if source.page_size != target.page_size:
        out.append(Difference("page size", str(source.page_size), str(target.page_size),
                              "code or tests assuming 4096-byte pages"))
    if source.little_endian != target.little_endian:
        out.append(Difference("endianness", "little" if source.little_endian else "big",
                              "little" if target.little_endian else "big",
                              "byte-order assumptions in serialisation and tests"))
    if source.pointer_bits != target.pointer_bits:
        out.append(Difference("pointer width", f"{source.pointer_bits} bits", f"{target.pointer_bits} bits",
                              "casts between pointers and int, size_t formats"))
    out.append(Difference(
        "architecture names", f"{source.dpkg}, {source.gnu}, {source.uname}",
        f"{target.dpkg}, {target.gnu}, {target.uname}",
        "hard-coded multiarch paths (lib/x86_64-linux-gnu), Architecture: fields in debian/control, "
        "per-architecture debian/*.symbols.<arch> files, uname -m checks in scripts"))
    out.append(Difference(
        "compiler macros", ", ".join(sorted(source.macros - target.macros)[:3]) + ", ...",
        ", ".join(sorted(target.macros - source.macros)[:3]) + ", ...",
        "#if blocks on these macros select the code path; a block for the source only, with no "
        "#else, means the target compiles nothing or the wrong thing"))
    return out


def render(source: Architecture | None, target: Architecture | None) -> str:
    """The differences as a block for the model, or "" when either is unknown."""
    if source is None or target is None:
        return ""
    lines = [f"=== What differs between {source.dpkg} (where the package built) and "
             f"{target.dpkg} (where it fails) ==="]
    for d in differences(source, target):
        lines.append(f"- {d.fact}: {d.source} -> {d.target}. Breaks: {d.consequence}.")
    lines.append("=== End of differences ===")
    return "\n".join(lines)
