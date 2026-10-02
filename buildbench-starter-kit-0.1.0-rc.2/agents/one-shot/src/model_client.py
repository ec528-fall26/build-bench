from .types import CaseContext, Edit, EditPlan
import json
from pathlib import Path

def check_old_text(file_text: str, edit: Edit) -> None:
    if not edit.old_text:
        raise ValueError("old_text must not be empty.")

    matches = file_text.count(edit.old_text)

    if matches != 1:
        raise ValueError(f"Expected old_text to match once, but found {matches} matches")


def locate_edit_file(worktree: Path, edit: Edit) -> Path:
    relative_path = Path(edit.path)

    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("Edit path must stay inside the worktree.")

    if not relative_path.parts or relative_path.parts[0] != "input":
        raise ValueError("Edit path must be under input/.")

    root = worktree.resolve()
    target = root

    for part in relative_path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("Edit paths must not contain symlinks.")

    target = target.resolve()

    if not target.is_relative_to(root):
        raise ValueError("Edit path escapes the worktree.")

    if not target.is_file():
        raise ValueError("The file to edit does not exist.")

    return target


def parse_response(data: dict) -> EditPlan:
    try: 
        if not isinstance(data["edits"], list):
            raise ValueError("'edits' must be a list.")

        for item in data["edits"]:
            if not isinstance(item, dict):
                raise ValueError("Each edit must be an object.")

            for field in ("path", "old_text", "new_text"):
                if not isinstance(item[field], str):
                    raise ValueError(f"'{field}' must be a string.")

            if not item["path"] or not item["old_text"]:
                raise ValueError("'path' and 'old_text' must not be empty.")

        if not isinstance(data.get("rationale", ""), str):
            raise ValueError("'rationale' must be a string.")

        edits = [
            Edit(
                path = item["path"],
                old_text = item["old_text"],
                new_text = item["new_text"],
            )
            for item in data["edits"]
    ]

        return EditPlan(
            edits = edits,
            rationale = data.get("rationale", ""),
            usage= {"input_tokens": None, "output_tokens": None},
        )

    except (KeyError, TypeError, ValueError) as exc:
        return EditPlan(
            edits = [],
            rationale = f"Malformed model response: {exc}",
            usage= {"input_tokens": None, "output_tokens": None},
    )


class ModelClient:
    def __init__(self, response_path: Path):
        self.response_path = response_path

    def propose(self, context: CaseContext) -> EditPlan:
        try: 
            data = json.loads(
                self.response_path.read_text(encoding= "utf-8")
            )

            if not isinstance(data, dict):
                raise ValueError("Response must be a JSON object.")

            if data.get("case_id") != context.case_id:
                raise ValueError("Saved response belongs to a different case.")

            plan= parse_response(data)

            for edit in plan.edits:
                file_path = locate_edit_file(context.worktree, edit)

                max_bytes = 8*1024 * 1024
                with file_path.open("rb") as source:
                    file_bytes = source.read(max_bytes +1)

                if len(file_bytes) > max_bytes:
                    raise ValueError("File exceedes the 8 Mib limit.")

                file_text = file_bytes.decode("utf-8")
                check_old_text(file_text, edit)

            return plan
        
        except (OSError, ValueError) as exc:
            return EditPlan(
                edits =[],
                rationale= f"Could not load saved response: {exc}",
                usage= {"input_tokens": None, "output_tokens": None},
            )

