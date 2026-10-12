# Part B — Read-only tools

Part B lets the model inspect a package before proposing a repair. Part C parses
the model's requests, calls these tools, and returns their results to the model.
Tools do not apply edits or run builds.

All six tools are implemented and covered by local unit tests. Connecting them
to Part C's loop and enforcing a per-case tool-call budget remain integration
work. See the [full-agent plan](full-agent.md).

## Files

The implementation lives in
`buildbench-starter-kit-0.1.0-rc.2/agents/full-agent/src/`:

| File | Responsibility |
| --- | --- |
| `tools.py` | Public dispatcher, six tools, bounded traversal, path-pattern matching |
| `path_checks.py` | Reject symlinks and resolved paths outside the source directory |
| `diff_tracker.py` | Capture originals before edits and generate a bounded diff |
| `tool_timeout.py` | Signal-based execution timer |
| `contracts.py` | Existing shared `ToolRequest` and `ToolResult` types |

Tests are in [`tests/full-agent/test_tools.py`](../tests/full-agent/test_tools.py).
The implementation uses only the Python standard library.

## Workspace and public interface

`worktree` is the directory containing the package's `input/` directory:

```text
<worktree>/
└── input/
    ├── src/
    └── debian/
```

This package-source directory is distinct from the runtime's
`/workspace/input/`, which holds task metadata and initial build evidence.
Pass the package worktree, normally `/workspace/work/repo`, to the dispatcher.
Use a stable absolute worktree path and serialize tool calls and edits.

```python
from src.contracts import ToolRequest
from src.tools import run_tool

request = ToolRequest(
    tool="read_file",
    args={"path": "input/src/example.c", "start_line": 1, "end_line": 30},
)
result = run_tool(request, worktree)
```

The public signature is:

```python
run_tool(request, worktree, *, diff_tracker=None) -> ToolResult
```

Existing two-argument calls still work. `current_diff` needs the optional tracker.
`_run_tool` is an internal implementation detail; external callers should use
`run_tool` for argument checks, timeout setup, and error handling.

| Result field | Meaning |
| --- | --- |
| `request` | The original request object, including for composed tools |
| `text` | Labelled content or an `unavailable (reason)` explanation |
| `truncated` | An output, file-count, or traversal limit prevented a complete result |

An unavailable response normally has `truncated=False`; this does not mean the
operation succeeded. Forward both text and the flag to the model. Search also
reports how many matching files it skipped as unreadable, oversized, or non-UTF-8.
Consequently, no returned matches is not necessarily proof that no matches exist.

## Tool arguments and behavior

| Tool | Arguments | Result |
| --- | --- | --- |
| `read_file` | `path`, `start_line`, `end_line` | Requested text, with a path label; line numbers are 1-based and inclusive |
| `list_dir` | `path` | Immediate entries; directories end in `/`, symlinks are labelled without following them |
| `find_files` | `name_glob` | Matching regular-file paths anywhere under `input/`, relative to the worktree |
| `search` | `regex`, `path_glob` | Matching lines formatted as `path:line_number:text` |
| `patch_series` | `{}` | Ordered `debian/patches/series` contents followed by a listing of `debian/patches/` |
| `current_diff` | `{}` | Unified diff for captured edit paths against their original contents |

Paths should use the `input/...` convention. `read_file` requires integer line
numbers, with `start_line >= 1` and `end_line >= start_line`. An ending line past
EOF returns the available lines; a range entirely past EOF returns empty content.
Reads reject nonregular files and decode UTF-8 strictly.

`find_files` matches the basename, not the whole path. For example, `*.c` finds
both `input/main.c` and `input/src/helper.c`. Matching is case-sensitive.

`search` uses Python regular expressions for text and component-based wildcards
for paths. `input/*.c` matches direct children; `input/**/*.c` also matches files
at any deeper level. `*` matches within one component; `**` matches zero or more
components. Use `/` separators. Search uses `re.search` on each line independently
and returns one result per matching line. It does not search across line breaks.

Listings are sorted after collection. If traversal is truncated, the collected
subset can vary with filesystem enumeration order; it is not a guaranteed
alphabetical prefix of the entire tree.

`patch_series` preserves application order rather than sorting the series file.
The separate directory listing can reveal patches not mentioned in `series`;
their presence does not mean they are applied. Missing sections are labelled
unavailable. The tool does not parse patch options, validate applicability, or
read every patch's contents; use `read_file` for a particular patch.

## Current limits

These are implementation defaults, not organizer-announced competition budgets.

| Limit | Default | Behavior |
| --- | --- | --- |
| Returned content | 4,000 characters | Cuts content and sets `truncated=True` |
| Individual file read | 1 MiB | `read_file` rejects it; search skips it |
| Tree entries visited | 5,000 | `find_files` and `search` return the collected subset with truncation |
| Matching files opened by search | 100 | Further matching files cause truncation |
| Tool timer | 2 seconds | Timeout becomes an unavailable response |
| Path/pattern length in search matcher | 4,096 characters each | Rejects an oversized path or pattern when evaluated |
| Search path/pattern components | 128 / 64 | Bounds wildcard recursion |
| Diff file read | 1 MiB | Capture or diff raises an error |
| Captured original content | 16 MiB per tracker | Additional captures exceeding the budget are rejected |
| Captured file count | 256 per tracker | Additional distinct paths are rejected |

For most tools, the 4,000-character cap applies to content, with labels added
afterward. `patch_series` caps its combined response including labels. Error
messages and echoed arguments do not currently share a universal response-size
cap. Part C must still enforce its overall context/token budget.

The file-byte limit is conservative: asking for one line from a file larger than
1 MiB is rejected by `read_file`. Snapshot bytes count UTF-8 content, not Python
object overhead. A truncated diff is an inspection result, not an applicable
complete patch.

## Connecting the diff tracker to Part C

Create one tracker at case start and keep it across attempts and rollbacks:

```python
from src.diff_tracker import DiffTracker
from src.tools import run_tool

tracker = DiffTracker(worktree)

# Before each batch: capture EVERY proposed path before applying ANY edits.
for edit in edits:
    tracker.capture_before_edit(edit.path)

# Part C now applies the batch through the existing edit applier.
# Later, dispatch model requests with the same tracker:
result = run_tool(request, worktree, diff_tracker=tracker)
```

This example shows the ordering, not a complete repair loop. Part C must catch
capture errors, record them, and skip the edit batch if capture fails. Otherwise
the diff could omit changes whose originals were never saved. Successfully
captured paths from an aborted batch can remain in the tracker.

Repeated capture of exactly the same path is a no-op. Use consistent canonical
relative path strings, such as `input/src/example.c`, for every attempt. The
tracker keys by the supplied string; aliases of one file must not be treated as
different original versions.

The tracker compares the original with the latest on-disk contents. Restoring a
file to its original contents removes its change from the diff automatically.
Do not reset or recreate the tracker between attempts. With no captured files,
the diff is empty. This means no changes in tracked files, not a complete audit
of the package tree.

Current tracking supports existing UTF-8 regular files. It does not implement
file creation or deletion diffs, persistence across process restarts, or detection
of changes to paths that were never captured. A tracked file becoming missing,
unreadable, oversized, or a symlink makes `current_diff` unavailable. Do not claim
a complete worktree diff unless every edit path was captured before modification.

Direct tracker methods raise errors; only calls through `run_tool` get the tool
timer and unavailable-response wrapper. Part C must bound capture work within
its own remaining case time. It can use `tool_time_limit` around a capture batch
only when no other real-time timer is active.

## Timeout requirements

The timeout uses `SIGALRM` and `setitimer` on Linux/macOS. Calls must run on the
main Python thread with no existing `ITIMER_REAL` timer. Unsupported platforms,
background threads, or an already-active timer yield an unavailable response.
The previous signal handler is restored and the tool timer is cleared on exit.

Part C must account for this before introducing threads or its own signal-based
case timeout. This is an in-process interruption mechanism, not a process-level
resource sandbox or a substitute for the outer case deadline. Composed tools
use `_run_tool` internally to share the outer timer. A caught timeout consumes
that one-shot timer; subsequent internal work does not automatically get a new
deadline. Retain an outer case limit for integration.

## Per-case tool-call budget — pending Part C integration

There is currently no tool-call counter in Part B or `Budget`. The proposed
integration is for Part C to own one counter per case:

1. Check the remaining allowance before dispatching each model-requested tool.
2. Count each attempted request, including unavailable results and timeouts.
3. For a model turn containing several requests, count each request separately.
4. Count `patch_series` as one request; its internal reads are not model calls.
5. Keep the counter across repair attempts and reject dispatch after exhaustion.

The numeric limit and ownership still need team agreement. Do not change the
shared `Budget` contract without coordinating with its owner. The integration
test should prove that the request after the limit is rejected without executing
the tool and that starting a new case resets the counter. Local Part B tests do
not yet establish this case-wide behavior.

## Verification and handoff

From the repository root:

```bash
python -m unittest discover -s tests/full-agent -v
```

At the latest implementation check, 58 tests passed: 47 tool/tracker tests and
11 scaffold tests. Coverage includes path escapes, symlinks, malformed arguments,
invalid UTF-8, permission errors, special files, limits, timeout cleanup, patch
directory listing, and multi-file/rollback diffs. These are local unit tests,
not evidence of a live-model repair or completed Part C integration.

Before declaring the integration complete, connect the dispatcher and capture
ordering to the real loop, agree on the call budget and timeout ownership, and
run the budget and multi-attempt integration tests. Part B itself requires no
model credentials or build server for its local tests.
