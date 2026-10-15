"""The full agent's entry point: Part A's evidence, Part C's loop, Part E's prompts.

    python -m src.main     (the platform runs this; see agent.yaml)

run_agent() is the whole repair, shared with the local harness, which passes
its own build feedback (harness/build_feedback.py). Submitted, the agent has
none: the organizers' in-run build protocol is not published, so edits are
proposed, investigated and revised, then submitted untested.

"completed" in agent-result.json means the agent finished, even with no
repair: the platform's clean build decides whether anything was fixed.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import sys
from time import monotonic

from .case import load_case
from .config import config_from_env, config_record
from .contracts import AgentConfig, Budget, BuildFeedback
from .evidence import diagnose
from .log_tail import extract_tail
from .loop import LoopResult, run_loop
from .model_adapter import Complete, ModelAdapter, client_completion
from .model_client import ModelClient
from .types import CaseContext


TAIL_LINES = 500  # Part 2's tail: the evidence when Part A finds no error


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_agent(
    context: CaseContext, log_path: Path, complete: Complete, *,
    config: AgentConfig | None = None, budget: Budget | None = None,
    build_feedback: BuildFeedback | None = None, patch_policy=None,
    package: str | None = None, clock=monotonic,
) -> tuple[LoopResult, ModelAdapter]:
    """Diagnose the failure log, then run the repair loop until it stops."""
    config = config if config is not None else config_from_env()
    budget = budget or Budget()
    context.log_tail = extract_tail(Path(log_path), TAIL_LINES)
    diagnostics = diagnose(Path(log_path), context.worktree)
    adapter = ModelAdapter(complete, context, config=config, budget=budget,
                           diagnostics=diagnostics, log_path=Path(log_path),
                           package=package, clock=clock)
    try:
        result = run_loop(context, next_turn=adapter, config=config, budget=budget,
                          build_feedback=build_feedback, patch_policy=patch_policy,
                          clock=clock)
    finally:
        adapter.close()
    return result, adapter


def _build_summary(build) -> dict | None:
    if build is None:
        return None
    first = build.diagnostics[0] if build.diagnostics else None
    return {"succeeded": build.succeeded, "stage_reached": build.stage_reached,
            "diagnostic_count": len(build.diagnostics),
            "first_error": asdict(first) if first else None,
            "duration_seconds": build.duration_seconds}


def result_record(result: LoopResult, adapter: ModelAdapter, config: AgentConfig) -> dict:
    """What the run did, for agent-result.json and the harness run record."""
    return {
        "termination_reason": result.termination_reason,
        "termination_message": result.message,
        "config": config_record(config),
        "usage": result.usage,
        "known_usage": result.known_usage,
        "malformed_count": result.malformed_count,
        "kept_attempts": result.kept_attempts,
        "attempts": [{
            "number": a.number, "hypothesis": a.hypothesis,
            "edits": [e.path for e in a.edits], "applied": a.applied,
            "build": _build_summary(a.build), "tokens": a.tokens,
        } for a in result.attempts],
        "turns": adapter.turns,
    }


def main() -> int:
    # Use BB_WORKSPACE when set, otherwise use /workspace.
    workspace = Path(os.environ.get("BB_WORKSPACE", "/workspace"))
    input_dir = workspace / "input"
    worktree = workspace / "work" / "repo"
    output_dir = workspace / "output"

    # Part 4 logs each accepted and rejected edit; stderr is captured by the platform.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s %(message)s")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for path in (input_dir / "task.json", input_dir / "initial-build.log"):
            if not path.is_file():
                raise FileNotFoundError(f"Required input file is missing: {path}")
        if not worktree.is_dir():
            raise FileNotFoundError(f"Package worktree is missing: {worktree}")

        config = config_from_env()  # a mistyped switch raises: no silent wrong arm
        case = load_case(input_dir / "task.json", worktree=worktree,
                         log_path=input_dir / "initial-build.log")
        context = CaseContext(case_id=case.case_id or "", worktree=worktree, log_tail="",
                              task_metadata=case.task_metadata)
        policy = case.task_metadata.get("patch_policy")
        model = ModelClient.from_env()
        result = {"schema_version": "0.1", "status": "completed", "model": model.name,
                  "served_model": None}

        if not model.api_key:
            # Like the baseline: finish without a request and without edits.
            result["message"] = "No model API key configured (set BB_MODEL_API_KEY)."
        else:
            loop_result, adapter = run_agent(
                context, case.log_path, client_completion(model), config=config,
                patch_policy=policy if isinstance(policy, dict) else None,
                package=case.package)
            last = next((a.hypothesis for a in reversed(loop_result.attempts)
                         if a.hypothesis), "")
            result.update(result_record(loop_result, adapter, config))
            result["served_model"] = model.served_model
            result["message"] = (last or loop_result.message)[:500]

        write_json(output_dir / "agent-result.json", result)
        print(result["message"])
        return 0

    except Exception as exc:
        diagnostic = {"status": "agent_error", "error_type": type(exc).__name__,
                      "message": str(exc)}
        print(json.dumps(diagnostic), file=sys.stderr)
        try:
            write_json(output_dir / "agent-error.json", diagnostic)
        except OSError as output_error:
            print(f"Could not write error diagnostics: {output_error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
