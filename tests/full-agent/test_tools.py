from pathlib import Path 
from tempfile import TemporaryDirectory
import unittest
import sys
from unittest.mock import patch
import os
import stat
from types import SimpleNamespace
import signal
import time

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT/ "buildbench-starter-kit-0.1.0-rc.2" / "agents" / "full-agent"
sys.path.insert(0, str(AGENT))

from src.diff_tracker import DiffTracker
from src.contracts import ToolRequest, ToolResult
from src.tools import run_tool, MAX_OUTPUT_CHARS, MAX_FILE_BYTES, MAX_SEARCH_FILES, collect_files

class TestTools(unittest.TestCase):

#Read file tests
    def test_read_file(self): 
        with TemporaryDirectory() as tmp: 
            worktree = Path(tmp) 
            source = worktree / "input" / "src"
            source.mkdir(parents = True)

            file_path = source / "example.c"
            file_path.write_text("first\nsecond\nthird\n")

            request = ToolRequest(
                tool = "read_file",
                args={
                    "path": "input/src/example.c",
                    "start_line": 2, 
                    "end_line": 3, 
                },
            )
            result = run_tool(request, worktree)

            self.assertIn("second\nthird", result.text)
            self.assertNotIn("first", result.text)
            self.assertFalse(result.truncated)

    def test_missing_file(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)

            request = ToolRequest(
                tool = "read_file",
                args ={
                    "path": "input/src/missing.c",
                    "start_line": 1,
                    "end_line": 5,
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertFalse(result.truncated)

    def test_invalid_line_range(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree / "input"
            source.mkdir()
            (source / "example.c").write_text("first\nsecond\nthird\n")

            request = ToolRequest(
                tool = "read_file",
                args = {
                    "path": "input/exaple.c",
                    "start_line": 0, 
                    "end_line": 2,
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertIn("invalid line range", result.text)

    def test_file_outside_input(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            (worktree/ "input").mkdir()

            (worktree/ "private.txt").write_text("do not expose this")

            request = ToolRequest(
                tool = "read_file",
                args = {
                    "path": "input/../private.txt",
                    "start_line": 1,
                    "end_line": 1,
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertNotIn("do not expose this", result.text)

    def test_symlink_rejected(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree/"input"
            source.mkdir()

            target = source/"example.c"
            target.write_text("file contents\n")

            shortcut = source/"shortcut.c"
            shortcut.symlink_to(target)

            request = ToolRequest(
                tool = "read_file",
                args={
                    "path": "input/shortcut.c",
                    "start_line":1,
                    "end_line":1, 
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertIn("symlink", result.text)

    def test_non_utf8_file(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree / "input"
            source.mkdir()

            (source / "example.c").write_bytes(b"\xff\xfe")

            request = ToolRequest(
                tool = "read_file",
                args = {
                    "path": "input/example.c",
                    "start_line": 1,
                    "end_line": 1,
                },
            )
            result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertIn("not UTF-8", result.text)

    def test_read_directory(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            (worktree / "input" / "src").mkdir(parents = True)

            request = ToolRequest(
                tool = "read_file",
                args = {
                    "path": "input/src",
                    "start_line": 1,
                    "end_line": 5,
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertIn("not a file", result.text)

    def test_large_output_is_truncated(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree / "input"
            source.mkdir()

            content = "x" * (MAX_OUTPUT_CHARS + 100)
            (source / "large.c").write_text(content)

            request = ToolRequest(
                tool = "read_file",
                args = {
                    "path": "input/large.c",
                    "start_line": 1,
                    "end_line": 1,
                },
            )

            result = run_tool(request, worktree)

            self.assertTrue(result.truncated)
            self.assertIn("x" * MAX_OUTPUT_CHARS, result.text)
            self.assertNotIn("x" * (MAX_OUTPUT_CHARS +1), result.text)

    def test_file_exceeds_read_limit(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            (source / "large.c").write_bytes(b"x" * (MAX_FILE_BYTES +1))

            request = ToolRequest(
                tool = "read_file",
                args = {
                    "path": "input/large.c",
                    "start_line":1, 
                    "end_line":1,
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertIn("file exceeds read limit", result.text)

    def test_missing_path_argument(self):
        with TemporaryDirectory() as tmp:
            request = ToolRequest(
                tool = "read_file",
                args = {
                    "start_line":1, 
                    "end_line":1,
                },
            )
            result = run_tool(request, Path(tmp))
        
            self.assertIn("unavailable", result.text)
            self.assertIn("missing argument", result.text)

    def test_invalid_argument_types(self):
        bad_arguments = [
            {"path": 123, "start_line": 1, "end_line": 3},
            {"path": "input/example.c", "start_line": "two", "end_line": 3},
            {"path": "input/example.c", "start_line": 1, "end_line": None},
        ]

        with TemporaryDirectory() as tmp:
            for args in bad_arguments:
                with self.subTest(args = args):
                    request = ToolRequest(tool = 'read_file', args = args)

                    result = run_tool(request, Path(tmp))

                    self.assertIn("unavailable", result.text)
                    self.assertIn("invalid argument type", result.text)

#List Directory Files
    def test_list_dir(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            (source / "example.c").write_text("some code\n")
            (source / "src").mkdir()

            request = ToolRequest(
                tool = "list_dir",
                args = {"path": "input"},
            )

            result = run_tool(request, worktree)

            self.assertIn("example.c", result.text)
            self.assertIn("src/", result.text)
            self.assertNotIn("unavailable", result.text)
            self.assertFalse(result.truncated)

    def test_list_dir_outside_input(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            private = worktree/ "private"
            private.mkdir()
            (private/ "secret.txt").write_text("fake test data")

            request = ToolRequest(
                tool = "list_dir",
                args = {"path": "input/../private"},
            )

            result = run_tool(request, worktree)
            
            self.assertIn("unavailable", result.text)
            self.assertIn("outside input directory", result.text)
            self.assertNotIn("secret.txt", result.text)

#Find Files Test
    def test_find_files(self): 
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            nested = source / "src"
            nested.mkdir(parents = True)

            (source/ "main.c").write_text("main code\n")
            (nested/ "helper.c").write_text("helper code\n")
            (nested/ "notes.txt").write_text("some notes\n")

            request = ToolRequest(
                tool = "find_files",
                args = {"name_glob": "*c"},
            )

            result = run_tool(request, worktree)

            self.assertIn("input/main.c", result.text)
            self.assertIn("input/src/helper.c", result.text)
            self.assertNotIn("notes.txt", result.text)
            self.assertNotIn("unavailable", result.text)
            self.assertFalse(result.truncated)

    def test_find_files_skips_symlink_driectories(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            outside = worktree /"outside"
            outside.mkdir()
            (outside/ "secret.c").write_text("fake test data\n")

            (source/"shortcut").symlink_to(
                outside, 
                target_is_directory=True,
            )

            request = ToolRequest(
                tool = "find_files",
                args = {"name_glob": "*c"},
            )

            result = run_tool(request, worktree)
            
            self.assertNotIn("secret.c", result.text)
            self.assertIn("(no matches)", result.text)
            self.assertFalse(result.truncated)

    def test_find_files_missing_pattern(self):
        with TemporaryDirectory() as tmp:
            request = ToolRequest(
                tool = "find_files",
                args = {},
            )

            result = run_tool(request, Path(tmp))

            self.assertIn("unavailable", result.text)
            self.assertIn("name_glob", result.text)
            self.assertFalse(result.truncated)
            
    def test_search(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input" / "src"
            source.mkdir(parents =True)

            (source/ "example.c").write_text(
                "// example\n"
                "#include <xmmintrin.h>\n"
                "int main(void) { return 0; }\n"
            )
            (source / "other.c").write_text("int value = 1;\n")

            request = ToolRequest(
                tool = "search",
                args = {
                    "regex": "xmmintrin",
                    "path_glob": "input/**/*.c",
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("input/src/example.c:2:#include <xmmintrin.h>", result.text)
            self.assertNotIn("other.c", result.text)
            self.assertNotIn("unavailable", result.text)
            self.assertFalse(result.truncated)

    def test_search_invalid_regex(self):
        with TemporaryDirectory() as tmp:
            request = ToolRequest(
                tool = "search",
                args = {
                    "regex": "[",
                    "path_glob": "input/**/*.c",
                },
            )

            result = run_tool(request, Path(tmp))

            self.assertIn("unavailable", result.text)
            self.assertIn("invalid regular expression", result.text)
            self.assertFalse(result.truncated)

    def test_search_filters_file_paths(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input" / "src"
            source.mkdir(parents =True)

            (source/ "example.c").write_text("needle\n")
            (source/ "notes.c").write_text("needle\n")

            request = ToolRequest(
                tool = "search",
                args = {
                    "regex": "needle",
                    "path_glob": "input/**/*.c",
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("input/src/example.c:1:needle", result.text)
            self.assertNotIn("notes.txt", result.text)
            self.assertFalse(result.truncated)

    def test_search_file_limit(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input" / "src"
            source.mkdir(parents =True)

            (source/ "a.c").write_text("nothing here\n")
            (source/ "b.c").write_text("nothing here\n")
            (source/ "c.c").write_text("needle\n")

            request = ToolRequest(
                tool = "search",
                args = {
                    "regex": "needle",
                    "path_glob": "input/**/*.c",
                },
            )

            with patch("src.tools.MAX_SEARCH_FILES", 2):
                result = run_tool(request, worktree)

            self.assertTrue(result.truncated)
            self.assertNotIn("input/src/c.c:1:needle", result.text)

    def test_search_glob_matches_root_and_nested_files(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            nested = source / "src"
            nested.mkdir(parents= True)

            (source / "main.c").write_text("needle\n")
            (nested / "helper.c").write_text("needle\n")

            request = ToolRequest(
                tool= "search",
                args = {
                    "regex": "needle",
                    "path_glob": "input/**/*.c",
                },
            )

            result = run_tool(request, worktree)

            self.assertIn("input/main.c:1:needle", result.text)
            self.assertIn("input/src/helper.c:1:needle", result.text)
            self.assertFalse(result.truncated)

    def test_search_single_star_stays_in_directory(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            nested = source / "src"
            nested.mkdir(parents= True)

            (source / "main.c").write_text("needle\n")
            (nested / "helper.c").write_text("needle\n") 

            request = ToolRequest(
                tool= "search",
                args = {
                    "regex": "needle",
                    "path_glob": "input/*.c",
                },
            )
            
            result = run_tool(request, worktree)
            
            self.assertIn("input/main.c:1:needle", result.text)
            self.assertNotIn("helper.c", result.text)
            self.assertFalse(result.truncated)

    def test_search_rejects_oversized_path_pattern(self):
        with TemporaryDirectory() as tmp:  
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()
            (source/"example.c").write_text("needle\n")

            request = ToolRequest(
                tool= "search",
                args = {
                    "regex": "needle",
                    "path_glob": "x" * 4097,
                },
            )

            result = run_tool(request, worktree)
            
            self.assertIn("unavailable", result.text)
            self.assertIn("length limit", result.text)

#Patch Series Test

    def test_patch_series(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            patches = worktree /"input" / "debian" / "patches"
            patches.mkdir(parents =True)

            (patches / "series").write_text(
                "fix-arm.patch\n"
                "fix-build.patch\n"
            )
            (patches / "fix-arm.patch").write_text("first patch\n")
            (patches / "fix-build.patch").write_text("second patch\n")

            request = ToolRequest(
                tool = "patch_series",
                args = {},
            )

            result = run_tool(request, worktree)

            self.assertIn("fix-arm.patch\nfix-build.patch", result.text)
            self.assertNotIn("unavailable", result.text)
            self.assertFalse(result.truncated)

    def test_patch_series_missing(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            (worktree /"input").mkdir()

            request = ToolRequest(
                tool = "patch_series",
                args = {},
            )

            result = run_tool(request, worktree)
            
            self.assertIn("unavailable", result.text)
            self.assertIn("missing file", result.text)
            self.assertIs(result.request, request)
            self.assertFalse(result.truncated)

#Current Different Test
    def test_tracker_keeps_first_original(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            file_path = source / "example.c"
            file_path.write_text("original\n")

            tracker = DiffTracker(worktree)
            tracker.capture_before_edit("input/example.c")

            file_path.write_text("first edit\n")

            tracker.capture_before_edit("input/example.c")

            self.assertEqual(tracker.originals["input/example.c"], "original\n")

    def test_tracker_shows_changes(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            file_path = source / "example.c"
            file_path.write_text("original\n")

            tracker = DiffTracker(worktree)
            tracker.capture_before_edit("input/example.c")
            
            file_path.write_text("updated\n")

            result, truncated = tracker.current_diff()

            self.assertIn("--- a/input/example.c", result)
            self.assertIn("+++ b/input/example.c", result)
            self.assertIn("-original\n", result)
            self.assertIn("+updated\n", result)
            self.assertFalse(truncated)

            self.assertEqual(file_path.read_text(), "updated\n")

    def test_tracker_rejects_outside_path(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            (worktree /"input").mkdir()
            (worktree / "private.txt").write_text("private data\n")

            tracker = DiffTracker(worktree)

            with self.assertRaisesRegex(ValueError, "outside input directory"):
                tracker.capture_before_edit("input/../private.txt")

            self.assertEqual(tracker.originals, {})

    def test_tracker_rejects_symlink_after_capture(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            file_path = source / "example.c"
            file_path.write_text("original\n")
            
            tracker = DiffTracker(worktree)
            tracker.capture_before_edit("input/example.c")

            outside = worktree/ "private.txt"
            outside.write_text("private data\n")

            file_path.unlink()
            file_path.symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "symlink"):
                tracker.current_diff()

    def test_tracker_rejects_large_files(self): 
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            (source/ "example.c").write_bytes(b"x" * 11)
            tracker = DiffTracker(worktree)

            with patch("src.diff_tracker.MAX_DIFF_FILE_BYTES", 10):
                with self.assertRaisesRegex(
                    ValueError, "diff file exceeds read limit"
                ):
                    tracker.capture_before_edit("input/example.c")
            self.assertEqual(tracker.originals, {})

    def test_tracker_total_snapshot_limit(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            (source/ "a.c").write_text("aaaaa")
            (source/ "b.c").write_text("bbbbb")

            tracker = DiffTracker(worktree)

            with patch("src.diff_tracker.MAX_SNAPSHOT_BYTES", 8):
                tracker.capture_before_edit("input/a.c")
                
                #tracking the same file again should use no extra spaces
                tracker.capture_before_edit("input/a.c")

                with self.assertRaisesRegex(
                    ValueError, "snapshot byte limit reached"
                ):
                    tracker.capture_before_edit("input/b.c")

            self.assertEqual(tracker.originals, {"input/a.c": "aaaaa"})
            self.assertEqual(tracker.saved_bytes, 5)

    def test_tracker_limits_diff_output(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            file_path = source / "example.c"
            file_path.write_text("a" * 100 + "\n")

            tracker = DiffTracker(worktree)
            tracker.capture_before_edit("input/example.c")

            file_path.write_text("b" * 100 + "\n")

            text, truncated = tracker.current_diff(max_chars = 80)

            self.assertTrue(truncated)
            self.assertEqual(len(text), 80)

    def test_current_diff_tool(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree /"input"
            source.mkdir()

            file_path = source / "example.c"
            file_path.write_text("original\n")

            tracker = DiffTracker(worktree)
            tracker.capture_before_edit("input/example.c")

            file_path.write_text("updated\n")

            request = ToolRequest(tool = "current_diff", args={})
            result = run_tool(
                request, worktree, diff_tracker = tracker,
            )

            self.assertIn("-original\n", result.text)
            self.assertIn("+updated\n", result.text)
            self.assertIs(result.request, request)
            self.assertFalse(result.truncated)

    def test_current_diff_without_tracker(self):
        with TemporaryDirectory() as tmp: 
            request = ToolRequest(tool= "current_diff", args={})

            result = run_tool(request, Path(tmp))

            self.assertIn("unavailable", result.text)
            self.assertIn("no tracker supplied", result.text)
            self.assertIs(result.request, request)
            self.assertFalse(result.truncated)

    def test_collect_files_limits_traversal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            for name in ("a.txt", "b.txt", "c.txt"):
                (root / name).write_text("test\n")

            with patch("src.tools.MAX_TREE_ENTRIES", 2):
                files, truncated = collect_files(root)

            self.assertTrue(truncated)
            self.assertEqual(len(files), 2)
            self.assertTrue(all(path.parent == root for path in files))

    def test_find_files_reports_traversal_limit(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree/ "input"
            source.mkdir()

            for name in ("a.txt", "b.txt", "c.txt"):
                (source / name).write_text("test\n")

            request = ToolRequest(
                tool = "find_files",
                args={"name_glob": "*.c"},
            )

            with patch("src.tools.MAX_TREE_ENTRIES", 2):
                result = run_tool(request, worktree)

            self.assertTrue(result.truncated)
            self.assertIn("(no matches)", result.text)
                
    def test_search_reports_traversal_limit(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree/ "input"
            source.mkdir()

            for name in ("a.txt", "b.txt", "c.txt"):
                (source / name).write_text("needle\n")

            request = ToolRequest(
                tool = "search",
                args={
                    "regex": "needle",
                    "path_glob": "input/**/*.c",
                    },
            )

            with patch("src.tools.MAX_TREE_ENTRIES", 2):
                result = run_tool(request, worktree)

            self.assertTrue(result.truncated)
            self.assertIn("(no matches returned)", result.text)

    def test_tracker_diff_empty_after_rollback(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree/ "input"
            source.mkdir()

            file_path = source / "example.c"
            file_path.write_text("original\n")

            tracker = DiffTracker(worktree)
            tracker.capture_before_edit("input/example.c")

            file_path.write_text("failed repair\n")

            changed, _ = tracker.current_diff()
            self.assertIn("+failed repair\n", changed)

            file_path.write_text("original\n")

            restored, truncated = tracker.current_diff()

            self.assertEqual(restored, "")
            self.assertFalse(truncated)
            self.assertEqual(tracker.originals["input/example.c"], "original\n")

    def test_tracker_with_no_captured_files(self):
        with TemporaryDirectory() as tmp:
            tracker = DiffTracker(Path(tmp))

            text, truncated = tracker.current_diff()

            self.assertEqual(text, "")
            self.assertFalse(truncated)

    def test_tracker_diff_includes_multiple_files(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree/ "input"
            source.mkdir()

            tracker = DiffTracker(worktree)

            for name in ("a.c", "b.c"):
                path = source/ name
                path.write_text(f"original {name}\n")
                tracker.capture_before_edit(f"input/{name}")
                path.write_text(f"updated {name}\n")

            text ,truncated = tracker.current_diff()

            for name in ("a.c", "b.c"):
                self.assertIn(f"--- a/input/{name}", text)
                self.assertIn(f"-original {name}\n", text)
                self.assertIn(f"+updated {name}\n", text)

            self.assertFalse(truncated)

    def test_tool_rejects_invalid_args_container(self):
        with TemporaryDirectory() as tmp:
            for args in (None, [], "bad arguments"):
                with self.subTest(args = args):
                    request = ToolRequest(tool="search", args = args)

                    result = run_tool(request, Path(tmp))

                    self.assertIn("unavailable", result.text)
                    self.assertIn("dictionary", result.text)
                    self.assertIs(result.request, request)

    def test_read_file_rejects_null_character_in_path(self):
        with TemporaryDirectory() as tmp:
            request = ToolRequest(
                tool = "read_file",
                args={
                    "path": "input/\x00bad",
                    "start_line": 1,
                    "end_line": 1,
                },
            )

            result = run_tool(request, Path(tmp))

            self.assertIn("unavailable", result.text)
            self.assertFalse(result.truncated)

    def test_read_file_handles_permission_error(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree / "input"
            source.mkdir()
            (source / "example.c").write_text("hello\n")

            request = ToolRequest(
                tool="read_file",
                args={
                    "path": "input/example.c",
                    "start_line": 1,
                    "end_line": 1,
                },
            )

            with patch.object(
                Path, "open", side_effect=PermissionError("access denied")
            ):
                result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertIn("access denied", result.text)

    def test_read_file_rejects_special_file(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree / "input"
            source.mkdir()
            target = source / "example.c"
            target.write_text("hello\n")

            request = ToolRequest(
                tool="read_file",
                args={
                    "path": "input/example.c",
                    "start_line": 1,
                    "end_line": 1,
                },
            )

            original_stat = Path.stat

            def fake_stat(path, *args, **kwargs):
                if path == target and kwargs.get("follow_symlinks", True):
                    return SimpleNamespace(st_mode=stat.S_IFIFO)
                return original_stat(path, *args, **kwargs)

            with patch.object(Path, "stat", fake_stat):
                with patch.object(Path, "open") as mocked_open:
                    result = run_tool(request, worktree)

            self.assertIn("unavailable", result.text)
            self.assertIn("not a file", result.text)
            mocked_open.assert_not_called()

    def test_tracker_limits_number_of_files(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            source = worktree / "input"
            source.mkdir()

            (source / "a.c").write_text("")
            (source / "b.c").write_text("")

            tracker = DiffTracker(worktree)

            with patch("src.diff_tracker.MAX_TRACKED_FILES", 1):
                tracker.capture_before_edit("input/a.c")

                # An existing capture is still allowed.
                tracker.capture_before_edit("input/a.c")

                with self.assertRaisesRegex(
                    ValueError, "tracked file limit reached"
                ):
                    tracker.capture_before_edit("input/b.c")

            self.assertEqual(tracker.originals, {"input/a.c": ""})

    def test_patch_series_lists_unlisted_patch(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            patches = worktree / "input" / "debian" / "patches"
            patches.mkdir(parents=True)

            (patches / "series").write_text("active.patch\n")
            (patches / "active.patch").write_text("active patch\n")
            (patches / "unused.patch").write_text("unused patch\n")

            request = ToolRequest(tool="patch_series", args={})
            result = run_tool(request, worktree)

            self.assertIn("=== Patch application order ===", result.text)
            self.assertIn("=== Patch directory ===", result.text)
            self.assertIn("unused.patch", result.text)
            self.assertIs(result.request, request)
            self.assertFalse(result.truncated)


    def test_tool_timeout_and_cleanup(self):
        request = ToolRequest(tool="list_dir", args={"path": "input"})
        previous_handler = signal.getsignal(signal.SIGALRM)

        def slow_tool(*args, **kwargs):
            time.sleep(0.2)
            return ToolResult(request, "finished without interruption", False)

        with TemporaryDirectory() as tmp:
            with patch("src.tools.MAX_TOOL_SECONDS", 0.01):
                with patch("src.tools._run_tool", side_effect=slow_tool):
                    result = run_tool(request, Path(tmp))

        self.assertIn("unavailable", result.text)
        self.assertIn("time limit", result.text)
        self.assertFalse(result.truncated)
        self.assertEqual(
            signal.getsignal(signal.SIGALRM),
            previous_handler,
        )
        self.assertEqual(
            signal.getitimer(signal.ITIMER_REAL),
            (0.0, 0.0),
        )


if __name__ == "__main__":
    unittest.main()
