"""Measure Part 2 without downloading data or retaining large fixtures.

Use --synthetic for sparse files with identical tails and different total
sizes. Use --log PATH for a real dataset log. JSON is written to stdout.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
import tracemalloc


MODULE = (
    Path(__file__).resolve().parents[1]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/one-shot/src/log_tail.py"
)
spec = importlib.util.spec_from_file_location("log_tail", MODULE)
log_tail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log_tail)


def measure(path: Path, n_lines: int, repeats: int, expected=None) -> dict:
    times = []
    peaks = []
    for _ in range(repeats):
        tracemalloc.start()
        started = time.perf_counter()
        try:
            result = log_tail.extract_tail(path, n_lines)
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        if expected is not None and result != expected:
            raise AssertionError("tail did not match the known fixture")
        times.append(elapsed)
        peaks.append(peak)
    return {
        "size_bytes": path.stat().st_size,
        "n_lines": n_lines,
        "repeats": repeats,
        "seconds_first": times[0],
        "seconds_median": statistics.median(times),
        "seconds_max": max(times),
        "peak_python_bytes_max": max(peaks),
        "returned_characters": len(result),
        "under_one_second_all_runs": max(times) < 1,
        "known_tail_verified": expected is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", action="store_true")
    source.add_argument("--log", type=Path)
    parser.add_argument("--lines", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.repeats < 1 or args.lines < 1:
        parser.error("--repeats and --lines must be positive")

    report = {
        "kind": "synthetic_sparse" if args.synthetic else "real_log",
        "memory_measurement": "tracemalloc Python allocations; not process RSS",
        "timing_note": "local filesystem; OS caches are not flushed",
        "measurements": [],
    }
    if args.log:
        report["measurements"].append(measure(args.log, args.lines, args.repeats))
    else:
        expected = "".join(f"build failure diagnostic {i:06d}\n" for i in range(args.lines))
        suffix = b"older line\n" + expected.encode()
        with tempfile.TemporaryDirectory(prefix="ec528-log-tail-") as directory:
            path = Path(directory) / "synthetic.log"
            for size in (2_370_000, 237_000_000, 2_370_000_000):
                if len(suffix) > size:
                    parser.error("synthetic tail is larger than the smallest fixture")
                with path.open("wb") as log:
                    if os.name == "nt":
                        # Avoid physically allocating a multi-GB Windows fixture.
                        subprocess.run(
                            ["fsutil", "sparse", "setflag", str(path)],
                            check=True, capture_output=True,
                        )
                    log.seek(size - len(suffix))
                    log.write(suffix)
                report["measurements"].append(
                    measure(path, args.lines, args.repeats, expected)
                )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
