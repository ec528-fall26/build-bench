"""Part A behavior and regressions; no model, network or build required."""
import dataclasses
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
                      "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"))
from src.evidence import diagnose
from src.case import load_case


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "build.log"

    def parse(self, text):
        self.log.write_bytes(text.encode("utf-8"))
        return diagnose(self.log, self.root)

    def source(self, name):
        path = self.root / "input" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source must never replace the compiler quote")

    def test_rcran_and_byte_provenance(self):
        self.source("src/crc32c/crc32c_prefetch.h")
        prefix = "é\r\n   dh_auto_build\r\n"
        result = self.parse(prefix + "crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: No such file or directory\r\n"
                            "   18 | #include <xmmintrin.h>\r\n      |          ^~~~~~~~~~~~~\r\n")
        self.assertEqual(dataclasses.asdict(result[0]), {
            "message": "fatal error: xmmintrin.h: No such file or directory",
            "stage": "build", "log_path": "crc32c/crc32c_prefetch.h", "line": 18,
            "resolved": ["input/src/crc32c/crc32c_prefetch.h"],
            "source_line": "#include <xmmintrin.h>", "log_offset": len(prefix.encode())})

    def test_duplicate_across_files_keeps_first(self):
        result = self.parse("a.c:1: error: bad type\n 1 | first\nb.c:2: error: bad type\n 2 | second\n"
                            "make[2]: *** [all] Error 1\nmake[2]: *** [all] Error 1\n")
        self.assertEqual(len(result), 2)
        self.assertEqual((result[0].log_path, result[0].source_line), ("a.c", "first"))

    def test_stages(self):
        stages = ("configure", "build", "test", "install")
        result = self.parse("".join(f"dh_auto_{s}\nx.c:1: error: {s}\n" for s in stages)
                            + "dpkg-deb --build x\nerror: packaging failed\n")
        self.assertEqual([d.stage for d in result], [*stages, "packaging"])

    def test_excerpt_clang_ansi_and_no_final_newline(self):
        result = self.parse("\x1b[31mx.c:7:2: error: bad\x1b[0m\n  int x = ;\n          ^")
        self.assertEqual(result[0].source_line, "  int x = ;")
        self.assertEqual(result[0].stage, "unknown")

    def test_no_invented_quote(self):
        result = self.parse("x.c:7: error: bad\nunrelated output\nmore output\nend\n")
        self.assertIsNone(result[0].source_line)

    def test_paths_ambiguous_missing_and_escape(self):
        self.source("a/x.h")
        self.source("b/x.h")
        result = self.parse("../x.h:1: error: a\nmissing.h:2: error: b\na/../../x.h:3: error: c\n")
        self.assertEqual(result[0].resolved, ["input/a/x.h", "input/b/x.h"])
        self.assertEqual(result[1].resolved, [])
        self.assertEqual(result[2].resolved, [])

    def test_symlinks_not_resolved(self):
        self.source("real.h")
        try:
            (self.root / "input/link.h").symlink_to(self.root / "input/real.h")
            (self.root / "input/dir").symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("host does not permit symlinks")
        self.assertEqual(self.parse("link.h:1: error: bad\n")[0].resolved, [])

    def test_linker_make_and_plain_output(self):
        result = self.parse("all good\n/usr/bin/ld: a.o: undefined reference to `foo'\n"
                            "make: *** No rule to make target 'x'. Stop.\n")
        self.assertEqual(len(result), 2)
        self.assertIsNone(result[0].log_path)
        self.assertEqual(self.parse("all good\n"), [])

    def test_repeated_gtest_failures(self):
        result = self.parse("dh_auto_test\n./unit_test/x.cc:23: Failure\nExpected equality\n"
                            "./unit_test/y.cc:24: Failure\nExpected equality\n")
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0].line, result[0].stage, result[0].message), (23, "test", "Failure"))

    def test_inactivity_timeout_and_packaging_error(self):
        result = self.parse("dh_auto_test\nE: Build killed with signal TERM after 150 minutes of inactivity\n"
                            "dpkg-gensymbols: error: missing symbols\n")
        self.assertEqual([d.stage for d in result], ["test", "packaging"])

    def test_empty_missing_invalid_utf8(self):
        self.assertEqual(diagnose(self.log, self.root), [])
        self.assertEqual(self.parse(""), [])
        self.log.write_bytes(b"x.c:1: error: bad \xff\n")
        self.assertIn("\ufffd", diagnose(self.log, self.root)[0].message)

    def test_streaming_without_whole_file_reads(self):
        class Lines:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self):
                for _ in range(100_000):
                    yield b"x.c:1: error: repeat\n"
            def read(self, *args): raise AssertionError("whole-file read")
        with patch.object(Path, "open", return_value=Lines()):
            self.assertEqual(len(diagnose(self.log, self.root)), 1)


class Cases(unittest.TestCase):
    def test_dataset_nested_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.json"
            task.write_text(json.dumps({"package": {"name": "digest"},
                                        "platform": {"target_arch": "aarch64"}}))
            case = load_case(task)
            self.assertEqual((case.package, case.target_arch), ("digest", "aarch64"))

    def test_task_precedence_manifest_fallback_and_unchanged_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, manifest = root / "task.json", root / "manifest.json"
            task.write_text(json.dumps({"case_id": "task", "target_arch": "arm64", "worktree": "repo"}))
            manifest.write_text(json.dumps({"case_id": "manifest", "build": {
                "architecture": "amd64", "recipe": "input/r-cran-digest_0.6.32-1.dsc"}}))
            before = (task.read_bytes(), manifest.read_bytes())
            case = load_case(task, manifest_path=manifest)
            self.assertEqual((case.case_id, case.package, case.target_arch), ("task", "r-cran-digest", "arm64"))
            self.assertEqual(case.worktree, root / "repo")
            self.assertEqual(case.log_path, root / "initial-build.log")
            self.assertEqual(before, (task.read_bytes(), manifest.read_bytes()))
            self.assertEqual(load_case(task, worktree=root, log_path=task).worktree, root)

    def test_missing_optional_fields_and_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.json"
            task.write_text("{}")
            case = load_case(task)
            self.assertIsNone(case.package)
            self.assertIsNone(case.target_arch)
            task.write_text("[]")
            with self.assertRaises(ValueError): load_case(task)


if __name__ == "__main__":
    unittest.main()
