"""The full agent's entry point: Part A's evidence, Part C's loop, Part E's prompts.

    python -m src.main     (the platform runs this; see agent.yaml)

run_agent() is the whole repair, shared with the local harness, which passes
its own build feedback (harness/build_feedback.py). Submitted, the agent has
none: the competition website says agents cannot request builds during a run,
so edits are proposed, investigated and revised, then submitted untested.

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
from .failure import FailureStage, classify_failure
from .log_tail import extract_tail
from .loop import LoopResult, run_loop
from .model_adapter import Complete, ModelAdapter, client_completion
from .model_client import ModelClient, ModelConfigError
from .types import CaseContext


TAIL_LINES = 500  # Part 2's tail: the evidence when Part A finds no error
# Hosted Smoke allows 20 minutes from startup, model calls included; official
# limits are not yet announced. Stop well inside it (the loop also keeps
# Budget.reserve_seconds back), and no request may run past this point.
HOSTED_SECONDS = 1_020


def _failure_stage(log_path: Path) -> FailureStage:
    """Part A's classification, which must never stop the repair."""
    try:
        return classify_failure(log_path)
    except Exception as error:  # noqa: BLE001 - a record field, not worth a failed run
        return FailureStage(evidence=[f"classification failed: {type(error).__name__}"])


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
    if os.environ.get("BB_REPAIR_STRATEGY") == "small-group" and build_feedback is None:
        raise ValueError("small-group requires an actual build-feedback adapter")
    review_setting = os.environ.get("BB_REVIEW_EDITS", "0")
    if review_setting not in {"0", "1"}:
        raise ValueError("BB_REVIEW_EDITS must be 0 or 1")
    context.log_tail = extract_tail(Path(log_path), TAIL_LINES)
    diagnostics = diagnose(Path(log_path), context.worktree)
    adapter = ModelAdapter(complete, context, config=config, budget=budget,
                           diagnostics=diagnostics, log_path=Path(log_path),
                           package=package, clock=clock)
    try:
        result = run_loop(context, next_turn=adapter, config=config, budget=budget,
                          build_feedback=build_feedback, patch_policy=patch_policy,
                          clock=clock, review_edits=review_setting == "1",
                          initial_diagnostics=diagnostics)
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
        "repair_strategy": adapter.repair_strategy,
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
        "review_events": result.review_events,
    }


def _failed(output_dir: Path, stage: str, exc: Exception, message: str = "") -> int:
    """Record a run the model could not take part in, without secrets, and exit 1."""
    result = {"schema_version": "0.1", "status": "no_fix", "failure_stage": stage,
              "error_type": type(exc).__name__, "message": (message or str(exc))[:500]}
    write_json(output_dir / "agent-result.json", result)
    print(json.dumps(result), file=sys.stderr)
    return 1


def main() -> int:
    started = monotonic()
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
        try:
            model = ModelClient.from_env()
        except ModelConfigError as exc:
            # The guide: log the stage and error type, exit nonzero.
            return _failed(output_dir, "model_initialization", exc)
        result = {"schema_version": "0.1", "status": "completed", "model": model.name,
                  "served_model": None,
                  # Part A: Debian, RPM or unknown, and the stage the original
                  # build failed at (setup failures cannot be fixed by edits).
                  "package_format": asdict(case.package_format),
                  "failure_stage": asdict(_failure_stage(case.log_path))}
        budget = None
        if model.hosted:
            budget = Budget(max_seconds=HOSTED_SECONDS)
            model.deadline = started + HOSTED_SECONDS

        if not model.api_key:
            # Like the baseline: finish without a request and without edits.
            result["message"] = "No model API key configured (set BB_MODEL_API_KEY)."
        else:
            loop_result, adapter = run_agent(
                context, case.log_path, client_completion(model), config=config,
                budget=budget, patch_policy=policy if isinstance(policy, dict) else None,
                package=case.package)
            if model.hosted and model.successful_calls == 0:
                # No answer from the platform's model: not a completed repair attempt.
                return _failed(output_dir, "model_repair",
                               RuntimeError(loop_result.message), loop_result.message)
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
