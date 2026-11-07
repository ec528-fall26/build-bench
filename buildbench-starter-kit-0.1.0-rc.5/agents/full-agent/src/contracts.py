"""Shared contracts between the full agent's parts (agreed by the team, 7 October).

Every part builds against these types so the five parts can be developed in
parallel. Change them only by team agreement: Part C depends on all of them.
See docs/full-agent.md, "Shared contracts" and "Model reply format".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .types import Edit


@dataclass
class Diagnostic:                 # Part A produces, everyone reads
    message: str                  # "fatal error: xmmintrin.h: No such file or directory"
    stage: str                    # configure | build | test | install | packaging | unknown
    log_path: str | None          # path as the log printed it: "crc32c/crc32c_prefetch.h"
    line: int | None
    resolved: list[str]           # matching real files: ["input/src/crc32c/crc32c_prefetch.h"]
    source_line: str | None       # the line the compiler quoted, if any
    log_offset: int               # where in the log it came from (provenance)
    compiler_details: str = ""    # bounded continuation of a compiler error


@dataclass
class ToolRequest:                # model -> Part B
    tool: str                     # list_dir | find_files | search | read_file | current_diff | patch_series | read_log | search_log
    args: dict


@dataclass
class ToolResult:                 # Part B -> model
    request: ToolRequest
    text: str                     # labelled, size-capped, never raises
    truncated: bool


@dataclass
class ModelTurn:                  # one model reply, parsed (see "Model reply format")
    action: str                   # tool | edit | approve (pending review only) | stop | malformed
    tool_requests: list[ToolRequest]   # action == "tool"; several allowed per turn
    hypothesis: str               # action == "edit"
    evidence: list[str]           # action == "edit": log lines / file:line it rests on
    edits: list[Edit]             # action == "edit"
    reason: str                   # stop reason, or why the reply was malformed


@dataclass
class BuildResult:                # Part D -> Part C
    succeeded: bool
    stage_reached: str            # furthest build stage, used to rank attempts
    diagnostics: list[Diagnostic] # Part A applied to the new build log
    duration_seconds: float


@dataclass
class Attempt:                    # Part C keeps these: the repair history
    number: int
    hypothesis: str               # falsifiable: "the x86 header is included unconditionally"
    edits: list[Edit]             # Part 4's Edit, unchanged
    applied: int
    build: BuildResult | None     # None if no build was run
    tokens: dict                  # {"input_tokens": int | None, "output_tokens": int | None}


@dataclass
class Budget:                     # research settings, calibrated on rcran; not competition limits
    # Without builds an attempt is one edit batch; five ended rust-subversion
    # with ten of its seventeen minutes unused.
    max_attempts: int = 8
    max_builds: int = 3
    # Prompt and completion tokens summed over every call. The website sets no
    # token limit for Hosted Smoke (time is the limit), and each turn resends
    # the conversation, so 100_000 ended runs after about six turns.
    max_tokens: int = 3_000_000
    max_seconds: int = 1_800
    reserve_seconds: int = 120    # kept back for a graceful stop


@dataclass
class AgentConfig:                # the ablation switches
    use_evidence_manager: bool = True
    use_tools: bool = True
    use_build_feedback: bool = True
    use_history: bool = True
    # 0.5.0, effective only without build feedback (a submitted run): an
    # investigation budget per run instead of tools switched off 6 turns after
    # each edit, the model's own untested edits shown as a diff, at most 3
    # attempts, and one refusal of a stop with nothing submitted.
    use_no_build_control: bool = True
    # 0.5.0: refuse edits that bypass tests (rules.html section 6) or edit .pc/
    # or debian/patches/ (rules_guard.py).
    use_rules_guard: bool = True
    # 0.5.1: send back an edit that leaves its file unparseable (unbalanced
    # braces or #if, Python/shell/JSON syntax, make recipe without a TAB,
    # compiler -fsyntax-only when a compiler exists) before it is written
    # (syntax_guard.py).
    use_syntax_guard: bool = True


@runtime_checkable
class BuildFeedback(Protocol):    # Part D implements; Part C calls
    def request_build(self, worktree: Path) -> BuildResult | None: ...  # None = unavailable
