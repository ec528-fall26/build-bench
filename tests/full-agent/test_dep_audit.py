"""Runtime safety and readable dependencies: dep_audit on synthetic debs, search without a silent
cap, run_tool turning any exception into a result, and main() keeping edits after an error.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent -p test_dep_audit.py
"""

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import dep_audit, tools  # noqa: E402
from src import main as agent_main  # noqa: E402
from src.contracts import ToolRequest  # noqa: E402
from test_deb_contents import make_deb  # noqa: E402

ARM = "usr/lib/aarch64-linux-gnu"


class Worktree(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "input/debian").mkdir(parents=True)
        self.deps = self.root / "dependencies/ubuntu-noble-arm64"
        self.deps.mkdir(parents=True)

    def control(self, depends):
        (self.root / "input/debian/control").write_text(
            f"Source: x\nBuild-Depends: {depends}\n\nPackage: x\n")


class Audit(Worktree):
    def setUp(self):
        super().setUp()
        # the pybigwig shape: -dev links to a library the empty runtime package should ship
        make_deb(self.deps, "libbigwig-dev", "1", "arm64", {
            f"{ARM}/libBigWig.so": ("link", "libBigWig.so.0"), f"{ARM}/libBigWig.a": b"!<arch>\n"})
        make_deb(self.deps, "libbigwig0t64", "1", "arm64", {})
        # healthy pair
        make_deb(self.deps, "libok-dev", "1", "arm64", {f"{ARM}/libok.so": ("link", "libok.so.1")})
        make_deb(self.deps, "libok1", "1", "arm64", {f"{ARM}/libok.so.1": b"elf"})
        # the ectrans shape
        make_deb(self.deps, "libfiat-dev", "1", "arm64", {
            f"{ARM}/cmake/fiat/fiat-targets.cmake":
                b'set_target_properties(fiat PROPERTIES\n INTERFACE_INCLUDE_DIRECTORIES '
                b'"/usr/lib/aarch64-linux-gnu/fortran/aarch64-linux-gnu-gfortran-13/fiat")\n',
            "usr/include/aarch64-linux-gnu/fortran/gfortran-mod-15/fiat/yomhook.mod": b"mod"})

    def render(self, log, depends="libok-dev"):
        self.control(depends)
        return dep_audit.render(self.root, log.splitlines(), "aarch64")

    def test_names_the_dangling_link_and_the_empty_runtime_package(self):
        text = self.render("/usr/bin/ld: cannot find -lBigWig: No such file\nld returned 1 exit status\n")
        self.assertIn("DEPENDENCY FACTS", text)
        self.assertIn("libBigWig.so -> libBigWig.so.0", text)
        self.assertIn("libbigwig0t64 ships no library", text)
        self.assertNotIn("libok", text)

    def test_names_the_missing_module_directory_and_its_neighbour(self):
        text = self.render("CMake Error in src/CMakeLists.txt:\n  Imported target \"fiat\" includes non-existent path\n"
                           "    \"/usr/lib/aarch64-linux-gnu/fortran/aarch64-linux-gnu-gfortran-13/fiat\"\n")
        self.assertIn("aarch64-linux-gnu-gfortran-13/fiat, which no package in the set ships", text)
        self.assertIn("gfortran-mod-15/fiat", text)

    def test_relevance_gate_silences_unrelated_failures(self):
        text = self.render("src/a.c:3:10: fatal error: xmmintrin.h: No such file or directory\n",
                           depends="debhelper-compat (= 13), libok-dev")
        self.assertEqual(text, "")

    def test_build_depends_alone_passes_the_gate(self):
        text = self.render("src/a.c:3:10: fatal error: xmmintrin.h: No such file or directory\n",
                           depends="libbigwig-dev (>= 0.4)")
        self.assertIn("libBigWig.so", text)

    def test_standalone_empty_runtime_package(self):
        make_deb(self.deps, "libgone2", "1", "arm64", {})
        text = self.render("error: libgone2 broke\n", depends="x")
        self.assertIn("libgone2 is a runtime library package and ships no files", text)

    def test_time_budget_reports_partial_and_never_raises(self):
        self.control("libbigwig-dev")
        ticks = iter(range(0, 1000, 20))
        lines = dep_audit.audit(self.root, ["error: libBigWig"], "aarch64", budget=30, clock=lambda: next(ticks))
        self.assertTrue(any(line.startswith("partial:") for line in lines) or lines == [])

    def test_no_dependencies_and_internal_failure_give_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(dep_audit.render(Path(temp), ["error: x"], "aarch64"), "")
        with patch.object(dep_audit, "audit", side_effect=zlib.error("boom")):
            self.assertEqual(dep_audit.render(self.root, ["error: x"], "aarch64"), "")


class Search(unittest.TestCase):
    def test_no_hundred_file_cap_and_count_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            src = root / "input/src"
            src.mkdir(parents=True)
            for n in range(150):
                (src / f"f{n:03d}.c").write_text("needle\n" if n == 149 else "x\n")
            (root / "input/.pc").mkdir()
            (root / "input/.pc/hidden.c").write_text("needle\n")
            (root / "input/obj-x86_64").mkdir()
            (root / "input/obj-x86_64/gen.c").write_text("needle\n")
            result = tools.run_tool(ToolRequest("search", {"regex": "needle", "path_glob": "input/**/*.c"}), root)
        self.assertIn("input/src/f149.c:1:needle", result.text)
        self.assertIn("scanned 150 of 150 matching files", result.text)
        self.assertNotIn("hidden.c", result.text)
        self.assertNotIn("gen.c", result.text)
        self.assertFalse(result.truncated)

    def test_budget_stop_names_the_first_unscanned_file_and_sources_rank_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input/docs").mkdir(parents=True)
            (root / "input/docs/a.txt").write_text("needle\n")
            (root / "input/z.c").write_text("needle\n")
            (root / "input/y.c").write_text("needle\n")
            with patch.object(tools, "MAX_SEARCH_BYTES", 1):
                result = tools.run_tool(ToolRequest("search", {"regex": "needle", "path_glob": "input/**"}), root)
        self.assertIn("input/y.c:1:needle", result.text)       # source before docs
        self.assertIn("scanned 1 of 3 matching files", result.text)
        self.assertIn("first unscanned: input/z.c", result.text)
        self.assertTrue(result.truncated)

    def test_binary_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input").mkdir()
            (root / "input/a.bin").write_bytes(b"needle\0\x01")
            result = tools.run_tool(ToolRequest("search", {"regex": "needle", "path_glob": "input/*"}), root)
        self.assertIn("skipped files: 1", result.text)
        self.assertNotIn("a.bin:1", result.text)


class RunToolSafety(unittest.TestCase):
    def test_a_tool_raising_zlib_error_is_a_result(self):
        for error in (zlib.error("bad stream"), EOFError("short"), KeyError("k")):
            with tempfile.TemporaryDirectory() as temp, \
                    patch.object(tools, "_run_tool", side_effect=error):
                result = tools.run_tool(ToolRequest("list_dir", {"path": "input"}), Path(temp))
            self.assertIn("=== list_dir: unavailable (" + type(error).__name__ + ":", result.text)
            self.assertFalse(result.truncated)


class KeepEdits(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ws = Path(temp.name)
        (self.ws / "input").mkdir()
        (self.ws / "input/task.json").write_text(json.dumps({"case_id": "c", "target_arch": "aarch64"}))
        (self.ws / "input/initial-build.log").write_text("error: boom\n")
        self.src = self.ws / "work/repo/input/a.c"
        self.src.parent.mkdir(parents=True)
        self.src.write_text("old\n")

    def run_main(self, fake):
        env = {"BB_WORKSPACE": str(self.ws), "BB_MODEL_API_KEY": "k"}
        with patch.dict(os.environ, env), patch.object(agent_main, "run_agent", fake), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return agent_main.main()

    def test_error_after_an_edit_keeps_the_worktree_and_reports_completed(self):
        def fake(context, *args, **kwargs):
            self.src.write_text("new\n")
            raise RuntimeError("model 503 after the edit")
        code = self.run_main(fake)
        result = json.loads((self.ws / "output/agent-result.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["error"]["error_type"], "RuntimeError")
        self.assertEqual(self.src.read_text(), "new\n")
        self.assertFalse((self.ws / "output/agent-error.json").exists())

    def test_error_without_an_edit_keeps_the_old_behaviour(self):
        def fake(context, *args, **kwargs):
            raise RuntimeError("no edit yet")
        code = self.run_main(fake)
        self.assertEqual(code, 1)
        self.assertTrue((self.ws / "output/agent-error.json").exists())
        self.assertFalse((self.ws / "output/agent-result.json").exists())
        self.assertEqual(self.src.read_text(), "old\n")


if __name__ == "__main__":
    unittest.main()
