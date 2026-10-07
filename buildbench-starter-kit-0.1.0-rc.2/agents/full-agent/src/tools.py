"""Part B stand-in: read-only tools the model can request.

Placeholder so Part C can be written against the real signature. Every tool
answers "unavailable" and nothing is read. Part B replaces the body; the
contract is that a tool never writes and never raises (docs/full-agent.md, Part B).
"""

from __future__ import annotations

from pathlib import Path

from .contracts import ToolRequest, ToolResult


TOOLS = ("list_dir", "find_files", "search", "read_file", "current_diff", "patch_series")


def run_tool(request: ToolRequest, worktree: Path) -> ToolResult:
    """Run one tool request against the worktree; failures come back as text."""
    if request.tool not in TOOLS:
        text = f"=== {request.tool}: unavailable (unknown tool) ==="
    else:
        text = f"=== {request.tool}: unavailable (not implemented yet) ==="
    return ToolResult(request=request, text=text, truncated=False)
