"""Read-only case metadata normalization for Part A.

This additive API leaves CaseContext and the agreed cross-part contracts intact.
Callers may pass workspace paths explicitly when replaying container metadata on
a host; otherwise relative task paths are interpreted relative to task.json.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass
class CaseInfo:
    case_id: str | None
    package: str | None
    target_arch: str | None
    worktree: Path
    log_path: Path
    task_metadata: dict
    manifest_metadata: dict


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _string(*values) -> str | None:
    return next((v for v in values if isinstance(v, str) and v.strip()), None)


def _nested(data: dict, key: str, field: str):
    value = data.get(key)
    return value.get(field) if isinstance(value, dict) else None


def load_case(task_path: Path, *, manifest_path: Path | None = None,
              worktree: Path | None = None, log_path: Path | None = None) -> CaseInfo:
    """Load task.json and an optional local manifest without changing either.

    Task values take precedence. Missing optional fields remain None. Malformed
    JSON and missing explicitly requested files raise, rather than inventing a
    case identity. No path read from metadata is executed or written.
    """
    task_path = Path(task_path)
    task = _object(task_path)

    def path_value(explicit, key, fallback):
        if explicit is not None:
            return Path(explicit)
        value = _string(task.get(key))
        path = Path(value) if value else fallback
        return path if path.is_absolute() else task_path.parent / path

    tree = path_value(worktree, "worktree", Path("../work/repo"))
    log = path_value(log_path, "initial_build_log", Path("initial-build.log"))
    manifest = _object(Path(manifest_path)) if manifest_path is not None else {}
    build = manifest.get("build")
    build = build if isinstance(build, dict) else {}
    task_build = task.get("build")
    task_build = task_build if isinstance(task_build, dict) else {}
    package = _string(task.get("package"), _nested(task, "package", "name"), task.get("package_name"),
                      manifest.get("package"), _nested(manifest, "package", "name"), manifest.get("package_name"))
    # Debian source recipe filenames encode the package before the version.
    recipe = _string(build.get("recipe"))
    if package is None and recipe and recipe.endswith(".dsc"):
        name = recipe.replace("\\", "/").rsplit("/", 1)[-1]
        if "_" in name:
            package = name.split("_", 1)[0]
    return CaseInfo(
        _string(task.get("case_id"), manifest.get("case_id")), package,
        _string(task.get("target_arch"), task.get("target_architecture"),
                _nested(task, "platform", "target_arch"), task_build.get("architecture"),
                manifest.get("target_arch"), _nested(manifest, "platform", "target_arch"), build.get("architecture")),
        tree, log, task, manifest,
    )
