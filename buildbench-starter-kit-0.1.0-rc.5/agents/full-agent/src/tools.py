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
from . import deb_archive

TOOLS = ("list_dir", "find_files", "search", "read_file", "current_diff", "patch_series",
         "read_log", "search_log", "deb_contents")

MAX_OUTPUT_CHARS = 8000
MAX_FILE_BYTES = 1024 * 1024
MAX_SEARCH_FILES = 100
MAX_TREE_ENTRIES = 5000
MAX_TOOL_SECONDS = 2.0
# The failure log is read-only evidence outside the worktree. A log can be
# megabytes, so its tools get longer and output lines are shortened.
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_LOG_SECONDS = 5.0
LOG_LINE_CHARS = 500
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def run_tool(request: ToolRequest, worktree: Path, *, diff_tracker : DiffTracker | None = None,
             log_path: Path | None = None) -> ToolResult:
    if not isinstance(request.args, dict):
        return ToolResult(
            request = request,
            text = "=== tool: unavailable (args must be a dictionary) ===",
            truncated = False,
        )

    try: 
        if request.tool in ("read_log", "search_log"):
            with tool_time_limit(MAX_LOG_SECONDS):
                return _run_log_tool(request, log_path)
        with tool_time_limit(MAX_TOOL_SECONDS):
            return _run_tool(request, worktree, diff_tracker= diff_tracker)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        return ToolResult(
            request= request,
            text = f"=== tool: unavailable ({error}) ===",
            truncated = False,
        ) 


def _log_line(line: bytes) -> str:
    """One log line as shown: decoded, without colour codes, long lines shortened."""
    text = _ANSI.sub("", line.decode("utf-8", errors="replace").rstrip("\r\n"))
    if len(text) > LOG_LINE_CHARS:
        half = LOG_LINE_CHARS // 2
        text = f"{text[:half]} ... [cut] ... {text[-half:]}"
    return text


def _run_log_tool(request: ToolRequest, log_path: Path | None) -> ToolResult:
    """read_log and search_log: numbered lines of the original failure log."""
    def unavailable(reason: str) -> ToolResult:
        return ToolResult(request=request, text=f"=== {request.tool}: unavailable ({reason}) ===",
                          truncated=False)

    if log_path is None:
        return unavailable("no build log for this run")
    args = request.args
    if request.tool == "read_log":
        start, end = args.get("start_line"), args.get("end_line")
        if type(start) is not int or type(end) is not int:
            return unavailable("start_line and end_line must be integers")
        if start < 1 or end < start:
            return unavailable("invalid line range")
        expression = None
    else:
        regex = args.get("regex")
        if not isinstance(regex, str) or not regex:
            return unavailable("regex must be a nonempty string")
        try:
            expression = re.compile(regex)
        except re.error:
            return unavailable("invalid regular expression")
        start, end = 1, None

    shown, used, truncated, total = [], 0, False, 0
    with Path(log_path).open("rb") as log:
        for number, raw in enumerate(log, start=1):
            total = number
            if log.tell() > MAX_LOG_BYTES:
                truncated = True
                break
            if number < start or truncated:
                continue
            if end is not None and number > end:
                break
            text = _log_line(raw)
            if expression is not None and expression.search(text) is None:
                continue
            entry = f"{number}: {text}"
            if used + len(entry) + 1 > MAX_OUTPUT_CHARS:
                truncated = True
                if end is not None:
                    break
                continue  # keep counting lines for the total
            shown.append(entry)
            used += len(entry) + 1

    if request.tool == "read_log":
        label = f"=== build log, lines {start}-{min(end, total)} ==="
        if start > total:
            label = f"=== build log: only {total} lines ==="
    else:
        label = f"=== search_log: {args['regex']}; the log has {total} lines ==="
    content = "\n".join(shown) if shown else "(no lines)"
    return ToolResult(request=request, text=f"{label}\n{content}", truncated=truncated)


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

def _unavailable(request: ToolRequest, reason: str, *, label: str | None = None) -> ToolResult:
    """The uniform failure result: nothing raised, nothing truncated."""
    return ToolResult(request=request,
                      text=f"=== {label or request.tool}: unavailable ({reason}) ===",
                      truncated=False)


def _checked_root(request: ToolRequest, worktree: Path, name: str) -> tuple[Path | None, ToolResult | None]:
    """input/ under the worktree, or the failure result to return instead."""
    root = worktree / "input"
    problem = check_path(root, worktree)
    if problem is not None:
        return None, _unavailable(request, problem, label=name)
    if not root.is_dir():
        return None, _unavailable(request, "input directory missing", label=name)
    return root, None


def _read_file(request: ToolRequest, worktree: Path, diff_tracker) -> ToolResult:
    args = request.args
    for name in ("path", "start_line", "end_line"):
        if name not in args:
            return _unavailable(request, f"missing argument: {name}")
    if (not isinstance(args["path"], str) or type(args["start_line"]) is not int
            or type(args["end_line"]) is not int):
        return _unavailable(request, "invalid argument type")
    path = worktree / args["path"]
    problem = check_path(path, worktree)
    if problem is not None:
        return _unavailable(request, problem)
    start, end = args["start_line"], args["end_line"]
    if start < 1 or end < start:
        return _unavailable(request, "invalid line range")
    truncated = False
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            return _unavailable(request, "not a file")
        with path.open("rb") as file:
            data = file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            return _unavailable(request, "file exceeds read limit")
        lines = data.decode("utf-8").splitlines(keepends=True)
        content = "".join(lines[start - 1:end])
        if len(content) > MAX_OUTPUT_CHARS:
            content = content[:MAX_OUTPUT_CHARS]
            truncated = True
        text = f"=== {args['path']} ===\n{content}"
    except FileNotFoundError:
        text = f"=== {args['path']}: unavailable (missing file) ==="
    except UnicodeDecodeError:
        text = f"=== {args['path']}: unavailable (not UTF-8 text) ==="
    except IsADirectoryError:
        text = f"=== {args['path']}: unavailable (not a file) ==="
    return ToolResult(request=request, text=text, truncated=truncated)


def _list_dir(request: ToolRequest, worktree: Path, diff_tracker) -> ToolResult:
    args = request.args
    if "path" not in args:
        return _unavailable(request, "missing argument: path")
    if not isinstance(args["path"], str):
        return _unavailable(request, "invalid argument type")
    truncated = False
    try:
        path = worktree / args["path"]
        problem = check_path(path, worktree)
        if problem is not None:
            return _unavailable(request, problem)
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
        text = f"=== {args['path']} ===\n" + "\n".join(sorted(entries))
    except OSError as error:
        text = f"=== list_dir: unavailable ({error}) ==="
    return ToolResult(request=request, text=text, truncated=truncated)


def _find_files(request: ToolRequest, worktree: Path, diff_tracker) -> ToolResult:
    pattern = request.args.get("name_glob")
    if not isinstance(pattern, str) or not pattern:
        return _unavailable(request, "name_glob must be a nonempty string")
    truncated = False
    try:
        root, failure = _checked_root(request, worktree, "find_files")
        if failure is not None:
            return failure
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
    return ToolResult(request=request, text=text, truncated=truncated)


def _search(request: ToolRequest, worktree: Path, diff_tracker) -> ToolResult:
    regex = request.args.get("regex")
    path_glob = request.args.get("path_glob")
    if not isinstance(regex, str) or not isinstance(path_glob, str):
        return _unavailable(request, "regex and path_glob must be strings")
    truncated = False
    try:
        expression = re.compile(regex)
        root, failure = _checked_root(request, worktree, "search")
        if failure is not None:
            return failure
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
            files_scanned += 1
            try:
                with path.open("rb") as file:
                    data = file.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    skipped += 1
                    continue
                lines = data.decode("utf-8").splitlines()
            except TimeoutError:
                raise
            except (OSError, UnicodeDecodeError):
                skipped += 1
                continue
            for number, line in enumerate(lines, start=1):
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
    except (OSError, ValueError) as error:
        text = f"=== search: unavailable ({error}) ==="
    return ToolResult(request=request, text=text, truncated=truncated)


def _patch_series(request: ToolRequest, worktree: Path, diff_tracker) -> ToolResult:
    listing = _run_tool(ToolRequest(tool="list_dir", args={"path": "input/debian/patches"}), worktree)
    series = _run_tool(ToolRequest(tool="read_file", args={
        "path": "input/debian/patches/series", "start_line": 1, "end_line": MAX_FILE_BYTES}), worktree)
    # Preserve application order before listing other directory entries.
    combined = ("=== Patch application order ===\n" + series.text
                + "\n=== Patch directory ===\n" + listing.text)
    return ToolResult(request=request, text=combined[:MAX_OUTPUT_CHARS],
                      truncated=series.truncated or listing.truncated or len(combined) > MAX_OUTPUT_CHARS)


def _current_diff(request: ToolRequest, worktree: Path, diff_tracker) -> ToolResult:
    if diff_tracker is None:
        return _unavailable(request, "no tracker supplied")
    truncated = False
    try:
        if diff_tracker.worktree.resolve() != worktree.resolve():
            return _unavailable(request, "wrong worktree")
        content, truncated = diff_tracker.current_diff(max_chars=MAX_OUTPUT_CHARS)
        text = "=== current_diff ===\n" + (content or "(no changes in tracked files)")
    except (OSError, ValueError, RuntimeError) as error:
        text = f"=== current_diff: unavailable ({error}) ==="
        truncated = False
    return ToolResult(request=request, text=text, truncated=truncated)


def _deb_contents(request: ToolRequest, worktree: Path, diff_tracker) -> ToolResult:
    """What a dependency package installs, from the case's frozen .deb files.

    {"package": "libfiat-dev"}                       lists its files
    {"package": "libfiat-dev", "filter": "cmake"}    only entries containing the text
    {"package": "libfiat-dev", "path": "usr/lib/.../fiat-targets.cmake"}  that file's text
    """
    args = request.args
    package = args.get("package")
    if not isinstance(package, str) or not package.strip():
        return _unavailable(request, "missing argument: package")
    for name in ("filter", "path"):
        if name in args and not isinstance(args[name], str):
            return _unavailable(request, f"invalid argument type: {name}")
    if not deb_archive.dependency_dirs(worktree):
        locked = deb_archive.lock_entries(worktree, package)
        text = ("=== deb_contents: unavailable (this worktree has no dependencies/ directory with the case's "
                ".deb files" + ("; config/dependency-lock.json lists " + ", ".join(
                    f"{d.get('package')} {d.get('version')} [{d.get('arch')}]" for d in locked[:6]) if locked else "")
                + ") ===")
        return ToolResult(request=request, text=text, truncated=False)
    debs = deb_archive.find_debs(worktree, package)
    if not debs:
        return _unavailable(request, f"no dependency package named like {package!r}; names are the .deb "
                                     "file names before the first underscore, for example libfiat-dev")
    if len(debs) > 6:
        names = ", ".join(d.name.split("_", 1)[0] for d in debs[:12])
        return _unavailable(request, f"{len(debs)} packages match {package!r}: {names}; give one name")
    if "path" in args:
        deb = debs[0]
        text, note = deb_archive.read_text(deb, args["path"])
        if text is None:
            return _unavailable(request, f"{deb.name}: {note}")
        truncated = len(text) > MAX_OUTPUT_CHARS
        return ToolResult(request=request, text=f"=== {deb.name}:{args['path']} ===\n{text[:MAX_OUTPUT_CHARS]}",
                          truncated=truncated)
    parts = [deb_archive.render(deb_archive.inspect(deb, pattern=args.get("filter", ""))) for deb in debs]
    text = "=== deb_contents ===\n" + "\n".join(parts)
    truncated = len(text) > MAX_OUTPUT_CHARS
    return ToolResult(request=request, text=text[:MAX_OUTPUT_CHARS], truncated=truncated)


# One function per worktree tool; the log tools are dispatched by run_tool
# because they take the log path instead of the worktree.
_WORKTREE_TOOLS = {
    "read_file": _read_file,
    "list_dir": _list_dir,
    "find_files": _find_files,
    "search": _search,
    "patch_series": _patch_series,
    "current_diff": _current_diff,
    "deb_contents": _deb_contents,
}


def _run_tool(request: ToolRequest, worktree: Path, *, diff_tracker: DiffTracker | None = None) -> ToolResult:
    """Run one worktree tool request; failures come back as text."""
    handler = _WORKTREE_TOOLS.get(request.tool)
    if handler is None:
        return _unavailable(request, "not implemented yet")
    return handler(request, worktree, diff_tracker)
