"""Fix-type classification and build-time command flagging.

The LIVE_* diffs are the three real repairs gpt-oss-120b produced for rcran on
3 October (docs/part5-findings.md section 9), with their real tab indentation.

Run from the repository root:  python3 -m unittest discover -s harness/tests -t .
"""

import json
from pathlib import Path
import tempfile
import unittest

from harness.diff_scan import scan_diff
from harness.summarize import load_rows, summarize


LIVE_1 = """--- a/input/debian/rules
+++ b/input/debian/rules
@@ -6,6 +6,10 @@
 %:
 \tdh $@ --buildsystem R

+override_dh_auto_configure:
+\tdh_auto_configure --buildsystem=R
+\tsed -i 's/#include <xmmintrin.h>/#if defined(__x86_64__) || defined(__i386__)\\n#include <xmmintrin.h>\\n#endif/' src/crc32c/crc32c_prefetch.h
+
 override_dh_auto_install:
 \tdh_auto_install -- prefix=/usr
 \trm debian/r-cran-digest/usr/lib/R/site-library/digest/doc/sha1.html
"""

LIVE_2 = """--- a/input/debian/rules
+++ b/input/debian/rules
@@ -9,3 +9,9 @@
 override_dh_auto_install:
 \tdh_auto_install -- prefix=/usr
 \trm debian/r-cran-digest/usr/lib/R/site-library/digest/doc/sha1.html
+
+override_dh_auto_build:
+\tsed -i 's/#include <xmmintrin.h>/\\/\\/#include <xmmintrin.h>/' src/crc32c/crc32c_prefetch.h
+\tdh_auto_build
+\tdh_auto_install -- prefix=/usr
+\trm debian/r-cran-digest/usr/lib/R/site-library/digest/doc/sha1.html
"""

LIVE_3 = """--- a/input/debian/rules
+++ b/input/debian/rules
@@ -6,6 +6,14 @@
 %:
 \tdh $@ --buildsystem R

+override_dh_auto_build:
+\tdh_auto_build
+\t# The upstream source unconditionally includes <xmmintrin.h>, which is not available on aarch64.
+\t# Comment out that include to allow the portable implementation to compile.
+\tif [ -f src/crc32c/crc32c_prefetch.h ]; then \\
+\t\tsed -i 's/#include <xmmintrin.h>/\\/\\/ #include <xmmintrin.h>/' src/crc32c/crc32c_prefetch.h; \\
+\tfi
+
 override_dh_auto_install:
"""

SOURCE_FIX = """--- a/input/src/crc32c/crc32c_config.h
+++ b/input/src/crc32c/crc32c_config.h
@@ -12,7 +12,11 @@
 // Define to 1 if targeting X86 and the compiler has the _mm_prefetch intrinsic.
+#if defined(__x86_64__) || defined(__i386__)
 #define HAVE_MM_PREFETCH 1
+#else
+#define HAVE_MM_PREFETCH 0
+#endif
"""

PACKAGING_FIX = """--- a/input/debian/control
+++ b/input/debian/control
@@ -2,2 +2,3 @@
 Build-Depends: debhelper-compat (= 13),
-               libssl-dev
+               libssl-dev,
+               zlib1g-dev
"""


def rules_diff(*added):
    return ("--- a/input/debian/rules\n+++ b/input/debian/rules\n@@ -1,1 +1,2 @@\n %:\n"
            + "".join(f"+{line}\n" for line in added))


class RealRcranRepairs(unittest.TestCase):
    def test_live_1_is_flagged(self):
        result = scan_diff(LIVE_1)
        self.assertEqual(result["fix_type"], "build_commands")
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["changed_files"], ["input/debian/rules"])
        self.assertEqual(len(result["build_commands"]), 1)
        self.assertIn("sed -i", result["build_commands"][0])

    def test_live_2_flags_the_sed_and_the_duplicated_rm(self):
        commands = scan_diff(LIVE_2)["build_commands"]
        self.assertEqual(len(commands), 2)
        self.assertIn("sed -i", commands[0])
        self.assertIn("rm debian/r-cran-digest", commands[1])

    def test_live_3_flags_the_sed_inside_an_if_and_skips_comments(self):
        commands = scan_diff(LIVE_3)["build_commands"]
        self.assertEqual(len(commands), 1)
        self.assertIn("sed -i", commands[0])


class FixTypes(unittest.TestCase):
    def test_source_edit_needs_no_review(self):
        result = scan_diff(SOURCE_FIX)
        self.assertEqual((result["fix_type"], result["needs_review"]), ("source", False))
        self.assertEqual(result["changed_files"], ["input/src/crc32c/crc32c_config.h"])

    def test_packaging_only(self):
        self.assertEqual(scan_diff(PACKAGING_FIX)["fix_type"], "packaging")

    def test_rules_edit_without_commands_is_packaging(self):
        self.assertEqual(scan_diff(rules_diff("export DEB_CFLAGS_MAINT_APPEND = -O2"))["fix_type"],
                         "packaging")

    def test_source_and_packaging_together_is_source(self):
        self.assertEqual(scan_diff(SOURCE_FIX + PACKAGING_FIX)["fix_type"], "source")

    def test_no_change(self):
        self.assertEqual(scan_diff(""), {"fix_type": "none", "changed_files": [],
                                         "build_commands": [], "needs_review": False})


class CommandDetection(unittest.TestCase):
    FLAGGED = [
        "\techo '#define X 1' > src/config.h",
        "\tcat extra.h >> src/config.h",
        "\tperl -pi -e 's/a/b/' src/a.c",
        "\tsed --in-place 's/a/b/' src/a.c",
        "\t-rm -rf tests",
        "\t@mv src/a.c src/b.c",
        "\tpatch -p1 < debian/fix.patch",
        "\tdh_auto_test 2>&1 | tee test.log",
        "\ttrue && cp src/a.h src/b.h",
    ]
    NOT_FLAGGED = [
        "\tdh_auto_configure --buildsystem=R",
        "\t$(MAKE) check 2>&1",
        "\techo done >&2",
        "\tsed 's/a/b/' src/a.c > /dev/null",
        "\tsed -e 's/a/b/' src/a.c",
        "\t# rm -rf tests (a comment)",
        "\tdh_auto_install -- prefix=/usr",
        "export DEB_BUILD_MAINT_OPTIONS = hardening=+all",
    ]

    def test_file_modifying_commands_are_flagged(self):
        for line in self.FLAGGED:
            with self.subTest(line=line):
                self.assertEqual(scan_diff(rules_diff(line))["fix_type"], "build_commands")

    def test_harmless_lines_are_not_flagged(self):
        for line in self.NOT_FLAGGED:
            with self.subTest(line=line):
                self.assertEqual(scan_diff(rules_diff(line))["build_commands"], [])

    def test_only_build_files_are_scanned(self):
        c_code = ("--- a/input/src/a.c\n+++ b/input/src/a.c\n@@ -1 +1,2 @@\n x\n"
                  "+if (a > b) rm(x); // sed -i\n")
        self.assertEqual(scan_diff(c_code)["fix_type"], "source")

    def test_only_added_lines_are_scanned(self):
        # live-1's context lines include an existing `rm`; only added lines count.
        self.assertEqual(len(scan_diff(LIVE_1)["build_commands"]), 1)


class Summary(unittest.TestCase):
    def test_counts_by_fix_type_and_review_list(self):
        rows = [
            {"run_id": "a", "repaired": True, **scan_diff(LIVE_1)},
            {"run_id": "b", "repaired": True, **scan_diff(SOURCE_FIX)},
            {"run_id": "c", "repaired": False, **scan_diff("")},
        ]
        s = summarize(rows)
        self.assertEqual((s["runs"], s["repaired"]), (3, 2))
        self.assertEqual(s["needs_review"], ["a"])
        pick = lambda g: (g["runs"], g["repaired"])
        self.assertEqual(pick(s["by_fix_type"]["build_commands"]), (1, 1))
        self.assertEqual(pick(s["by_fix_type"]["none"]), (1, 0))

    def test_older_rows_are_classified_from_their_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            diff = Path(tmp) / "repair.diff"
            diff.write_text(LIVE_2)
            runs = Path(tmp) / "runs.jsonl"
            runs.write_text(json.dumps({"run_id": "live-2", "repaired": True,
                                        "diff_path": str(diff)}) + "\n")
            row = load_rows(runs)[0]
        self.assertEqual(row["fix_type"], "build_commands")
        self.assertTrue(row["needs_review"])


if __name__ == "__main__":
    unittest.main()
