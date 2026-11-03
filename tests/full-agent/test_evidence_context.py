"""Part A: diagnostic context added 1 November 2026; no model, network or build.

From the Demo 2 retest: on ectrans the model was shown only generic
"make: *** ... Error 1" lines, because gfortran's error was not recognized
(docs/evidence/demo2/paired-retest/full-agent/03-*ectrans*/record.json, builds
2, 3, 5, 7 and 8). The gfortran lines below are the audit's real lines 1797 and 1799 of
build-02; the location line before them is not in the audit and is written
in gfortran's standard form for the same file and line.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
from src import evidence
from src.evidence import COMMAND_CHARS, diagnose

HELLO_REPAIRED_LOG = ROOT / "tests/one-shot/part2-log-tail/fixtures/build.log"

ECTRANS = """\
[   60s] dpkg-buildpackage: info: source package ectrans
[   88s]    dh_auto_build
[   90s] [ 40%] Building Fortran object src/trans/CMakeFiles/trans_dp.dir/algor/bluestein_mod.F90.o
[   90s] /usr/src/packages/BUILD/src/trans/algor/bluestein_mod.F90:20:5:
[   90s]
[   90s]    20 | USE PARKIND1, ONLY : JPIM, JPRB
[   90s]       |     1
[   90s] Fatal Error: Cannot open module file 'parkind1.mod' for reading at (1): No such file or directory
[   90s] compilation terminated.
[   90s] make[3]: *** [src/trans/CMakeFiles/trans_dp.dir/build.make:105: src/trans/CMakeFiles/trans_dp.dir/algor/bluestein_mod.F90.o] Error 1
[   90s] make[2]: *** Waiting for unfinished jobs....
[   90s] dh_auto_build: error: cd debian/build && make -j2 "INSTALL=install --strip-program=true" returned exit code 2
[   90s] make: *** [debian/rules:37: build] Error 25
[   90s] dpkg-buildpackage: error: debian/rules build subprocess returned exit status 2
"""


class Context(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = self.root / "build.log"

    def put(self, relative, text="x\n"):
        path = self.root / "input" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def parse(self, text):
        self.log.write_text(text, encoding="utf-8")
        return diagnose(self.log, self.root)


class Fortran(Context):
    def setUp(self):
        super().setUp()
        self.put("src/trans/algor/bluestein_mod.F90",
                 "MODULE BLUESTEIN_MOD\nUSE PARKIND1, ONLY : JPIM, JPRB\nEND MODULE\n")

    def test_ectrans_fortran_error_comes_first_with_its_file(self):
        result = self.parse(ECTRANS)
        first = result[0]
        self.assertTrue(first.message.startswith("Fatal Error: Cannot open module file 'parkind1.mod'"))
        self.assertEqual(first.line, 20)
        self.assertEqual(first.resolved, ["input/src/trans/algor/bluestein_mod.F90"])
        self.assertEqual(first.source_line, "USE PARKIND1, ONLY : JPIM, JPRB")
        self.assertEqual(first.stage, "build")
        # The generic wrappers follow, in the order printed.
        self.assertEqual([d.message.split(":")[0] for d in result[1:]],
                         ["make[3]", "dh_auto_build", "make", "dpkg-buildpackage"])

    def test_missing_module_is_said_to_come_from_outside_the_package(self):
        details = self.parse(ECTRANS)[0].compiler_details
        self.assertIn("None of the 1 Fortran files under input/ defines module 'parkind1'", details)
        self.assertIn("build dependency", details)
        self.assertIn("generated during the build", details)
        self.assertIn("Building Fortran object", details)

    def test_a_stopped_search_does_not_claim_absence(self):
        self.put("src/a.f90", "program a\nend\n")
        self.put("src/z/parkind1.f90", "module parkind1\nend module\n")
        with patch.object(evidence, "MODULE_SEARCH_FILES", 2):
            details = self.parse(ECTRANS)[0].compiler_details
        self.assertIn("not found in the first 2 Fortran files", details)
        self.assertNotIn("expected from a build dependency", details)

    def test_module_search_handles_crlf_and_long_blank_runs(self):
        self.put("src/k.f90", "\n" * 200_000 + "MODULE Parkind1\r\nEND MODULE\r\n")
        details = self.parse(ECTRANS)[0].compiler_details
        self.assertIn("is defined in this package: input/src/k.f90", details)

    def test_location_without_column_is_recognized(self):
        result = self.parse("src/trans/algor/bluestein_mod.F90:20:\n\n"
                            "Error: Symbol 'x' at (1) has no IMPLICIT type\n")
        self.assertEqual(result[0].resolved, ["input/src/trans/algor/bluestein_mod.F90"])

    def test_module_defined_in_the_package_is_named(self):
        self.put("src/utils/parkind1.F90", "! kinds\n  module parkind1  ! comment\nend module\n")
        details = self.parse(ECTRANS)[0].compiler_details
        self.assertIn("is defined in this package: input/src/utils/parkind1.F90", details)

    def test_module_procedure_is_not_a_module_definition(self):
        self.put("src/x.f90", "interface parkind1\n  module procedure parkind1\nend interface\n")
        self.assertIn("None of the 2 Fortran files", self.parse(ECTRANS)[0].compiler_details)

    def test_absurd_line_numbers_do_not_crash(self):
        result = self.parse("a.f90:" + "9" * 5000 + ":1:\n" + "9" * 5000 + " | x\n"
                            "src/a.c:1:1: error: still found\n")
        self.assertEqual([d.message for d in result], ["error: still found"])

    def test_error_without_location_is_still_reported(self):
        result = self.parse("Fatal Error: Cannot open module file 'x.mod' for reading at (1)\n")
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].log_path)

    def test_location_line_without_an_error_is_ignored(self):
        result = self.parse("src/a.f90:3:1:\n\n    3 | x = 1\n      | 1\n"
                            "Warning: Nonconforming tab character at (1)\n"
                            "src/b.c:1:1: error: real error\n")
        self.assertEqual([d.message for d in result], ["error: real error"])


class Commands(Context):
    def test_compiler_error_carries_its_command(self):
        result = self.parse(
            "gcc -O2 -Werror=format-security -c crc32c/crc32c.cc -o crc32c/crc32c.o\n"
            "crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: No such file or directory\n"
            "   18 | #include <xmmintrin.h>\n")
        details = result[0].compiler_details
        self.assertIn("gcc -O2 -Werror=format-security -c crc32c/crc32c.cc", details)
        self.assertIn("No file named 'xmmintrin.h' exists under input/", details)
        self.assertIn("or generated during the build", details)

    def test_header_present_in_the_package_is_named(self):
        self.put("include/foo/bar.h")
        result = self.parse("src/a.c:1:10: fatal error: foo/bar.h: No such file or directory\n")
        self.assertIn("exists in this package: input/include/foo/bar.h", result[0].compiler_details)

    def test_linker_error_carries_the_link_command(self):
        # libunwind (development-v1): the real linker line, after a libtool link command.
        line = ("/bin/ld: .libs/Lperf-simple: hidden symbol `__aarch64_cas8_acq_rel' in "
                "/usr/lib/gcc/aarch64-linux-gnu/14/libgcc.a(cas_8_4.o) is referenced by DSO")
        result = self.parse("libtool: link: gcc -g -O2 -o .libs/Lperf-simple Lperf-simple.o "
                            "../src/.libs/libunwind.so\n" + f"[  151s] {line}\n")
        self.assertEqual(result[0].message, line)
        self.assertIn("libtool: link: gcc -g -O2 -o .libs/Lperf-simple", result[0].compiler_details)

    def test_cross_compiler_and_absolute_tool_paths(self):
        for command in ("aarch64-linux-gnu-gcc-13 -c x.c", "/usr/bin/c++ -c y.cc",
                        "cd /b/obj && /usr/bin/gfortran -c z.f90", "  CC       src/x.lo"):
            with self.subTest(command=command):
                result = self.parse(command + "\nx.c:1:1: error: bad\n")
                self.assertIn(" ".join(command.split()), result[0].compiler_details)

    def test_long_commands_are_clipped(self):
        result = self.parse("gcc " + "-Dx " * 500 + "-c a.c\na.c:1:1: error: bad\n")
        command = result[0].compiler_details.split(": ", 1)[1]
        self.assertLessEqual(len(command), COMMAND_CHARS)

    def test_no_command_means_no_command_detail(self):
        result = self.parse("a.c:1:1: error: bad\n")
        self.assertNotIn("compile or link step", result[0].compiler_details)

    def test_compiler_diagnostics_about_the_tool_are_not_commands(self):
        result = self.parse("gcc: error: unrecognized command-line option '-mavx'\n")
        self.assertEqual(result[0].message, "gcc: error: unrecognized command-line option '-mavx'")


class Paths(Context):
    def test_absolute_build_root_paths_resolve(self):
        self.put("src/x.c")
        for root in ("/usr/src/packages/BUILD", "/<<PKGBUILDDIR>>",
                     # Real layouts: the dataset's sbuild placeholder (evidence-report.json,
                     # liboqs), sbuild's two-level root, rpm 4.20's extra level (hello log).
                     "/<<BUILDDIR>>/liboqs-0.7.0.27.gba5b61a7+dfsg",
                     "/build/liboqs-AbC123/liboqs-0.7.0", "/build/pkg-1.0",
                     "/home/abuild/rpmbuild/BUILD/buildbench-hello-1.0-build/buildbench-hello-1.0",
                     "/home/abuild/rpmbuild/BUILD/pkg-1.0-build"):
            with self.subTest(root=root):
                result = self.parse(f"{root}/src/x.c:1:1: error: bad {root}\n")
                self.assertEqual(result[0].resolved, ["input/src/x.c"])

    def test_other_absolute_paths_still_do_not_resolve(self):
        self.put("x.h")
        result = self.parse("/usr/include/x.h:1:1: error: bad\n")
        self.assertEqual(result[0].resolved, [])


class Ordering(Context):
    def test_diagnostics_stay_in_the_order_printed(self):
        # The prompt titles the list "(in the order printed)" and the loop compares [0].
        text = ("make[2]: *** [all] Error 1\nsrc/a.c:1:1: error: real\n"
                "dh_auto_test: error: make -j4 check returned exit code 2\n")
        self.assertEqual([d.message for d in self.parse(text)],
                         ["make[2]: *** [all] Error 1", "error: real",
                          "dh_auto_test: error: make -j4 check returned exit code 2"])


class SetupNoise(Context):
    def test_successful_rpm_hello_log_has_no_diagnostics(self):
        # It printed "ERROR: not all operations were successful." while setting up the build root.
        self.assertEqual(diagnose(HELLO_REPAIRED_LOG, self.root), [])

    def test_errors_before_the_debian_build_starts_are_dropped(self):
        result = self.parse("ERROR: not all operations were successful.\n"
                            "dpkg-buildpackage: info: source package foo\n"
                            "src/a.c:1:1: error: real\n")
        self.assertEqual([d.message for d in result], ["error: real"])

    def test_without_a_start_marker_nothing_is_dropped(self):
        # An excerpt may not include the start of the package build.
        result = self.parse("ERROR: something during setup\nsrc/a.c:1:1: error: real\n")
        self.assertEqual(len(result), 2)

    def test_an_error_repeated_inside_the_build_is_kept(self):
        result = self.parse("ERROR: foo failed\ndpkg-buildpackage: info: source package x\n"
                            "ERROR: foo failed\n")
        self.assertEqual([d.message for d in result], ["ERROR: foo failed"])

    def test_a_later_final_dpkg_error_is_not_a_start_marker(self):
        result = self.parse("src/a.c:1:1: error: real\n"
                            "dpkg-buildpackage: error: debian/rules build subprocess returned exit status 2\n")
        self.assertEqual(result[0].message, "error: real")


if __name__ == "__main__":
    unittest.main()


class CrossLinker(unittest.TestCase):
    """pybigwig after the QEMU fix (docs/evidence/qemu-fix-20261102): the linker
    is x86_64-linux-gnu-ld.bfd, and its specific error had been missed."""

    LOG = (ROOT / "docs/evidence/qemu-fix-20261102/x86-original-qemu10-20261102T022142Z/"
           "launchpad-resolute-arm64-amd64-pybigwig-56ac842288ce/build.log")

    def test_the_relocation_error_is_the_first_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = diagnose(self.LOG, Path(tmp))
        self.assertTrue(result[0].message.startswith("/usr/bin/x86_64-linux-gnu-ld.bfd: "))
        self.assertIn("recompile with -fPIC", result[0].message)
        # Its warning on the line before is not an error.
        self.assertFalse(any("warning: relocation" in d.message for d in result))

    def test_linker_warnings_naming_an_object_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "build.log"
            log.write_text("/usr/bin/ld: x.o: warning: missing .note.GNU-stack section\n"
                           "/usr/bin/aarch64-linux-gnu-ld: cannot find -lfoo\n")
            self.assertEqual([d.message for d in diagnose(log, Path(tmp))],
                             ["/usr/bin/aarch64-linux-gnu-ld: cannot find -lfoo"])
