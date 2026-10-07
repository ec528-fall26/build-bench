"""Standalone Part 2 checks; no model, Docker, or external dependencies."""

import importlib.util
import io
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch


MODULE = (
    Path(__file__).resolve().parents[3]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/one-shot/src/log_tail.py"
)
spec = importlib.util.spec_from_file_location("log_tail", MODULE)
log_tail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log_tail)


def reference_tail(data: bytes, n: int) -> str:
    """Small-fixture oracle: LF-delimited lines, retaining terminators."""
    if not n or not data:
        return ""
    pieces = data.split(b"\n")
    lines = [piece + b"\n" for piece in pieces[:-1]]
    if pieces[-1]:
        lines.append(pieces[-1])
    return b"".join(lines[-n:]).decode("utf-8", errors="replace")


class TailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "build.log"

    def check_tail(self, data, n, expected):
        self.path.write_bytes(data)
        self.assertEqual(log_tail.extract_tail(self.path, n), expected)
        self.assertEqual(self.path.read_bytes(), data, "input was modified")

    def test_required_edge_cases(self):
        cases = [
            (b"", 500, ""),
            (b"one\ntwo\n", 500, "one\ntwo\n"),
            (b"one\ntwo\nthree\n", 2, "two\nthree\n"),
            (b"one\ntwo\nthree", 2, "two\nthree"),
            (b"one", 1, "one"),
            (b"\n", 1, "\n"),
            (b"one\n\n", 1, "\n"),
            (b"\n\n\n", 2, "\n\n"),
            (b"one\r\ntwo\r\n", 1, "two\r\n"),
            (b"one\rtwo", 1, "one\rtwo"),
            (b"old\n  repeated  \n  repeated  \n", 2,
             "  repeated  \n  repeated  \n"),
            (b"old\nerror: \xff\xfe\n", 1, "error: \ufffd\ufffd\n"),
            ("old\nerror: \u03bb \U0001f680\n".encode(), 1, "error: \u03bb \U0001f680\n"),
            (b"one\ntwo\n", 0, ""),
        ]
        for data, n, expected in cases:
            with self.subTest(data=data, n=n):
                self.check_tail(data, n, expected)

    def test_missing_file_is_explicit(self):
        with self.assertRaises(FileNotFoundError):
            log_tail.extract_tail(self.path, 500)

    def test_zero_does_not_open_missing_file(self):
        self.assertEqual(log_tail.extract_tail(self.path, 0), "")

    def test_invalid_counts(self):
        with self.assertRaises(ValueError):
            log_tail.extract_tail(self.path, -1)
        for value in (True, False, 1.5, "500", None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                log_tail.extract_tail(self.path, value)

    def test_boundaries_and_split_utf8(self):
        data = "first\r\n\u03bb\U0001f680\n\nlast \u00e9\r\n".encode()
        for block_size in (1, 2, 3, 4, 7, 16, 64):
            with patch.object(log_tail, "_BLOCK_SIZE", block_size):
                for n in (1, 2, 3, 4, 5, 500):
                    with self.subTest(block_size=block_size, n=n):
                        self.check_tail(data, n, reference_tail(data, n))

    def test_long_lines_are_not_truncated(self):
        data = b"old\n" + b"x" * (log_tail._BLOCK_SIZE * 3) + b"\nlast"
        self.check_tail(data, 2, reference_tail(data, 2))

    def test_no_newlines(self):
        data = b"x" * (log_tail._BLOCK_SIZE * 2 + 1)
        self.check_tail(data, 500, data.decode())

    def test_seeded_random_bytes_against_reference(self):
        rng = random.Random(528)
        alphabet = [0, 9, 10, 13, 32, 65, 128, 195, 255]
        with patch.object(log_tail, "_BLOCK_SIZE", 17):
            for _ in range(250):
                data = bytes(rng.choice(alphabet) for _ in range(rng.randrange(400)))
                n = rng.randrange(30)
                self.check_tail(data, n, reference_tail(data, n))

    def test_reads_only_tail_in_bounded_blocks(self):
        data = b"prefix\n" * 200_000 + b"last\n" * 500

        class GuardedReader(io.BytesIO):
            def __init__(self):
                super().__init__(data)
                self.bytes_read = 0

            def read(self, size=-1):
                if not 0 <= size <= log_tail._BLOCK_SIZE:
                    raise AssertionError("unbounded read")
                value = super().read(size)
                self.bytes_read += len(value)
                return value

        reader = GuardedReader()
        with patch.object(Path, "open", return_value=reader):
            self.assertEqual(log_tail.extract_tail(self.path, 500), "last\n" * 500)
        self.assertEqual(reader.bytes_read, log_tail._BLOCK_SIZE)

    def test_short_read_reports_error(self):
        class ShortReader(io.BytesIO):
            def read(self, size=-1):
                return super().read(max(0, size - 1))

        with patch.object(Path, "open", return_value=ShortReader(b"a\nb\n")):
            with self.assertRaises(OSError):
                log_tail.extract_tail(self.path, 1)

    def test_saved_real_demo_logs(self):
        evidence = Path(__file__).resolve().parent / "fixtures"
        for name in ("initial-build.log", "build.log"):
            path = evidence / name
            with self.subTest(log=name):
                data = path.read_bytes()
                self.assertEqual(log_tail.extract_tail(path, 500), reference_tail(data, 500))


if __name__ == "__main__":
    unittest.main()
