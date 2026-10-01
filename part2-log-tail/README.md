# Part 2: log tail extractor

Implements `extract_tail(path: Path, n_lines: int) -> str` for the one-shot
agent. The module is at
`buildbench-starter-kit-0.1.0-rc.2/agents/one-shot/src/log_tail.py`, alongside
Part 4's edit applier. It uses only the Python standard library.

## Integration

Part 1 can call it from an entrypoint launched with `python -m src.main`:

```python
from pathlib import Path
from src.log_tail import extract_tail

log_tail = extract_tail(Path("/workspace/input/initial-build.log"), 500)
# Supply this string as CaseContext.log_tail.
```

The agent shell, manifest, model adapter, and full-agent readiness are outside
this change. No changes were made to the official runner or the edit applier.

## Behavior

- Reads backward in 64 KiB blocks, stopping at the requested line boundary.
- Preserves whitespace, repeated lines, blank lines, CRLF, and the presence or
  absence of the final newline. Lines are delimited by LF; bare CR is content.
- A final LF terminates a line rather than creating a phantom extra line.
- Decodes UTF-8 once after assembling the tail; invalid bytes become U+FFFD.
- Empty logs return an empty string; short logs return all available lines.
- Missing files raise `FileNotFoundError`; other I/O errors propagate for Part
  1 to report. Missing evidence is not silently treated as an empty log.
- Zero lines returns an empty string without opening the file. Negative counts
  raise `ValueError`; non-integers (including bool) raise `TypeError`.
- Requires a stable, seekable file. A detected short read raises `OSError`.

Memory is O(returned tail bytes + read block), independent of total file size
for a fixed tail. Exceptionally long requested lines still require memory to
return them. There is no silent truncation, evidence filtering, deduplication,
temporary-file creation, or model call.

## Tests and benchmark

Run from the repository root with Python 3.11 or newer:

```bash
python -B -m unittest discover -s part2-log-tail/tests -v
python -B part2-log-tail/benchmark.py --synthetic
python -B part2-log-tail/benchmark.py --log /path/to/libyuv.log --lines 500 --repeats 7
```

The 11 tests cover edge cases, split Unicode and read boundaries, long lines,
250 seeded random comparisons with a reference implementation, bounded reads,
and two real demo logs. The fixtures are copies of the original and repaired
logs from the official hello example run on September 21, 2026. They are test
inputs, not repair logic or files for the agent submission ZIP.

`benchmark-results.json` preserves the September 26 measurement from the
original implementation: the synthetic 2.37 GB sparse file's last 500 lines
took at most 0.700 ms across seven runs. Peak traced Python allocations were
98,091 bytes at all three tested file sizes. These are local, cache-sensitive
measurements, not process RSS or a timing guarantee. Sparse fixtures have
known tails and are removed automatically; on Windows, creating them requires
filesystem support for `fsutil sparse setflag`.

**Pending acceptance check:** the actual 2.37 GB libyuv log was unavailable
during implementation. The large-file result is synthetic, not a measured
result on that dataset log. Integration with Part 1 and full-agent `bb ready`
also remain separate checks. No submission ZIP is produced by this module.
