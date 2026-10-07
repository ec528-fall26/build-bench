"""Part A stand-in: Case and Evidence Manager.

Placeholder so Part C can be written against the real signature. It returns
the log's last non-blank line as a single unresolved diagnostic. Part A
replaces the body; the signature is the contract (docs/full-agent.md, Part A).
"""

from __future__ import annotations

from pathlib import Path

from .contracts import Diagnostic
from .log_tail import extract_tail


def diagnose(log_path: Path, worktree: Path) -> list[Diagnostic]:
    """Return the diagnostics found in a build log or excerpt, earliest first."""
    try:
        tail = extract_tail(Path(log_path), 50)
    except (OSError, ValueError):
        return []
    lines = [line for line in tail.splitlines() if line.strip()]
    if not lines:
        return []
    return [Diagnostic(message=lines[-1].strip(), stage="unknown", log_path=None,
                       line=None, resolved=[], source_line=None, log_offset=-1)]
