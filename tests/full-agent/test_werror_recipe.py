"""0.6.0-rc.8: the wno-error recipe (a newer GCC warning stops a -Werror build).

Shapes from the rc.5 Development Validation run: liboqs (jammy, arm64,
-Werror=array-parameter) and bzrtp (plucky, amd64, -Werror=stringop-overflow)
were edited one file at a time and failed on the next file with the same
warning. wlrctl (-Werror=type-limits, a char-signedness bug) must not match.
No model, network, compiler or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import patterns, rules_guard, syntax_guard  # noqa: E402
from src.recipes import registry  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "recipes"

FLAGS = ("-g -O2 -ffile-prefix-map=/<<PKGBUILDDIR>>=. -fstack-protector-strong "
         "-Wformat -Werror=format-security -Wall -Werror")
LIBOQS_LOG = [
    "dpkg-buildpackage: info: host architecture arm64",
    f"[12/300] /usr/bin/cc -DOQS_EXPORTS {FLAGS} -c src/kem/kyber/kem.c",
    "/<<BUILDDIR>>/liboqs/src/kem/kyber/kem.c:24:57: error: argument 1 of type 'uint8_t[800]' "
    "with mismatched bound [-Werror=array-parameter=]",
    "/<<BUILDDIR>>/liboqs/src/kem/kyber/kem.c:25:17: error: argument 2 of type 'uint8_t[1632]' "
    "with mismatched bound [-Werror=array-parameter=]",
    "cc1: all warnings being treated as errors",
    "ninja: build stopped: subcommand failed.",
    "dh_auto_build: error: cd obj-aarch64-linux-gnu && ninja -j4 -v returned exit code 1",
]
LIBOQS_RULES = "#!/usr/bin/make -f\n\n%:\n\tdh $@\n\noverride_dh_dwz:\n"
BZRTP_RULES = ("#!/usr/bin/make -f\ninclude /usr/share/dpkg/pkg-info.mk\n\n"
               "export DEB_BUILD_MAINT_OPTIONS = hardening=+all\n\n%:\n\tdh $@ --buildsystem=cmake\n")


def bzrtp_log():
    return [
        "dpkg-buildpackage: info: host architecture amd64",
        f"/usr/bin/cc {FLAGS} -o packetParser.c.o -c /<<PKGBUILDDIR>>/src/packetParser.c",
        "/<<PKGBUILDDIR>>/src/packetParser.c:360:55: error: writing 1 byte into a region of size 0 "
        "[-Werror=stringop-overflow=]",
        "cc1: all warnings being treated as errors",
        "dh_auto_build: error: cd obj-x86_64-linux-gnu && make -j4 returned exit code 2",
    ]


class WnoError(unittest.TestCase):
    def tree(self, rules):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "input/debian").mkdir(parents=True)
        (root / "input/debian/rules").write_text(rules)
        return root

    def run_recipe(self, log, rules, target=""):
        root = self.tree(rules)
        edits, note = registry.run("wno-error", root, log, target)
        after = rules
        for edit in edits:
            after = after.replace(edit.old_text, edit.new_text, 1)
        return edits, note, after

    def test_liboqs_shape_without_exports_goes_above_the_first_rule(self):
        edits, note, after = self.run_recipe(LIBOQS_LOG, LIBOQS_RULES)
        self.assertEqual(len(edits), 1, note)
        self.assertIn("ifeq ($(DEB_HOST_ARCH),arm64)\n"
                      "export DEB_CFLAGS_MAINT_APPEND += -Wno-error=array-parameter\n"
                      "export DEB_CXXFLAGS_MAINT_APPEND += -Wno-error=array-parameter\nendif\n%:", after)
        self.assertIsNone(rules_guard.check(edits[0].path, edits[0].old_text, edits[0].new_text))
        self.assertEqual(syntax_guard.file_problems(edits[0].path, LIBOQS_RULES, after), [])

    def test_bzrtp_shape_goes_after_the_maint_options_export(self):
        edits, note, after = self.run_recipe(bzrtp_log(), BZRTP_RULES)
        self.assertEqual(len(edits), 1, note)
        self.assertIn("export DEB_BUILD_MAINT_OPTIONS = hardening=+all\n# A newer GCC", after)
        self.assertIn("ifeq ($(DEB_HOST_ARCH),amd64)", after)
        self.assertIn("-Wno-error=stringop-overflow", after)

    def test_an_existing_maint_append_export_is_appended_after(self):
        rules = "export DEB_CFLAGS_MAINT_APPEND = -Wall\n\n%:\n\tdh $@\n"
        edits, note, after = self.run_recipe(bzrtp_log(), rules)
        self.assertEqual(len(edits), 1, note)
        self.assertLess(after.index("= -Wall"), after.index("-Wno-error=stringop-overflow"))

    def test_a_later_plain_assignment_is_refused(self):
        rules = ("export DEB_BUILD_MAINT_OPTIONS = hardening=+all\n"
                 "export DEB_CXXFLAGS_MAINT_APPEND = -std=c++17\n%:\n\tdh $@\n")
        # DEB_CXXFLAGS_MAINT_APPEND is preferred as anchor, so ours lands after it: allowed
        edits, note, _ = self.run_recipe(bzrtp_log(), rules)
        self.assertEqual(len(edits), 1, note)
        rules = ("export DEB_BUILD_MAINT_OPTIONS = hardening=+all\n%:\n\tdh $@\n\n"
                 "DEB_CFLAGS_MAINT_APPEND = -O1\n")
        edits, note, _ = self.run_recipe(bzrtp_log(), rules)
        self.assertEqual(edits, [])
        self.assertIn("further down", note)

    def test_real_errors_type_limits_and_missing_evidence_do_not_match(self):
        root = self.tree(BZRTP_RULES)
        recipe = registry.get("wno-error")
        mixed = bzrtp_log()[:3] + ["/<<PKGBUILDDIR>>/src/a.c:3:10: fatal error: emmintrin.h: "
                                   "No such file or directory"] + bzrtp_log()[3:]
        type_limits = [l.replace("stringop-overflow", "type-limits") for l in bzrtp_log()]
        no_all = [l for l in bzrtp_log() if "all warnings" not in l]
        no_flags = [l.replace(FLAGS, "-O2 -Werror") for l in bzrtp_log()]
        implicit = [l.replace("stringop-overflow", "implicit-function-declaration") for l in bzrtp_log()]
        for name, log in (("mixed", mixed), ("type-limits", type_limits), ("no-all", no_all),
                          ("no-buildflags", no_flags), ("implicit", implicit)):
            with self.subTest(name):
                self.assertIsNone(recipe.recognise(log, root, ""))

    def test_an_explicit_werror_of_that_warning_is_refused(self):
        log = bzrtp_log()
        log[1] = log[1] + " -Werror=stringop-overflow"
        edits, note, _ = self.run_recipe(log, BZRTP_RULES)
        self.assertEqual(edits, [])
        self.assertIn("does not override", note)

    def test_already_present_is_refused(self):
        rules = BZRTP_RULES + "export DEB_CFLAGS_MAINT_APPEND += -Wno-error=stringop-overflow\n"
        edits, note, _ = self.run_recipe(bzrtp_log(), rules)
        self.assertEqual(edits, [])

    def test_the_pattern_block_offers_the_recipe(self):
        root = self.tree(LIBOQS_RULES)
        block = patterns.werror_warning(LIBOQS_LOG, root, "aarch64")
        self.assertIn("@@recipe:wno-error@@", block)
        self.assertIn("-Warray-parameter", block)
        self.assertIn("Dry run of recipe `wno-error`", block)

    def test_no_existing_recipe_case_is_claimed(self):
        recipe = registry.get("wno-error")
        root = self.tree(LIBOQS_RULES)
        for log_path in sorted((FIXTURES / "logs").glob("*.log")):
            with self.subTest(log_path.name):
                lines = log_path.read_text(errors="replace").splitlines()
                self.assertIsNone(recipe.recognise(lines, root, ""))


if __name__ == "__main__":
    unittest.main()
