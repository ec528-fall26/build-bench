"""Streaming Part A evidence. log_offset is a zero-based byte offset in the
supplied log or excerpt. Source quotes come from the log, not edited files.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re

from .contracts import Diagnostic

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_COMPILER = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?:\s*(?P<message>(?:fatal )?error:.*|Failure)$"
)
# Most of a large log is ordinary output. Filter bytes before decoding or running
# the richer parsers, while always retaining the lines following a diagnostic.
_RELEVANT = re.compile(rb"error|Error|ERROR|Failure|FAIL|Stop\.|undefined reference|dh_auto_|dpkg-|dh_builddeb|E: Build killed")
_STAGE = re.compile(r"\b(?:dh_auto_|override_dh_auto_)(configure|build|test|install)\b")
_MAKE = re.compile(r"^(?:g?make)(?:\[\d+\])?:\s*\*\*\*.*(?:Error|Stop\.)")
_QUOTE = re.compile(r"^\s*(\d+)\s*\| ?(.*)$")
_GENERAL = re.compile(
    r"^(?:.*?:\s*(?:fatal )?error:|CMake Error|configure: error:|"
    r"(?:dh_\w+|dpkg-\w+): error:|ERROR:|Error:|FAILED:|FAIL:|E: Build killed)", re.IGNORECASE
)


def _file_index(worktree: Path) -> dict[str, list[str]]:
    """Index real files under input only; never traverse symlinks."""
    root = Path(worktree) / "input"
    if root.is_symlink() or not root.is_dir():
        return {}
    index: dict[str, list[str]] = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
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
    candidate: str | None = None
    quote_remaining = 0
    offset = 0
    try:
        with Path(log_path).open("rb") as stream:
            for raw in stream:
                start = offset
                offset += len(raw)
                if pending is None and not _RELEVANT.search(raw):
                    continue
                text = _ANSI.sub("", raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                marker = _STAGE.search(text)
                if marker:
                    stage = marker.group(1)
                elif re.match(r"^\s*(?:dpkg-(?:source|gencontrol|genchanges|gensymbols|deb)\b|dh_builddeb\b)", text):
                    stage = "packaging"
                match = _COMPILER.match(text.strip())
                message = match.group("message") if match else None
                if message is None and ("undefined reference" in text or _MAKE.match(text.strip())
                                        or _GENERAL.match(text.strip())):
                    message = text.strip()
                if message is not None:
                    pending = None
                    candidate = None
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
    return result
