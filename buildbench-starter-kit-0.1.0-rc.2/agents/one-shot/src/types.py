from dataclasses import dataclass
from pathlib import Path


@dataclass
class CaseContext:
    case_id: str
    worktree: Path
    log_tail: str
    task_metadata: dict


@dataclass
class Edit:
    path: str
    old_text: str
    new_text: str


@dataclass
class EditPlan:
    edits: list[Edit]
    rationale: str
    usage: dict
