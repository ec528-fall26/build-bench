"""Part B stand-in: read-only tools the model can request.

Placeholder so Part C can be written against the real signature. Every tool
answers "unavailable" and nothing is read. Part B replaces the body; the
contract is that a tool never writes and never raises (docs/full-agent.md, Part B).
"""

from __future__ import annotations
from pathlib import Path
from .contracts import ToolRequest, ToolResult
import os 
from fnmatch import fnmatchcase
import re
from .path_checks import check_path
from .diff_tracker import DiffTracker
from functools import lru_cache
import stat
from .tool_timeout import tool_time_limit

TOOLS = ("list_dir", "find_files", "search", "read_file", "current_diff", "patch_series")

MAX_OUTPUT_CHARS = 4000
MAX_FILE_BYTES = 1024 * 1024
MAX_SEARCH_FILES = 100
MAX_TREE_ENTRIES = 5000
MAX_TOOL_SECONDS = 2.0

def run_tool(request: ToolRequest, worktree: Path, *, diff_tracker : DiffTracker | None = None) -> ToolResult:
    if not isinstance(request.args, dict):
        return ToolResult(
            request = request,
            text = "=== tool: unavailable (args must be a dictionary) ===",
            truncated = False,
        )

    try: 
        with tool_time_limit(MAX_TOOL_SECONDS):
            return _run_tool(request, worktree, diff_tracker= diff_tracker)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        return ToolResult(
            request= request,
            text = f"=== tool: unavailable ({error}) ===",
            truncated = False,
        ) 


def collect_files(root: Path) -> tuple[list[Path], bool]:
    pending = [root]
    files = []
    visited = 0
    while pending:
        directory = pending.pop()

        with os.scandir(directory) as entries:
            for entry in entries:
                if visited >= MAX_TREE_ENTRIES:
                    return sorted(files), True

                visited +=1

                if entry.is_symlink():
                    continue

                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    files.append(Path(entry.path))

    return sorted(files), False

def matches_path(path: str, pattern: str) -> bool:
    if len(path) > 4096 or len(pattern) > 4096:
        raise ValueError("path or pattern exceeds length limit")

    
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")

    if len(path_parts) > 128 or len(pattern_parts) > 64:
        raise ValueError("path or patter nexceeds depth limit")

    @lru_cache(maxsize= None)

    def match(path_index:int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)

        part = pattern_parts[pattern_index]

        if part == "**":
            if match(path_index, pattern_index +1):
                return True

            return(
                path_index < len(path_parts)
                and match(path_index + 1, pattern_index)
            )

        return(
            path_index < len(path_parts)
            and fnmatchcase(path_parts[path_index], part)
            and match(path_index +1, pattern_index +1)
        )
    return match(0,0)

def _run_tool(request: ToolRequest, worktree: Path, *, diff_tracker : DiffTracker | None = None) -> ToolResult:
    """Run one tool request against the worktree; failures come back as text."""
    truncated = False

    if request.tool == "read_file":
        required = ("path", "start_line", "end_line")
        for name in required:
            if name not in request.args:
                return ToolResult(
                    request = request,
                    text = f"=== read_file: unavailable (missing argument: {name}) ===",
                    truncated= False,
                )

        if (
            not isinstance(request.args["path"], str)
            or type(request.args["start_line"]) is not int
            or type(request.args["end_line"]) is not int
        ):
            return ToolResult(
                request = request,
                text = "=== read_file: unavailable (invalid argument type) ===",
                truncated= False
            )
        
        path = worktree / request.args["path"]
        problem = check_path(path, worktree)

        if problem is not None:
            return ToolResult(
                request = request,
                text = f"=== {request.tool}: unavailable ({problem}) ===",
                truncated = False,
            )
        start = request.args["start_line"]
        end = request.args["end_line"]

        if start < 1 or end < start:
            return ToolResult(
                request = request,
                text = "=== read_file: unavailable (invalid line range) ===", 
                truncated = False, 
            )

        try: 
            if not stat.S_ISREG(path.stat().st_mode):
                return ToolResult(
                    request= request,
                    text = "=== read_fileL unavailable (not a file) ===",
                    truncated= False,
                )
            
            with path.open("rb") as file:
                data = file.read(MAX_FILE_BYTES +1)

            if len(data) > MAX_FILE_BYTES:
                return ToolResult(
                    request = request,
                    text = "=== read_file: unavailable (file exceeds read limit) ===",
                    truncated = False,
                )
            
            lines = data.decode("utf-8").splitlines(keepends = True)
            selected = lines[start - 1:end]
            content = "".join(selected)

            if len(content) > MAX_OUTPUT_CHARS:
                content = content[:MAX_OUTPUT_CHARS]
                truncated = True
            
            text = f"=== {request.args['path']} ===\n{content}"
        except FileNotFoundError:
            text = f"=== {request.args['path']}: unavailable (missing file) ==="
        except UnicodeDecodeError:
            text = f"=== {request.args['path']}: unavailable (not UTF-8 text) ==="
        except IsADirectoryError:
            text = f"=== {request.args['path']}: unavailable (not a file) ==="
   
    elif request.tool == "list_dir":
        if "path" not in request.args:
            return ToolResult(
                request = request,
                text = "=== list_dir: unavailable (missing argument: path) ===",
                truncated = False,
            )

        if not isinstance(request.args["path"], str):
            return ToolResult(
                request = request,
                text = "=== list_dir: unavailable (invalid argument type) ===",
                truncated= False,
            )

        try: 
            path = worktree / request.args["path"]
            problem = check_path(path, worktree)
            
            if problem is not None:
                return ToolResult(
                    request = request,
                    text = f"=== {request.tool}: unavailable ({problem}) ===",
                    truncated = False,
                )

            entries = []
            used_chars = 0

            for entry in path.iterdir():
                if entry.is_symlink():
                    name = entry.name + " [symlink]"
                elif entry.is_dir():
                    name = entry.name + "/"
                else:
                    name = entry.name

                if used_chars + len(name) + 1 > MAX_OUTPUT_CHARS:
                    truncated = True
                    break
                
                entries.append(name)
                used_chars += len(name) + 1

            content = "\n".join(sorted(entries))
            text = f"=== {request.args['path']} ===\n{content}"

        except OSError as error:
            text = f"=== list_dir: unavailable ({error}) ==="

    elif request.tool == "find_files": 
        pattern = request.args.get("name_glob")

        if not isinstance(pattern, str) or not pattern:
            return ToolResult(
                request = request,
                text = "=== find_files: unavailable (name_glob must be a nonempty string) ===",
                truncated = False,
            )

        try: 
            root = worktree/ "input"
            problem = check_path(root, worktree)

            if problem is not None:
                return ToolResult(
                    request = request,
                    text = f"=== find_files: unavailable ({problem}) ===",
                    truncated = False,
                )

            if not root.is_dir():
                return ToolResult(
                    request = request,
                    text = "=== find_files: unavailable (input directory missing) ===",
                    truncated = False,
                )

            files, tree_truncated = collect_files(root)
            matches = []
            used_chars = 0

            for path in files: 
                if not fnmatchcase(path.name, pattern):
                    continue

                relative = path.relative_to(worktree).as_posix()

                if used_chars + len(relative) + 1 > MAX_OUTPUT_CHARS:
                    truncated = True
                    break

                matches.append(relative)
                used_chars += len(relative) + 1

            truncated = truncated or tree_truncated

            content = "\n".join(matches) if matches else "(no matches)"
            text = f"=== find_files: {pattern} ===\n{content}"

        except OSError as error:
            text = f"=== find_files: unavailable ({error}) ==="

    elif request.tool == "search":
        regex = request.args.get("regex")
        path_glob = request.args.get("path_glob")

        if not isinstance(regex, str) or not isinstance(path_glob, str):
            return ToolResult(
                request = request,
                text = "=== search: unavailable (regex and path_glob must be strings) ===",
                truncated = False,
            )

        try: 
            expression = re.compile(regex)
            root = worktree / "input"
            problem = check_path(root, worktree)

            if problem is not None:
                return ToolResult(
                    request = request,
                    text = f"=== search: unavailable ({problem}) ===",
                    truncated = False,
                )

            if not root.is_dir():
                return ToolResult(
                    request = request,
                    text = "=== search: unavailable (input directory missing) ===",
                    truncated = False,
                )

            files, tree_truncated = collect_files(root)
            matches = []
            used_chars = 0
            skipped = 0
            files_scanned = 0

            for path in files:
                relative = path.relative_to(worktree).as_posix()

                if not matches_path(relative, path_glob):
                    continue
                
                if files_scanned >= MAX_SEARCH_FILES:
                    truncated = True
                    break
                
                files_scanned +=1

                try:
                    with path.open("rb") as file:
                        data = file.read(MAX_FILE_BYTES + 1)

                    if len(data) > MAX_FILE_BYTES:
                        skipped +=1
                        continue

                    lines = data.decode("utf-8").splitlines()
                except TimeoutError:
                    raise 
                except (OSError, UnicodeDecodeError):
                    skipped += 1
                    continue

                for number, line in enumerate(lines, start =1):
                    if expression.search(line) is None:
                        continue

                    match = f"{relative}:{number}:{line}"

                    if used_chars + len(match) + 1 > MAX_OUTPUT_CHARS:
                        truncated = True
                        break

                    matches.append(match)
                    used_chars += len(match) + 1

                if truncated:
                    break

            truncated = truncated or tree_truncated

            content = "\n".join(matches) if matches else "(no matches returned)"
            text = f"=== search: {regex}; skipped files: {skipped} ===\n{content}"

        except re.error:
            text = "=== search: unavailable (invalid regular expression) ==="
        except OSError as error:
            text = f"=== search: unavailable ({error}) ==="
        except ValueError as error:
            text = f"=== search: unavailable ({error}) ==="

    elif request.tool == "patch_series":
        listing = _run_tool(
            ToolRequest(
                tool="list_dir",
                args={"path": "input/debian/patches"},
            ),
            worktree,
        )
        series = _run_tool(
            ToolRequest(
                tool="read_file",
                args={
                    "path": "input/debian/patches/series",
                    "start_line": 1,
                    "end_line": MAX_FILE_BYTES,
                },
            ),
            worktree,
        )
        # Preserve application order before listing other directory entries.
        combined = (
            "=== Patch application order ===\n"
            + series.text
            + "\n=== Patch directory ===\n"
            + listing.text
        )
        return ToolResult(
            request=request,
            text=combined[:MAX_OUTPUT_CHARS],
            truncated=(
                series.truncated
                or listing.truncated
                or len(combined) > MAX_OUTPUT_CHARS
            ),
        )

    elif request.tool == "current_diff":
        if diff_tracker is None:
            return ToolResult(
                request = request,
                text = "=== current_diff: unavailable (no tracker supplied) ===",
                truncated = False,
            )

        try:
            if diff_tracker.worktree.resolve() != worktree.resolve():
                return ToolResult(
                    request = request,
                    text = "=== current_diff: unavailable (wrong worktree) ===",
                    truncated = False,
                )

            content, truncated = diff_tracker.current_diff(max_chars = MAX_OUTPUT_CHARS)

            if not content: 
                content = "(no changes in tracked files)"

            text = f"=== current_diff ===\n{content}"

        except (OSError, ValueError, RuntimeError) as error:
            text = f"=== current_diff: unavailable ({error}) ==="
            truncated = False
    
    else:
        text = f"=== {request.tool}: unavailable (not implemented yet) ==="
    return ToolResult(request=request, text=text, truncated=truncated)
