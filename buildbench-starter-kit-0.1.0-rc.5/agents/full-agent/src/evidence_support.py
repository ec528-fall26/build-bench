"""Shared bounded evidence text and source-file indexing."""
from __future__ import annotations
import os
from pathlib import Path
from .contracts import Diagnostic
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
