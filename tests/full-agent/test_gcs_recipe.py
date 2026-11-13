"""gcs-report-dynamic-none: ld's GCS warning about a dependency fails a
--fatal-warnings link on arm64 (efibootmgr, kdsoap, grantlee5,
kf6-frameworkintegration in the public cases). Log lines and debian/rules are
taken from those cases. No model, network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import patterns  # noqa: E402
from src.recipes import gcs_report, registry  # noqa: E402

GCS = ("warning: GCS is required by -z gcs, but this shared library lacks the necessary property note. "
       "The dynamic loader might not enable GCS or refuse to load the program unless all the shared "
       "library dependencies have the GCS marking.")
EFI_LOG = [
    "dpkg-buildpackage: info: host architecture arm64",
    "gcc -Wl,--fatal-warnings,--no-allow-shlib-undefined -o efibootdump efibootdump.o -lefivar -lpopt",
    "/usr/lib/gcc/aarch64-linux-gnu/15/../../../aarch64-linux-gnu/libpopt.so: " + GCS,
    "collect2: error: ld returned 1 exit status",
    "make[3]: *** [/<<PKGBUILDDIR>>/Make.rules:21: efibootdump] Error 1",
]
KDSOAP_LOG = [
    "dpkg-buildpackage: info: host architecture arm64",
    "/usr/bin/c++ -fPIC -Wl,--no-undefined -Wl,--fatal-warnings -shared -o libkdsoap-qt6.so.2.2.0",
    "/usr/lib/aarch64-linux-gnu/libQt6Network.so.6.8.3: " + GCS,
    "/usr/lib/aarch64-linux-gnu/libQt6Core.so.6.8.3: " + GCS,
    "/<<PKGBUILDDIR>>/kdwsdl2cpp/wsdl/definitions.cpp:179:48: warning: 'qAsConst' is deprecated",
    "collect2: error: ld returned 1 exit status",
    "make[3]: *** [src/CMakeFiles/kdsoap.dir/build.make:400: bin/libkdsoap-qt6.so.2.2.0] Error 1",
]
# tasmanian, qmidiarp, rocm-llvm: the same warning, the link goes through.
WARNING_ONLY_LOG = [
    "dpkg-buildpackage: info: host architecture arm64",
    "/usr/lib/gcc/aarch64-linux-gnu/15/libgomp.so: " + GCS,
    "cd /<<PKGBUILDDIR>>/obj && /usr/bin/cmake -E cmake_symlink_library libtasmaniansparsegrid.so.8.2.0",
    "make[3]: Leaving directory '/<<PKGBUILDDIR>>/obj-aarch64-linux-gnu'",
    "FAILED: test 19",
]
EFI_RULES = """#!/usr/bin/make -f

include /usr/share/dpkg/default.mk

ifeq ($(DEB_TARGET_ARCH),arm64)
        DEB_LDFLAGS_MAINT_APPEND=-Wl,-z,gcs-report-dynamic=none
endif

EFIDIR ?= $(shell dpkg-vendor --query vendor | tr A-Z a-z)

%:
\tdh $@
"""
KDSOAP_RULES = """#!/usr/bin/make -f

%:
\tdh $@

override_dh_auto_configure:
\tdh_auto_configure -- -DKDSoap_QT6=ON
"""
KF6_RULES = """#!/usr/bin/make -f
# -*- makefile -*-

export DEB_BUILD_MAINT_OPTIONS = hardening=+all

%:
\tdh $@
"""
BLOCK = ("ifeq ($(DEB_HOST_ARCH),arm64)\n"
         "export DEB_LDFLAGS_MAINT_APPEND += -Wl,-z,gcs-report-dynamic=none\n"
         "endif\n")


class Worktree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def rules(self, text):
        path = self.root / "input/debian/rules"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def run_recipe(self, log):
        edits, note = registry.run(gcs_report.ID, self.root, log, "arm64", {})
        text = (self.root / "input/debian/rules").read_text()
        for edit in edits:
            self.assertEqual(text.count(edit.old_text), 1)
            text = text.replace(edit.old_text, edit.new_text)
        return text, note


class Recognition(Worktree):
    def test_a_warning_followed_by_the_failed_link_matches(self):
        self.rules(KDSOAP_RULES)
        match = gcs_report.recognise(KDSOAP_LOG, self.root, "arm64")
        self.assertIsNotNone(match)
        self.assertIn("libQt6Network.so.6.8.3", match.evidence[0])
        self.assertIn("2 such warnings", match.evidence[1])

    def test_the_warning_alone_does_not(self):
        self.rules(KDSOAP_RULES)
        self.assertIsNone(gcs_report.recognise(WARNING_ONLY_LOG, self.root, "arm64"))

    def test_a_link_that_fails_for_another_reason_does_not(self):
        self.rules(KDSOAP_RULES)
        log = KDSOAP_LOG[:4] + ["/usr/bin/ld: foo.o: undefined reference to `bar'"] + KDSOAP_LOG[5:]
        self.assertIsNone(gcs_report.recognise(log, self.root, "arm64"))

    def test_only_on_arm64(self):
        self.rules(KDSOAP_RULES)
        log = ["dpkg-buildpackage: info: host architecture amd64"] + KDSOAP_LOG[1:]
        self.assertIsNone(gcs_report.recognise(log, self.root, "amd64"))

    def test_it_is_a_build_verified_recipe_with_a_block(self):
        self.assertEqual(registry.get(gcs_report.ID).tier, "verified")
        self.rules(KDSOAP_RULES)
        block = patterns.gcs_link_warning(KDSOAP_LOG, self.root, "arm64")
        self.assertIn("@@recipe:gcs-report-dynamic-none@@", block)
        self.assertIn("--no-gcs", block)          # the option rc.5 lost efibootmgr on is named as wrong
        self.assertEqual(patterns.gcs_link_warning(WARNING_ONLY_LOG, self.root, "arm64"), "")


class Expansion(Worktree):
    def test_a_rules_file_without_exports_gets_the_block_before_the_first_rule(self):
        self.rules(KDSOAP_RULES)
        text, note = self.run_recipe(KDSOAP_LOG)
        self.assertIn(BLOCK + "%:\n\tdh $@\n", text)
        self.assertIn("for arm64", note)

    def test_it_goes_after_the_existing_export(self):
        self.rules(KF6_RULES)
        text, _ = self.run_recipe(KDSOAP_LOG)
        self.assertLess(text.index("hardening=+all"), text.index(BLOCK))
        self.assertLess(text.index(BLOCK), text.index("%:"))

    def test_efibootmgr_the_line_is_there_and_only_lacks_export(self):
        self.rules(EFI_RULES)
        text, note = self.run_recipe(EFI_LOG)
        self.assertIn("        export DEB_LDFLAGS_MAINT_APPEND=-Wl,-z,gcs-report-dynamic=none\n", text)
        self.assertEqual(text.count("gcs-report-dynamic=none"), 1)       # no second block
        self.assertEqual(text.replace("export DEB_LDFLAGS", "DEB_LDFLAGS"), EFI_RULES)
        self.assertIn("not exported", note)

    def test_refused_when_the_option_is_already_exported(self):
        self.rules(EFI_RULES.replace("        DEB_LDFLAGS", "        export DEB_LDFLAGS"))
        with self.assertRaises(registry.Refusal):
            registry.get(gcs_report.ID).expand(self.root, gcs_report.recognise(EFI_LOG, self.root, "arm64"), {})

    def test_refused_when_a_later_assignment_would_drop_it(self):
        self.rules(KF6_RULES.replace("%:", "ifneq (,$(X))\nexport DEB_LDFLAGS_MAINT_APPEND = -Wl,--as-needed\nendif\n\n%:"))
        match = gcs_report.recognise(KDSOAP_LOG, self.root, "arm64")
        try:
            edits = registry.get(gcs_report.ID).expand(self.root, match, {})
        except registry.Refusal:
            return
        text = (self.root / "input/debian/rules").read_text().replace(edits[0].old_text, edits[0].new_text)
        # accepted only when the new line comes after the plain assignment
        self.assertLess(text.index("--as-needed"), text.index("gcs-report-dynamic=none"))


if __name__ == "__main__":
    unittest.main()
