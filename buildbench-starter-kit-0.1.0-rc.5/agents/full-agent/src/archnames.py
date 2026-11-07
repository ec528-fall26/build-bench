"""Architecture names as each tool spells them, and the facts that differ (0.5.1).

The case metadata says aarch64 or x86_64 (the kernel and GNU names), while
Debian files want arm64 or amd64: DEB_HOST_ARCH, Architecture fields, symbols
tags such as (arch=!arm64), and Build-Depends [arm64]. A tag written with the
GNU name never matches and silently does nothing, and earlier runs mixed the
two (the 15 October bundle notes, "aarch64 versus Debian's arm64"). The
competition also lists riscv64 among the evaluated architectures (task.html),
which no development case covers yet.

Standard library only.
"""

from __future__ import annotations

_DEBIAN = {
    "x86_64": "amd64", "amd64": "amd64", "x86-64": "amd64",
    "aarch64": "arm64", "arm64": "arm64",
    "riscv64": "riscv64", "riscv": "riscv64",
    "i386": "i386", "i686": "i386", "x86": "i386",
    "armhf": "armhf", "armv7l": "armhf", "ppc64el": "ppc64el", "ppc64le": "ppc64el",
    "s390x": "s390x",
}
_TRIPLET = {
    "amd64": "x86_64-linux-gnu", "arm64": "aarch64-linux-gnu",
    "riscv64": "riscv64-linux-gnu", "i386": "i386-linux-gnu",
    "armhf": "arm-linux-gnueabihf", "ppc64el": "powerpc64le-linux-gnu",
    "s390x": "s390x-linux-gnu",
}
_MACROS = {
    "amd64": "__x86_64__", "arm64": "__aarch64__", "riscv64": "__riscv (and __riscv_xlen == 64)",
    "i386": "__i386__", "armhf": "__arm__", "ppc64el": "__powerpc64__", "s390x": "__s390x__",
}
# Facts that commonly decide a cross-architecture failure.
_FACTS = {
    "amd64": "plain char is signed; long double is 80-bit x87; page size 4 KiB",
    "arm64": ("plain char is UNSIGNED; long double is 128-bit (software IEEE quad, "
              "no __float128 type or x86 intrinsics); page size may be 4, 16 or 64 KiB; "
              "GCC emits outline-atomics helpers (__aarch64_*) from libgcc"),
    "riscv64": ("plain char is UNSIGNED; long double is 128-bit; no x86 intrinsics or "
                "__float128; 1- and 2-byte __atomic_* operations need -latomic"),
    "i386": "plain char is signed; long double is 80-bit; 32-bit pointers and time_t may be 32-bit",
}

DEBIAN_NAMES = frozenset(_TRIPLET) | {
    "any", "all", "linux-any", "armel", "arm", "mips", "mipsel", "mips64el", "mips64",
    "powerpc", "ppc64", "powerpcspe", "sparc", "sparc64", "s390", "hppa", "alpha", "ia64",
    "m68k", "sh4", "x32", "loong64", "loongarch64", "arc", "or1k",
    "kfreebsd-amd64", "kfreebsd-i386", "hurd-i386", "hurd-amd64"}


def debian(arch: str | None) -> str:
    """The Debian architecture name for ``arch`` ("" when unknown)."""
    return _DEBIAN.get((arch or "").strip().lower(), "")


def describe(arch: str | None) -> str:
    """One line for the case header: every spelling of the target and its facts."""
    name = debian(arch)
    if not name:
        return ""
    parts = [f"Debian name {name} (DEB_HOST_ARCH, Architecture fields, symbols tags "
             f"such as (arch=!{name}), Build-Depends [{name}])",
             f"GNU triplet {_TRIPLET[name]} (DEB_HOST_MULTIARCH, library paths)"]
    if name in _MACROS:
        parts.append(f"compiler macro {_MACROS[name]}")
    line = "Target names: " + "; ".join(parts) + "."
    if name in _FACTS:
        line += f"\nOn {name}: {_FACTS[name]}."
    return line
