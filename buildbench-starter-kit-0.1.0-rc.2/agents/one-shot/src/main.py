from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    # Use BB_WORKSPACE when set; otherwise use /workspace.
    workspace = Path(os.environ.get("BB_WORKSPACE", "/workspace"))
    input_dir = workspace / "input"
    worktree = workspace / "work" / "repo"
    output_dir = workspace / "output"

    output_dir.mkdir(parents=True, exist_ok=True)

    required_paths = [
        input_dir / "task.json",
        input_dir / "initial-build.log",
    ]

    for path in required_paths:
        if not path.is_file():
            raise FileNotFoundError(f"Required input file is missing: {path}")

    if not worktree.is_dir():
        raise FileNotFoundError(f"Package worktree is missing: {worktree}")

    result = {
        "schema_version": "0.1",
        "status": "completed",
        "message": "Agent shell completed. No repair was attempted.",
        "modified_paths": [],
    }

    result_path = output_dir / "agent-result.json"
    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(result["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())