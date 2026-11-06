"""Test-suite failures (test_failures.py): the failing tests and why, from the log.

Each excerpt keeps the shape of a real public development case's log (named in
the test), shortened. No model, network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import test_failures as tf  # noqa: E402
from src.contracts import AgentConfig, Budget  # noqa: E402
from src.model_adapter import ModelAdapter  # noqa: E402
from src.types import CaseContext  # noqa: E402

START = "   dh_auto_test -a -O--buildsystem=pybuild\n"
END = ("make[1]: *** [debian/rules:35: override_dh_auto_test] Error 25\n"
       "dh_auto_test: error: pybuild --test returned exit code 13\n")

UNITTEST = START + """test_link (test_strace.TestStrace.test_link) ... FAIL
======================================================================
FAIL: test_link (test_strace.TestStrace.test_link)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/<<PKGBUILDDIR>>/tests/test_strace.py", line 91, in test_link
    self.assert_syscall("import os; os.link('oldpath', 'newpath')", pattern)
  File "/<<PKGBUILDDIR>>/tests/test_strace.py", line 41, in assert_syscall
    self.assertTrue(pattern.search(stdout), stdout)
AssertionError: None is not true : b'linkat(AT_FDCWD, "oldpath", AT_FDCWD, "newpath", 0) = 0'
----------------------------------------------------------------------
Ran 12 tests in 0.4s

FAILED (failures=1)
""" + END

PYTEST = START + """________________________ test_multiple_contexts ________________________

    def test_multiple_contexts():
>       assert counter.count == 2
E       assert 3 == 2
E        +  where 3 = <Counter>.count

test/test_stack_sampler.py:121: AssertionError
=========================== short test summary info ============================
FAILED test/test_stack_sampler.py::test_multiple_contexts - assert 3 == 2
======================= 1 failed, 40 passed in 2.31s ========================
""" + END

GO = """   dh_auto_test -O--buildsystem=golang
=== RUN   TestLongRequestHeader
--- FAIL: TestLongRequestHeader (0.01s)
    server_test.go:88: header too long: got 413, want 200
=== RUN   TestOther
--- PASS: TestOther (0.00s)
FAIL
FAIL\tv2ray.com/core/transport/internet/http\t0.012s
dh_auto_test: error: cd obj-aarch64-linux-gnu && go test -vet=off -v -p 4 v2ray.com/core returned exit code 1
"""

CTEST = """   dh_auto_test -a
7: [ RUN      ] Elliptic.Arc
7: /<<PKGBUILDDIR>>/tests/elliptical-arc-test.cpp:211: Failure
7: Expected equality of these values:
7:   a.center()
7:     Which is: (1.0000000000000002, 0)
7: [  FAILED  ] Elliptic.Arc (1 ms)
 7/42 Test  #7: elliptical-arc-test ..............***Failed    0.02 sec

The following tests FAILED:
\t  7 - elliptical-arc-test (Failed)
Errors while running CTest
make[1]: *** [debian/rules:20: override_dh_auto_test] Error 8
dh_auto_test: error: cd obj-aarch64-linux-gnu && make -j4 test ARGS+=--verbose returned exit code 2
"""

AUTOMAKE_ASSERT = """   dh_auto_test -a
PASS: unit-test
Error: test/lib/server.c:16: assertion failed: *fd != -1 (-1 != -1)
Error: child killed by signal 6 (Aborted)
FAIL: integration-test
# TOTAL: 2
# FAIL:  1
make[4]: *** [Makefile:2699: test-suite.log] Error 1
make[3]: *** [Makefile:2807: check-TESTS] Error 2
dh_auto_test: error: make -j4 check VERBOSE=1 returned exit code 2
"""

LUA_CRASH = """   dh_auto_test -a
ok 97 thread - thread_getcpu
ok 98 thread - getaffinity, setaffinity
default priority\t0
Segmentation fault (core dumped)
make[2]: *** [/usr/share/dh-lua/make/dh-lua.Makefile.single:343: test-app-dynamic-real] Error 139
dh_auto_test: error: make --no-print-directory -f /usr/share/dh-lua/make/dh-lua.Makefile.multiple test returned exit code 2
"""

HANG = """   dh_auto_test -O--buildsystem=pybuild
collected 28 items

tests/test_benchmark.py ....                                             [ 14%]
tests/test_fuzzer.py .
E: Build killed with signal TERM after 150 minutes of inactivity
"""

LIT = """   dh_auto_test -a
Failed Tests (2):
  LLVM_SPIRV :: transcoding/OpGroupAsyncCopy.ll
  LLVM_SPIRV :: spirv-tools-dis.ll

Testing Time: 14.05s
  Passed           : 635
  Failed           :   2
make[5]: *** [test/CMakeFiles/check-llvm-spirv.dir/build.make:74: test/CMakeFiles/check-llvm-spirv] Error 1
dh_auto_test: error: cd obj-aarch64-linux-gnu && make -j4 check-llvm-spirv returned exit code 2
"""

# rust-blake3: not a test at all, but a tool in the test step that says what it needs.
TOOL = """   dh_auto_test -a
/usr/share/cargo/bin/dh-cargo-built-using: abort: could not determine source-distribution conditions of libblake3_neon.a.
You must patch build.rs of blake3 to output 'println!("dh-cargo:deb-built-using=blake3_neon=$s={}", ...);'
dh_auto_test: error: env CARGO_CHANNEL=debug /usr/share/cargo/bin/dh-cargo-built-using returned exit code 1
"""

COMPILE_ONLY = """   dh_auto_build -a
src/x.c:3:10: fatal error: immintrin.h: No such file or directory
make[1]: *** [Makefile:10: x.o] Error 1
dh_auto_build: error: make -j4 returned exit code 2
"""


class Logs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = self.root / "initial-build.log"

    def extract(self, text):
        # The validator's elapsed-time prefix and colour codes are removed first.
        self.log.write_text("".join(f"[  {n:3d}s] {line}\n" for n, line in enumerate(text.splitlines())))
        return tf.extract(tf.read_log(self.log))

    def names(self, text):
        return [(f.framework, f.name) for f in self.extract(text)[1]]

    def test_python_unittest_with_the_assertion_and_the_test_frame(self):
        _, failures, _ = self.extract(UNITTEST)
        self.assertEqual([(f.framework, f.name) for f in failures],
                         [("python-unittest", "test_strace.TestStrace.test_link")])
        self.assertIn("AssertionError: None is not true", failures[0].details[-1])
        self.assertIn(("/<<PKGBUILDDIR>>/tests/test_strace.py", 41), failures[0].locations)

    def test_pytest_summary_and_the_E_lines(self):
        _, failures, _ = self.extract(PYTEST)
        self.assertEqual(failures[0].name, "test/test_stack_sampler.py::test_multiple_contexts")
        self.assertIn("E       assert 3 == 2", failures[0].details)
        self.assertIn(("test/test_stack_sampler.py", 121), failures[0].locations)

    def test_go_test_failure_with_its_message(self):
        _, failures, _ = self.extract(GO)
        self.assertEqual([f.name for f in failures], ["TestLongRequestHeader"])
        self.assertIn("server_test.go:88: header too long: got 413, want 200", failures[0].details)

    def test_ctest_list_with_the_googletest_failure(self):
        _, failures, _ = self.extract(CTEST)
        self.assertEqual(failures[0].name, "elliptical-arc-test (Failed)")
        self.assertTrue(any("Which is: (1.0000000000000002, 0)" in d for d in failures[0].details))
        self.assertIn(("/<<PKGBUILDDIR>>/tests/elliptical-arc-test.cpp", 211), failures[0].locations)

    def test_automake_name_and_the_assertion_line(self):
        _, failures, salient = self.extract(AUTOMAKE_ASSERT)
        self.assertEqual([f.name for f in failures], ["integration-test"])
        self.assertTrue(any("server.c:16: assertion failed" in t for _, t in salient))

    def test_lit_failed_tests(self):
        self.assertEqual(self.names(LIT), [("llvm-lit", "LLVM_SPIRV :: transcoding/OpGroupAsyncCopy.ll"),
                                           ("llvm-lit", "LLVM_SPIRV :: spirv-tools-dis.ll")])

    def test_no_test_step_no_region(self):
        self.assertEqual(self.extract(COMPILE_ONLY), (None, [], []))

    def test_rendering_a_crash_names_the_last_passing_test(self):
        self.log.write_text(LUA_CRASH)
        block = tf.render(self.log, None, "x86_64")
        self.assertIn("Segmentation fault (core dumped)", block)
        self.assertIn("ok 98 thread - getaffinity, setaffinity", block)
        self.assertIn("fails only on x86_64", block)
        self.assertIn("forbid disabling, skipping or deleting the test", block)

    def test_rendering_a_hang_names_the_test_that_was_running(self):
        self.log.write_text(HANG)
        block = tf.render(self.log, None)
        self.assertIn("hung or deadlocked test", block)
        self.assertIn("tests/test_fuzzer.py .", block)

    def test_a_tool_that_states_its_fix_is_shown(self):
        self.log.write_text(TOOL)
        self.assertIn("You must patch build.rs of blake3", tf.render(self.log, None))

    def test_a_compile_error_inside_the_test_step_is_not_a_test_failure(self):
        # Debian's Rust packaging builds the crate in dh_auto_test (rust-apr and others).
        self.log.write_text("   dh_auto_test -O--buildsystem=cargo\n"
                            "error[E0308]: mismatched types\n  --> src/lib.rs:10:5\n"
                            "error: could not compile `apr` due to previous error\n"
                            "dh_auto_test: error: /usr/share/cargo/bin/cargo build returned exit code 101\n")
        self.assertEqual(tf.render(self.log, None), "")

    def test_no_test_failure_no_block(self):
        self.log.write_text(COMPILE_ONLY)
        self.assertEqual(tf.render(self.log, None), "")
        self.assertEqual(tf.render(None, None), "")
        self.assertEqual(tf.render(self.root / "missing.log", None), "")

    def test_the_block_is_bounded(self):
        self.log.write_text(START + "".join(f"FAIL: test_{n} (pkg.tests.T.test_{n})\n"
                                            + "-" * 70 + "\nAssertionError: " + "x" * 5000 + "\n"
                                            for n in range(200)) + END)
        block = tf.render(self.log, None)
        self.assertLessEqual(len(block), tf.MAX_BLOCK_CHARS + 100)
        self.assertTrue(block.endswith(tf.END))


class Source(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        tests = self.root / "input/tests"
        tests.mkdir(parents=True)
        (tests / "test_strace.py").write_text("".join(f"line {n}\n" for n in range(1, 100)))
        (self.root / "input/teststruct.lua").write_text("".join(f"check {n}\n" for n in range(1, 300)))
        (self.root / "input/other").mkdir()
        (self.root / "input/other/test_strace.py").write_text("decoy\n")
        self.log = self.root / "initial-build.log"

    def test_resolution(self):
        files = ["input/tests/test_strace.py", "input/other/test_strace.py", "input/pkg/tests/test_mod.py",
                 "input/teststruct.lua"]
        self.assertEqual(tf.resolve("/<<PKGBUILDDIR>>/tests/test_strace.py", files), "input/tests/test_strace.py")
        self.assertIsNone(tf.resolve("test_strace.py", files))          # two candidates
        self.assertEqual(tf.resolve("pkg.tests.test_mod.Case.test_x", files), "input/pkg/tests/test_mod.py")
        self.assertEqual(tf.resolve("teststruct.lua", files), "input/teststruct.lua")
        self.assertIsNone(tf.resolve("/usr/lib/python3/dist-packages/x.py", files))

    def test_the_failing_line_of_the_test_is_shown(self):
        self.log.write_text(UNITTEST)
        block = tf.render(self.log, self.root, "aarch64")
        self.assertIn("source: input/tests/test_strace.py line 41", block)
        self.assertIn("--- input/tests/test_strace.py, lines 33-49 ---", block)
        self.assertIn("   41> line 41", block)

    def test_a_location_in_an_assertion_line_is_shown(self):
        self.log.write_text("   dh_auto_test -a\nlua5.1: teststruct.lua:234: assertion failed!\n"
                            "make[1]: *** [debian/rules:9: override_dh_auto_test] Error 1\n")
        block = tf.render(self.log, self.root)
        self.assertIn("--- input/teststruct.lua, lines 226-242 ---", block)
        self.assertIn("  234> check 234", block)


class Wiring(unittest.TestCase):
    def test_the_opening_message_carries_the_block(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input").mkdir()
            log = root / "initial-build.log"
            log.write_text(GO)
            context = CaseContext("c", root, "", {"target_arch": "x86_64"})
            adapter = ModelAdapter(lambda messages: ("", "stop", {}), context,
                                   config=AgentConfig(use_build_feedback=False), budget=Budget(),
                                   diagnostics=[], log_path=log)
            self.assertIn(tf.TITLE, adapter.pattern)
            self.assertIn("TestLongRequestHeader", adapter.pattern)
            off = ModelAdapter(lambda messages: ("", "stop", {}), context,
                               config=AgentConfig(use_build_feedback=False, use_evidence_manager=False),
                               budget=Budget(), diagnostics=[], log_path=log)
            self.assertEqual(off.pattern, "")  # the baseline arm is unchanged


if __name__ == "__main__":
    unittest.main()
