from pathlib import Path
from difflib import unified_diff
from .path_checks import check_path

MAX_DIFF_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_TRACKED_FILES = 256

class DiffTracker:
    def __init__(self, worktree:Path):
        self.worktree = worktree
        self.originals = {}
        self.saved_bytes = 0

    def capture_before_edit(self, name: str):
        if name in self.originals:
            return 

        if len(self.originals) >= MAX_TRACKED_FILES:
            raise ValueError("tracked file limit reached")

        original = self._read_text(name)
        size = len(original.encode("utf-8"))

        if self.saved_bytes + size > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot byte limit reached")

        self.originals[name] = original
        self.saved_bytes += size

    def current_diff(self, max_chars: int = 4000) -> tuple[str, bool]:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        parts = []
        used_chars = 0

        for name, original in sorted(self.originals.items()):
            current = self._read_text(name)

            patch = unified_diff(
                original.splitlines(keepends = True),
                current.splitlines(keepends = True),
                fromfile = f"a/{name}",
                tofile = f"b/{name}",
            )

            for line in patch:
                if not line.endswith("\n"):
                    line += "\n\\ No newline at the end of file\n"

                remaining = max_chars - used_chars

                if len(line) > remaining:
                    parts.append(line[:remaining])
                    return "".join(parts), True

                parts.append(line)
                used_chars += len(line)

        return "".join(parts), False

    def _read_text(self, name:str) -> str:
        path = self.worktree / name
        problem = check_path(path, self.worktree)

        if problem is not None: 
            raise ValueError(problem)

        if not path.is_file():
            raise ValueError("not a regular file")

        with path.open("rb") as file:
            data = file.read(MAX_DIFF_FILE_BYTES +1)

        if len(data) > MAX_DIFF_FILE_BYTES:
            raise ValueError("diff file exceeds read limit")

        return data.decode("utf-8")