"""Read a validator result directory into one comparable outcome.

A run counts as repaired only when the validator reports status "succeeded"
and its artifact check passed. patch_applied is deliberately ignored: our
repair travels inside a rebuilt source package, so the validator's own patch
mechanism is never used and that field stays false even on success.

Usage: python3 -m harness.record RESULT_DIR [RESULT_DIR ...]
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys


RESULT_FILE = "build-result.json"


@dataclass(frozen=True)
class BuildOutcome:
    result_dir: str
    case_id: str | None
    status: str
    repaired: bool
    artifact_validation_passed: bool
    build_exit_code: int | None
    duration_seconds: float | None
    timed_out: bool
    binary_artifacts: tuple[str, ...]
    message: str


def _missing(result_dir: Path, status: str, message: str) -> BuildOutcome:
    return BuildOutcome(
        result_dir=str(result_dir), case_id=None, status=status, repaired=False,
        artifact_validation_passed=False, build_exit_code=None,
        duration_seconds=None, timed_out=False, binary_artifacts=(),
        message=message,
    )


def _int(value: object) -> int | None:
    # JSON true/false parse as bool, which is an int subclass.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def load_outcome(result_dir: Path) -> BuildOutcome:
    """Never raises for a bad run.

    A missing or unreadable result is itself an outcome: crashed and
    never-started runs stay in the evaluation denominator.
    """
    result_dir = Path(result_dir)
    path = result_dir / RESULT_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _missing(result_dir, "missing_result", f"{RESULT_FILE} not found")
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return _missing(result_dir, "invalid_result", f"{RESULT_FILE} unreadable: {error}")
    if not isinstance(data, dict):
        return _missing(result_dir, "invalid_result", f"{RESULT_FILE} is not a JSON object")

    status = data.get("status") if isinstance(data.get("status"), str) else "unknown"
    validated = data.get("artifact_validation_passed") is True
    artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), list) else []
    binaries = tuple(
        Path(item["path"]).name
        for item in artifacts
        if isinstance(item, dict)
        and item.get("kind") == "binary"
        and isinstance(item.get("path"), str)
    )
    duration = data.get("duration_seconds")
    return BuildOutcome(
        result_dir=str(result_dir),
        case_id=data.get("case_id") if isinstance(data.get("case_id"), str) else None,
        status=status,
        repaired=status == "succeeded" and validated,
        artifact_validation_passed=validated,
        build_exit_code=_int(data.get("build_exit_code")),
        duration_seconds=float(duration)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else None,
        timed_out=data.get("timed_out") is True,
        binary_artifacts=binaries,
        message=data.get("message") if isinstance(data.get("message"), str) else "",
    )


def append_jsonl(path: Path, row: dict) -> None:
    """Append one record as a single JSON line; earlier rows are never rewritten."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def outcome_row(outcome: BuildOutcome) -> dict:
    row = asdict(outcome)
    row["binary_artifacts"] = list(outcome.binary_artifacts)
    return row


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    for arg in argv:
        o = load_outcome(Path(arg))
        seconds = "-" if o.duration_seconds is None else f"{o.duration_seconds:.0f}s"
        verdict = "REPAIRED" if o.repaired else "not repaired"
        print(f"{Path(arg).name:<20} {o.status:<15} {verdict:<13} {seconds:>5}  {o.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
