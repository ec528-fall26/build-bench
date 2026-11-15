"""Streaming Part A evidence. log_offset is a zero-based byte offset in the
supplied log or excerpt. Source quotes come from the log, not edited files.

Besides the error itself, Part A keeps context the model needs to find a cause
rather than guess (1 November 2026, from the Demo 2 ectrans/libunwind runs):
gfortran's two-line errors with their file, the last compiler/linker command
before an error, and whether a missing Fortran module or header exists in the
package. Errors printed while the build environment was being prepared are
dropped once the package build itself is seen to start. Diagnostics stay in
the order printed.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re

from .contracts import Diagnostic

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# The local validator (OBS build) starts every log line with elapsed time: "[   12s] ".
_ELAPSED = re.compile(r"^\[\s*\d+s\] ?")
_COMPILER = re.compile(
    r"^(?P<path>.+?):(?P<line>\d{1,9})(?::\d+)?:\s*(?P<message>(?:fatal )?error:.*|Failure)$"
)
# Most of a large log is ordinary output. Filter bytes before decoding or running
# the richer parsers, while always retaining the lines following a diagnostic.
_RELEVANT = re.compile(rb"error|Error|ERROR|Failure|FAIL|Stop\.|undefined reference|dh_auto_|dpkg-|dh_builddeb|E: Build killed|ld(?:\.\w+)?: |Executing\(%|\.[fF]\w{0,3}:\d+(?::[\d-]+)?:\s*$")
_STAGE = re.compile(r"\b(?:dh_auto_|override_dh_auto_)(configure|build|test|install)\b")
_MAKE = re.compile(r"^(?:g?make)(?:\[\d+\])?:\s*\*\*\*.*(?:Error|Stop\.)")
_QUOTE = re.compile(r"^\s*(\d{1,9})\s*\| ?(.*)$")
# The linker's own messages ("/usr/bin/ld: ..."), other than warnings. libmceliece
# failed on "/bin/ld: BFD ... assertion fail ...", with no "error" in the line.
# Cross builds name the linker with its target ("/usr/bin/x86_64-linux-gnu-ld.bfd:",
# pybigwig after the QEMU fix, 2 November).
# Warnings are skipped, also when the linker names an object first ("ld: x.o: warning: ...").
_LINKER = re.compile(r"^(?:\S*/)?(?:[\w.+]+-)*ld(?:\.\w+)?: (?!(?:\S+: )?warning:)")
# CMake prints "CMake Error ...:" and the reason on the indented lines below it.
_CMAKE_HEADER = re.compile(r"^CMake Error\b.*:$")
CMAKE_DETAIL_LINES = 3
_RUST_ERROR = re.compile(r"^error(?:\[E\d+\])?:\s*.*$")
_RUST_LOCATION = re.compile(r"^\s*-->\s+(?P<path>.+?):(?P<line>\d{1,9}):\d+\s*$")
_GENERAL = re.compile(
    r"^(?:.*?:\s*(?:fatal )?error:|CMake Error|configure: error:|"
    r"(?:dh_\w+|dpkg-\w+): error:|ERROR:|Error:|Fatal Error:|FAILED:|FAIL:|E: Build killed)", re.IGNORECASE
)
# gfortran prints the location on its own line, then a blank line, the quoted
# source, a marker line, and only then "Fatal Error: ..." or "Error: ...".
_FORTRAN_LOCATION = re.compile(
    r"^(?P<path>\S+\.(?:f|for|ftn|f77|f90|f95|f03|f08|fpp)):(?P<line>\d{1,9})(?::[\d-]+)?:\s*$",
    re.IGNORECASE)
_FORTRAN_ERROR = re.compile(r"^(?:Fatal )?Error: .+")
FORTRAN_CONTEXT_LINES = 6
_MISSING_MODULE = re.compile(r"Cannot open module file '(?P<name>[\w]+)\.mod'", re.IGNORECASE)
_MISSING_HEADER = re.compile(r"fatal error: (?P<name>[\w./+-]+\.(?:h|hh|hpp|hxx|inc)): No such file")
# A compiler or linker invocation, or a build tool's progress line naming what it builds.
_COMMAND_BYTES = re.compile(
    rb"gcc|g\+\+|cc |c\+\+|clang|gfortran|f9[05]|mpif|mpic|libtool: |\] (?:Building|Linking)|LD |ld |"
    rb"\b(?:CC|CXX|FC|F77|CCLD|CXXLD|FCLD|F77LD|AR) ")
_COMMAND_FORMS = (
    r"(?:\+\s+)?(?:cd\s+\S+\s+&&\s+)?(?:/\S*/)?(?:[\w.+]+-)*"
    r"(?:gcc|g\+\+|cc|c\+\+|clang\+\+|clang|gfortran|f95|mpif90|mpif77|mpicc|mpicxx|"
    r"ld(?:\.bfd|\.gold|\.lld)?)(?:-\d+(?:\.\d+)*)?\s+\S"
    r"|libtool: (?:compile|link): "
    r"|\[\s*\d+%\] (?:Building|Linking) "
    r"|(?:CC|CXX|FC|F77|CCLD|CXXLD|FCLD|F77LD|AR|LD)\s+\S+\s*$")
_COMMAND = re.compile(r"^(?:" + _COMMAND_FORMS + r")")
# The same, on raw bytes with the validator's elapsed-time prefix, so the many
# command lines of a large log need not be decoded until an error needs one.
_COMMAND_RAW = re.compile(rb"^(?:\[\s*\d+s\] ?)?\s*(?:" + _COMMAND_FORMS.encode() + rb")")
COMMAND_CHARS = 600
# The package build itself starts here; errors before it come from preparing
# the build environment (the RPM hello log has "ERROR: not all operations were
# successful." from package scripts while the build root is set up).
_PACKAGE_BUILD_START = re.compile(r"^(?:dpkg-buildpackage: info: source package\b|\+? ?Executing\(%(?:mkbuilddir|prep)\))")
_SOURCE_SUFFIXES = (".f", ".for", ".ftn", ".f77", ".f90", ".f95", ".f03", ".f08", ".fpp")
MODULE_SEARCH_FILES = 400
MODULE_SEARCH_BYTES = 256_000
# Build roots that compilers print as absolute paths: OBS, sbuild (and its
# <<PKGBUILDDIR>>/<<BUILDDIR>> placeholders) and rpmbuild (4.20+ adds a level).
# The stripped path is used only if it then resolves to a file in the package.
_BUILD_ROOTS = tuple(re.compile(p) for p in (
    r"^/usr/src/packages/BUILD/",
    r"^/<<PKGBUILDDIR>>/",
    r"^/<<BUILDDIR>>/[^/]+/",
    r"^/build/[^/]+/[^/]+/",
    r"^/build/[^/]+/",
    r"^/home/abuild/rpmbuild/BUILD/[^/]+/[^/]+/",
    r"^/home/abuild/rpmbuild/BUILD/[^/]+/",
))


RUST_DETAIL_CHARS = 8000
_RUST_DETAIL_CUT = "\n[Compiler details truncated.]"


def _append_rust_detail(item: Diagnostic, text: str) -> None:
    """Preserve compiler continuation lines, with an explicit size safeguard."""
    if item.compiler_details.endswith(_RUST_DETAIL_CUT):
        return
    line = text.rstrip()
    if not line.strip():
        return
    combined = (
        item.compiler_details + "\n" + line
        if item.compiler_details else line
    )
    if len(combined) > RUST_DETAIL_CHARS:
        combined = (
            combined[:RUST_DETAIL_CHARS - len(_RUST_DETAIL_CUT)]
            + _RUST_DETAIL_CUT
        )
    item.compiler_details = combined


def _append_detail(item: Diagnostic, text: str) -> None:
    """Add a line of Part A context to compiler_details, within the same bound."""
    _append_rust_detail(item, text)


def _command_text(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= COMMAND_CHARS else text[:COMMAND_CHARS - 1] + "\u2026"


def _module_definitions(name: str, index: dict[str, list[str]],
                        worktree: Path) -> tuple[list[str], int, bool]:
    """(files defining `module name`, files searched, search stopped early).

    Bounded: at most MODULE_SEARCH_FILES Fortran files, MODULE_SEARCH_BYTES each.
    The pattern matches spaces and tabs only, not newlines, so it stays on one
    line and cannot take quadratic time on long runs of blank lines.
    """
    pattern = re.compile(rb"^[ \t]*module[ \t]+" + re.escape(name.encode())
                         + rb"[ \t]*(?:!.*)?\r?$", re.IGNORECASE | re.MULTILINE)
    found, checked = [], 0
    for paths in index.values():
        for relative in paths:
            if not relative.lower().endswith(_SOURCE_SUFFIXES):
                continue
            if checked >= MODULE_SEARCH_FILES:
                return sorted(found), checked, True
            checked += 1
            try:
                with (Path(worktree) / relative).open("rb") as handle:
                    data = handle.read(MODULE_SEARCH_BYTES)
            except OSError:
                continue
            if pattern.search(data):
                found.append(relative)
    return sorted(found), checked, False


def _producer_context(item: Diagnostic, index: dict[str, list[str]], worktree: Path,
                      modules: dict) -> None:
    """Say whether what the compiler could not find is part of this package.

    `modules` caches module searches for one diagnose() call.
    """
    module = _MISSING_MODULE.search(item.message)
    if module:
        name = module.group("name").lower()
        if name not in modules:
            modules[name] = _module_definitions(name, index, worktree)
        defined, searched, stopped = modules[name]
        if defined:
            _append_detail(item, f"Fortran module '{name}' is defined in this package: "
                           + ", ".join(defined[:5]))
        elif stopped:
            _append_detail(item, f"Module '{name}' was not found in the first {searched} Fortran "
                           "files under input/ (search limit reached).")
        else:
            _append_detail(item, f"None of the {searched} Fortran files under input/ defines module "
                           f"'{name}': it is expected from a build dependency (an installed .mod "
                           "file) or generated during the build.")
        return
    header = _MISSING_HEADER.search(item.message)
    if header:
        name = header.group("name")
        matches = _resolve(name, index)
        if matches:
            _append_detail(item, f"A file matching '{name}' exists in this package: "
                           + ", ".join(matches[:5]))
        else:
            _append_detail(item, f"No file named '{name}' exists under input/: it is expected "
                           "from the compiler, the system or a build dependency, or generated "
                           "during the build.")


def _file_index(worktree: Path) -> dict[str, list[str]]:
    """Index source files under input, excluding root quilt backups and symlinks."""
    root = Path(worktree) / "input"
    if root.is_symlink() or not root.is_dir():
        return {}
    index: dict[str, list[str]] = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs
            if not (Path(directory) / d).is_symlink()
            and not (Path(directory) == root and d == ".pc")
        )
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = "input/" + path.relative_to(root).as_posix()
            index.setdefault(name, []).append(relative)
    return index


def _resolve(printed: str, index: dict[str, list[str]]) -> list[str]:
    path = printed.replace("\\", "/")
    # Compilers often print the absolute path inside the build root, which is the
    # package's top directory: /usr/src/packages/BUILD/src/x.c is input/src/x.c.
    for root in _BUILD_ROOTS:
        if root.match(path):
            found = _resolve_relative(root.sub("", path, count=1), index)
            if found:
                return found
    return _resolve_relative(path, index)


def _resolve_relative(path: str, index: dict[str, list[str]]) -> list[str]:
    # Leading ../ is common in out-of-tree builds. Interior escapes are rejected.
    while path.startswith("./") or path.startswith("../"):
        path = path.partition("/")[2]
    parts = PurePosixPath(path).parts
    if not parts or ".." in parts:
        return []
    # Require the full suffix: guessing by basename could select the wrong file.
    return sorted(p for p in index.get(parts[-1], [])
                  if p == path or p.endswith("/" + path))


def diagnose(log_path: Path, worktree: Path) -> list[Diagnostic]:
    """Return unique errors, earliest first, without loading the whole log.

    Memory scales with unique diagnostics, source paths and the longest line.
    A marker-free excerpt has unknown stage: compiler errors can also be probes
    during configuration. Missing/unreadable logs give no diagnostics.
    """
    result: list[Diagnostic] = []
    seen: set[str] = set()
    index: dict[str, list[str]] | None = None
    stage = "unknown"
    pending: Diagnostic | None = None
    rust_detail: Diagnostic | None = None
    candidate: str | None = None
    quote_remaining = 0
    cmake: Diagnostic | None = None
    cmake_lines = 0
    fortran: list | None = None   # [printed path, line, offset, quoted source] awaiting its error
    fortran_remaining = 0
    last_command: str | bytes = ""
    build_start: int | None = None
    modules: dict = {}
    offset = 0

    def add_context(item: Diagnostic, compiled: bool) -> None:
        nonlocal index
        if last_command:
            command = last_command
            if isinstance(command, bytes):
                command = _ANSI.sub("", command.decode("utf-8", errors="replace").rstrip("\r\n"))
                command = _command_text(_ELAPSED.sub("", command).strip())
            _append_detail(item, "Last compile or link step in the log before this error "
                           "(parallel builds can interleave jobs): " + command)
        if compiled:
            if index is None:
                index = _file_index(worktree)
            _producer_context(item, index, worktree, modules)

    try:
        with Path(log_path).open("rb") as stream:
            for raw in stream:
                start = offset
                offset += len(raw)
                if (pending is None and cmake is None and rust_detail is None
                        and fortran is None and not _RELEVANT.search(raw)):
                    if _COMMAND_BYTES.search(raw) and _COMMAND_RAW.match(raw):
                        last_command = raw   # decoded only if an error needs it
                    continue
                text = _ANSI.sub("", raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                text = _ELAPSED.sub("", text)
                if fortran is not None:
                    stripped = text.strip()
                    if _FORTRAN_ERROR.match(stripped):
                        printed, line_number, location_offset, quoted = fortran
                        fortran = None
                        if stripped not in seen:
                            seen.add(stripped)
                            if index is None:
                                index = _file_index(worktree)
                            item = Diagnostic(stripped, stage, printed, line_number,
                                              _resolve(printed, index), quoted, location_offset)
                            result.append(item)
                            add_context(item, True)
                        continue
                    quote = _QUOTE.match(text)
                    if quote and int(quote.group(1)) == fortran[1]:
                        fortran[3] = quote.group(2)
                    fortran_remaining -= 1
                    if fortran_remaining > 0 and (not stripped or quote or re.match(r"^\s*\|", text)):
                        continue
                    fortran = None   # not a gfortran error after all: process the line normally
                if rust_detail is not None:
                    continuation = (
                        not text.strip()
                        or text[:1].isspace()
                        or _QUOTE.match(text) is not None
                        or text.startswith(("note:", "help:"))
                    )
                    if continuation:
                        _append_rust_detail(rust_detail, text)
                        location = _RUST_LOCATION.match(text)
                        if location and rust_detail.line is None:
                            printed = location.group("path")
                            if index is None:
                                index = _file_index(worktree)
                            rust_detail.log_path = printed
                            rust_detail.line = int(location.group("line"))
                            rust_detail.resolved = _resolve(printed, index)
                        quote = _QUOTE.match(text)
                        if (
                            quote
                            and rust_detail.source_line is None
                            and int(quote.group(1)) == rust_detail.line
                        ):
                            rust_detail.source_line = quote.group(2)
                        # Compiler quotes and notes are evidence, not new
                        # diagnostics or build-stage markers.
                        continue
                    rust_detail = None
                if cmake is not None:
                    # Indented lines and blank lines belong to the CMake error.
                    if not text.strip() or text[:1].isspace():
                        if text.strip():
                            cmake.message += " " + " ".join(text.split())
                            cmake_lines += 1
                        if cmake_lines >= CMAKE_DETAIL_LINES:
                            cmake = None
                        continue
                    cmake = None
                # Source excerpts are evidence, never new diagnostics or stage markers.
                quote = _QUOTE.match(text)
                if quote:
                    if pending is not None:
                        if int(quote.group(1)) == pending.line:
                            pending.source_line = quote.group(2)
                            pending = None
                        else:
                            quote_remaining -= 1
                            if quote_remaining <= 0:
                                pending = None
                    continue
                stripped = text.strip()
                location = _FORTRAN_LOCATION.match(stripped)
                if location:
                    fortran = [location.group("path"), int(location.group("line")), start, None]
                    fortran_remaining = FORTRAN_CONTEXT_LINES
                    continue
                if _COMMAND.match(stripped):
                    last_command = _command_text(stripped)
                    continue
                if build_start is None and _PACKAGE_BUILD_START.match(stripped):
                    build_start = start
                    # Errors before this are dropped below; a repeat inside the
                    # package build must not be deduplicated against them.
                    seen.clear()
                    # Nor is a command line from setup context for a build error
                    # (libmceliece's was a list of packages being installed).
                    last_command = ""
                # Rust prints the file location after its error header.
                location = _RUST_LOCATION.match(text) if pending is not None and pending.line is None else None
                if location:
                    printed = location.group("path")
                    if index is None:
                        index = _file_index(worktree)
                    pending.log_path = printed
                    pending.line = int(location.group("line"))
                    pending.resolved = _resolve(printed, index)
                    quote_remaining = 4
                    continue
                marker = _STAGE.search(text)
                if marker:
                    stage = marker.group(1)
                elif re.match(r"^\s*(?:dpkg-(?:source|gencontrol|genchanges|gensymbols|deb)\b|dh_builddeb\b)", text):
                    stage = "packaging"
                # Rust warnings may contain names such as winnow::error::ErrorKind.
                # Those names do not make the warning an error.
                if text.lstrip().startswith("warning:"):
                    pending = None
                    candidate = None
                    continue
                match = _COMPILER.match(text.strip())
                rust = _RUST_ERROR.match(text.strip())
                message = match.group("message") if match else (rust.group(0) if rust else None)
                if message is None and ("undefined reference" in text or _MAKE.match(text.strip())
                                        or _GENERAL.match(text.strip())
                                        or _LINKER.match(text.strip())):
                    message = text.strip()
                if message is not None:
                    pending = None
                    candidate = None
                    coded_rust = bool(re.match(r"^error\[E\d+\]:", message))
                    if not coded_rust:
                        if message in seen:
                            continue
                        seen.add(message)
                    printed = match.group("path") if match else None
                    if printed is not None and index is None:
                        index = _file_index(worktree)
                    item = Diagnostic(message, stage, printed,
                                      int(match.group("line")) if match else None,
                                      _resolve(printed, index or {}) if printed else [],
                                      None, start)
                    result.append(item)
                    linker = bool(_LINKER.match(text.strip()) or "undefined reference" in text
                                  or text.strip().startswith("collect2:"))
                    if (match and message != "Failure") or linker:
                        add_context(item, bool(match))
                    if match and message != "Failure":
                        pending, quote_remaining = item, 3
                    elif coded_rust:
                        rust_detail = item
                    elif rust:
                        pending, quote_remaining = item, 6
                    elif _CMAKE_HEADER.match(message):
                        cmake, cmake_lines = item, 0
                elif pending is not None:
                    quote = _QUOTE.match(text)
                    if quote and int(quote.group(1)) == pending.line:
                        pending.source_line = quote.group(2)
                        pending = None
                    elif candidate is not None and re.match(r"^\s*\^", text):
                        pending.source_line = candidate
                        pending = None
                    elif text.strip() and not quote and not re.match(r"^\s*[|~^]", text):
                        candidate = text
                    quote_remaining -= 1
                    if quote_remaining <= 0:
                        pending = None
    except OSError:
        # Preserve evidence already read if a stream becomes unavailable.
        pass
    # Rust locations follow their headline. Deduplicate after collecting
    # the locations, preserving identical headlines at different locations.
    unique = []
    keys = set()
    for item in result:
        if re.match(r"^error\[E\d+\]:", item.message):
            key = (item.message, item.log_path, item.line)
        else:
            key = ("offset", item.log_offset)
        if key not in keys:
            keys.add(key)
            unique.append(item)
    if build_start is not None:
        # Environment preparation succeeded, so its messages are not the failure.
        unique = [item for item in unique if item.log_offset >= build_start]
    return unique
