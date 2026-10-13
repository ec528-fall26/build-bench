from pathlib import Path 

def check_path(path : Path, worktree: Path) -> str | None:
    for candidate in (path, *path.parents):
        if candidate ==worktree:
            break
        
        if candidate.is_symlink():
            return "symlink"

    allowed_root = (worktree / "input").resolve()
    resolved_path = path.resolve()

    if not resolved_path.is_relative_to(allowed_root):
        return "outside input directory"

    return None