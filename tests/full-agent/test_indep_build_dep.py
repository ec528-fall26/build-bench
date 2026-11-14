"""promote-indep-build-dep: the arch-only build needs a program that is only in
Build-Depends-Indep (libmbim: gtkdocize; python-msgspec on arm64:
pybuild-plugin-pyproject). Log lines and control files are taken from those
public cases. No model, network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import patterns  # noqa: E402
from src.recipes import indep_build_dep, registry  # noqa: E402

MBIM_CONTROL = """Source: libmbim
Section: libs
Build-Depends: debhelper-compat (= 13),
               pkg-config,
               autoconf-archive,
               libglib2.0-dev (>= 2.56)
Build-Depends-Indep: gtk-doc-tools <!nodoc>,
                     libglib2.0-doc <!nodoc>
Standards-Version: 4.5.1

Package: libmbim-glib-dev
Architecture: any
Depends: ${misc:Depends}
"""
INSTALLED = [f"Unpacking {name} (1.0) ..." for name in
             ("debhelper", "pkg-config", "autoconf-archive", "libglib2.0-dev", "autoconf", "automake", "libtool")]
MBIM_LOG = ["Build Type: any", *INSTALLED,
            "Command: dpkg-buildpackage -us -uc -mLaunchpad Build Daemon <buildd@x> -B -rfakeroot",
            "dpkg-buildpackage: info: host architecture arm64",
            "   dh_autoreconf -a",
            "Can't exec \"gtkdocize\": No such file or directory at /usr/share/autoconf/Autom4te/FileUtils.pm line 293.",
            "autoreconf: error: gtkdocize failed with exit status: 2",
            "dh_autoreconf: error: autoreconf -f -i returned exit code 2",
            "make: *** [debian/rules:7: binary-arch] Error 25"]
MSGSPEC_CONTROL = """Source: python-msgspec
Build-Depends:
 debhelper-compat (= 13),
 dh-sequence-python3,
 python3-all,
Build-Depends-Indep:
 furo <!nodoc>,
 pybuild-plugin-pyproject,
 python3-pytest <!nocheck>,
Standards-Version: 4.7.0

Package: python3-msgspec
Architecture: any
"""
MSGSPEC_LOG = ["Build Type: any", *INSTALLED[:5], "Unpacking python3-all (3.12) ...",
               "Command: dpkg-buildpackage -us -uc -mLaunchpad Build Daemon <buildd@x> -B -rfakeroot",
               "E: pybuild pybuild:389: configure: plugin pyproject failed with: PEP517 plugin dependencies "
               "are not available. Please Build-Depend on pybuild-plugin-pyproject.",
               "dh_auto_configure: error: pybuild --configure -i python{version} -p 3.12 returned exit code 13",
               "make: *** [debian/rules:19: binary-arch] Error 25"]


class Worktree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def control(self, text):
        path = self.root / "input/debian/control"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def applied(self, log):
        edits, note = registry.run(indep_build_dep.ID, self.root, log, "arm64", {})
        text = (self.root / "input/debian/control").read_text()
        for edit in edits:
            self.assertEqual(text.count(edit.old_text), 1)
            text = text.replace(edit.old_text, edit.new_text)
        return text, note


class Recognition(Worktree):
    def test_a_missing_program_from_an_indep_package(self):
        self.control(MBIM_CONTROL)
        match = indep_build_dep.recognise(MBIM_LOG, self.root, "arm64")
        self.assertEqual(match.params["package"], "gtk-doc-tools")
        self.assertEqual(match.params["named"], "gtkdocize")
        self.assertIn("gtkdocize", match.evidence[0])

    def test_an_error_that_names_the_package(self):
        self.control(MSGSPEC_CONTROL)
        match = indep_build_dep.recognise(MSGSPEC_LOG, self.root, "arm64")
        self.assertEqual(match.params["package"], "pybuild-plugin-pyproject")

    def test_not_in_a_full_build(self):
        self.control(MBIM_CONTROL)
        full = [line.replace(" -B ", " -b ").replace("binary-arch", "binary") for line in MBIM_LOG
                if line != "Build Type: any"]
        self.assertIsNone(indep_build_dep.recognise(full, self.root, "amd64"))

    def test_not_when_the_package_was_installed(self):
        self.control(MBIM_CONTROL)
        log = ["Unpacking gtk-doc-tools (1.33) ..."] + MBIM_LOG
        self.assertIsNone(indep_build_dep.recognise(log, self.root, "arm64"))

    def test_not_for_a_program_no_indep_package_matches(self):
        self.control(MBIM_CONTROL)
        log = [line.replace("gtkdocize", "help2man") for line in MBIM_LOG]
        self.assertIsNone(indep_build_dep.recognise(log, self.root, "arm64"))
        log = MBIM_LOG[:-4] + ["/usr/bin/python3.10: No module named pip"] + MBIM_LOG[-2:]
        self.assertIsNone(indep_build_dep.recognise(log, self.root, "arm64"))

    def test_not_when_the_log_does_not_show_what_was_installed(self):
        self.control(MBIM_CONTROL)
        self.assertIsNone(indep_build_dep.recognise([l for l in MBIM_LOG if not l.startswith("Unpacking")],
                                                    self.root, "arm64"))

    def test_the_block_says_what_not_to_do(self):
        self.control(MBIM_CONTROL)
        block = patterns.indep_only_dependency(MBIM_LOG, self.root, "arm64")
        self.assertIn("@@recipe:promote-indep-build-dep@@", block)
        self.assertIn("GTK_DOC_CHECK", block)
        self.control(MBIM_CONTROL.replace("Build-Depends-Indep: gtk-doc-tools <!nodoc>,\n", "Build-Depends-Indep:\n"))
        self.assertEqual(patterns.indep_only_dependency(MBIM_LOG, self.root, "arm64"), "")


class Expansion(Worktree):
    def test_the_entry_is_added_to_build_depends_with_its_restriction(self):
        self.control(MBIM_CONTROL)
        text, note = self.applied(MBIM_LOG)
        self.assertIn("               libglib2.0-dev (>= 2.56),\n gtk-doc-tools <!nodoc>\nBuild-Depends-Indep:", text)
        fields = indep_build_dep.fields(text)
        self.assertIn("gtk-doc-tools", [name for name, _ in fields["Build-Depends"][1]])
        self.assertIn("gtk-doc-tools", [name for name, _ in fields["Build-Depends-Indep"][1]])
        self.assertIn("arch-only build", note)

    def test_a_field_that_ends_with_a_comma(self):
        self.control(MSGSPEC_CONTROL)
        text, _ = self.applied(MSGSPEC_LOG)
        self.assertIn(" python3-all,\n pybuild-plugin-pyproject\nBuild-Depends-Indep:", text)

    def test_refused_when_the_frozen_dependencies_do_not_have_it(self):
        self.control(MBIM_CONTROL)
        lock = self.root / "config/dependency-lock.json"
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps([{"package": "debhelper"}, {"package": "pkg-config"}]))
        with self.assertRaises(registry.Refusal):
            indep_build_dep.expand(self.root, indep_build_dep.recognise(MBIM_LOG, self.root, "arm64"), {})
        lock.write_text(json.dumps([{"package": "debhelper"}, {"package": "gtk-doc-tools"}]))
        self.assertEqual(len(indep_build_dep.expand(
            self.root, indep_build_dep.recognise(MBIM_LOG, self.root, "arm64"), {})), 1)


if __name__ == "__main__":
    unittest.main()
