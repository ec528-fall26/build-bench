from __future__ import annotations
from .edit_applier import apply_edits
from .log_tail import extract_tail
from .model_client import ModelClient
from .types import CaseContext, EditPlan
import json
import logging
import os
import sys
from pathlib import Path

def write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

def run_repair(
    context: CaseContext,
    log_path: Path,
    extract_tail,
    model_client,
    *,
    patch_policy=None,
) -> tuple[EditPlan, int]:
    context.log_tail = extract_tail(log_path, n_lines=500)

    plan = model_client.propose(context)

    applied_count = apply_edits(
        context.worktree,
        plan.edits,
        patch_policy=patch_policy,
    )

    return plan, applied_count

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

        required_paths = [
            input_dir / "task.json",
            input_dir / "initial-build.log",
        ]

        for path in required_paths:
            if not path.is_file():
                raise FileNotFoundError(
                    f"Required input file is missing: {path}"
                )

        if not worktree.is_dir():
            raise FileNotFoundError(
                f"Package worktree is missing: {worktree}"
            )

        task_metadata = json.loads(
            (input_dir / "task.json").read_text(encoding="utf-8")
        )
        if not isinstance(task_metadata, dict):
            raise ValueError("task.json must contain a JSON object")

        context = CaseContext(
            case_id=str(task_metadata.get("case_id", "")),
            worktree=worktree,
            log_tail="",
            task_metadata=task_metadata,
        )
        model = ModelClient.from_env()
        plan, applied = run_repair(
            context, input_dir / "initial-build.log", extract_tail, model,
        )

        # "completed" means the agent finished, even with no repair: the
        # platform's clean build decides whether anything was fixed.
        result = {
            "schema_version": "0.1",
            "status": "completed",
            "message": plan.rationale[:500] or "The model gave no rationale.",
            "model": model.name,
            "served_model": getattr(model, "served_model", None),
            "edits_proposed": len(plan.edits),
            "edits_applied": applied,
        }

        write_json(output_dir / "agent-result.json", result)
        print(result["message"])
        return 0

    except Exception as exc:
        diagnostic = {
            "status": "agent_error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(diagnostic), file=sys.stderr)

        try:
            write_json(output_dir / "agent-error.json", diagnostic)
        except OSError as output_error:
            print(
                f"Could not write error diagnostics: {output_error}",
                file=sys.stderr,
            )

        return 1



if __name__ == "__main__":
    raise SystemExit(main())