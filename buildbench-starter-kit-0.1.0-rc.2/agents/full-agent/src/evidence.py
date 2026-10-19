"""Streaming Part A evidence. log_offset is a zero-based byte offset in the
supplied log or excerpt. Source quotes come from the log, not edited files.
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
    r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?:\s*(?P<message>(?:fatal )?error:.*|Failure)$"
)
# Most of a large log is ordinary output. Filter bytes before decoding or running
# the richer parsers, while always retaining the lines following a diagnostic.
_RELEVANT = re.compile(rb"error|Error|ERROR|Failure|FAIL|Stop\.|undefined reference|dh_auto_|dpkg-|dh_builddeb|E: Build killed|ld: ")
_STAGE = re.compile(r"\b(?:dh_auto_|override_dh_auto_)(configure|build|test|install)\b")
_MAKE = re.compile(r"^(?:g?make)(?:\[\d+\])?:\s*\*\*\*.*(?:Error|Stop\.)")
_QUOTE = re.compile(r"^\s*(\d+)\s*\| ?(.*)$")
# The linker's own messages ("/usr/bin/ld: ..."), other than warnings. libmceliece
# failed on "/bin/ld: BFD ... assertion fail ...", with no "error" in the line.
_LINKER = re.compile(r"^(?:\S*/)?ld(?:\.\w+)?: (?!warning:)")
# CMake prints "CMake Error ...:" and the reason on the indented lines below it.
_CMAKE_HEADER = re.compile(r"^CMake Error\b.*:$")
CMAKE_DETAIL_LINES = 3
_RUST_ERROR = re.compile(r"^error(?:\[E\d+\])?:\s*.*$")
_RUST_LOCATION = re.compile(r"^\s*-->\s+(?P<path>.+?):(?P<line>\d+):\d+\s*$")
_GENERAL = re.compile(
    r"^(?:.*?:\s*(?:fatal )?error:|CMake Error|configure: error:|"
    r"(?:dh_\w+|dpkg-\w+): error:|ERROR:|Error:|FAILED:|FAIL:|E: Build killed)", re.IGNORECASE
)


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
    # Leading ../ is common in out-of-tree builds. Interior escapes are rejected.
    path = printed.replace("\\", "/")
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
    offset = 0
    try:
        with Path(log_path).open("rb") as stream:
            for raw in stream:
                start = offset
                offset += len(raw)
                if (pending is None and cmake is None and rust_detail is None
                        and not _RELEVANT.search(raw)):
                    continue
                text = _ANSI.sub("", raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                text = _ELAPSED.sub("", text)
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
    return unique
