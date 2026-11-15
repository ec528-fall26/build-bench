"""Part D, agent side: build feedback inside the submitted agent.

The competition's in-run build protocol is not published yet, so the agent
has no build feedback of its own: request_build returns None and Part C must
still finish (investigate and revise, then submit untested).

The local implementation, which repacks a copy of the worktree and runs the
validator, lives in the harness and is never part of the agent ZIP.
"""

from __future__ import annotations

from pathlib import Path

from .contracts import BuildResult


class NoBuildFeedback:
    def request_build(self, worktree: Path) -> BuildResult | None:
        return None
