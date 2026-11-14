"""strip-buildflag: a dpkg-buildflags option the target's compiler rejects
(eegdev and rocm-device-libs, noble amd64: -fno-cf-protection), and the block
for an option the package sets itself (qmidiarp: -msse on arm64). No model,
network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import edit_repair, patterns  # noqa: E402
from src.recipes import registry, strip_buildflag  # noqa: E402
from src.types import Edit  # noqa: E402

EEGDEV_RULES = """#!/usr/bin/make -f
CONFFLAGS = --without-gtec

ifeq (,$(findstring noopt,$(DEB_BUILD_OPTIONS)))
	export DEB_CFLAGS_MAINT_APPEND=-O3 -Wall -Wextra
else
	export DEB_CFLAGS_MAINT_APPEND=-Wall -Wextra
endif
export DEB_LDFLAGS_MAINT_APPEND=-Wl,-Bsymbolic-functions
export DEB_BUILD_MAINT_OPTIONS=hardening=-all,+format,+relro

%:
	dh  $@  --with movetousr
"""
EEGDEV_LOG = [
    "dpkg-buildpackage: info: host architecture amd64",
    "configure:5437: gcc -V >&5",
    "gcc: error: unrecognized command-line option '-V'",
    "configure:5457: gcc -g -O2 -fno-omit-frame-pointer -Wformat -Werror=format-security -fno-stack-protector "
    "-fno-stack-clash-protection -fno-cf-protection -O3 -Wall -Wextra conftest.c >&5",
    "gcc: error: unrecognized command-line option '-fno-cf-protection'; did you mean '-fcf-protection'?",
    "configure:5463: error: C compiler cannot create executables",
    "dh_auto_configure: error: ./configure --build=x86_64-linux-gnu returned exit code 77",
]
QMIDIARP_LOG = [
    "dpkg-buildpackage: info: host architecture arm64",
    "gcc: error: unrecognized command-line option ‘-msse’",
    "gcc: error: unrecognized command-line option ‘-mfpmath=sse’",
    "make[2]: *** [Makefile:1302: qmidiarp_arp.lv2/qmidiarp_arp_ui_gl.so] Error 1",
]


GOLF_LOG = [
    "dpkg-buildpackage: info: host architecture arm64",
    "gcc -c -o chandle.o chandle.c -Wall -fstack-protector-strong -fstack-clash-protection -fcf-protection -g",
    "cc1: error: ‘-fcf-protection=full’ is not supported for this target",
    "make[1]: *** [Makefile:339: chandle.o] Error 1",
]


class Worktree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class Recognition(Worktree):
    def test_a_buildflags_option_nowhere_in_the_package_is_recognised(self):
        self.write("input/debian/rules", EEGDEV_RULES)
        self.write("input/configure.ac", "AC_PROG_CC\n")
        match = registry.get("strip-buildflag").recognise(EEGDEV_LOG, self.root, "x86_64")
        self.assertEqual(match.params["flags"], ["-fno-cf-protection"])   # not configure's -V probe
        self.assertEqual(match.params["arch"], "amd64")
        self.assertEqual([r.id for r, _ in registry.menu(EEGDEV_LOG, self.root, "x86_64")], ["strip-buildflag"])

    def test_an_option_the_package_sets_itself_is_not_the_recipe(self):
        self.write("input/debian/rules", "#!/usr/bin/make -f\n%:\n\tdh $@\n")
        self.write("input/configure.ac", 'CFLAGS="$CFLAGS -msse -msse2 -mfpmath=sse"\n')
        self.assertIsNone(registry.get("strip-buildflag").recognise(QMIDIARP_LOG, self.root, "aarch64"))
        self.write("input/Makefile", "CFLAGS += -fno-cf-protection\n")
        self.write("input/debian/rules", EEGDEV_RULES)
        self.assertIsNone(registry.get("strip-buildflag").recognise(EEGDEV_LOG, self.root, "x86_64"))

    def test_other_logs_do_not_match(self):
        self.write("input/debian/rules", EEGDEV_RULES)
        for log in (["gcc: error: unrecognized command-line option '-V'"],
                    ["foo.c:3:1: error: unknown type name 'x'"],
                    ["cc1: error: unrecognized command-line option '-Wno-foo'"]):
            self.assertIsNone(registry.get("strip-buildflag").recognise(log, self.root, "x86_64"))


class Expansion(Worktree):
    def expand(self, rules=EEGDEV_RULES, log=EEGDEV_LOG, arch="x86_64"):
        self.write("input/debian/rules", rules)
        return edit_repair.expand_macros(
            [Edit("input/debian/rules", "@@recipe:strip-buildflag@@", "")], self.root, log, arch)

    def test_strips_the_flag_for_the_failing_architecture_after_the_options_line(self):
        edits, notes = self.expand()
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].old_text, "export DEB_BUILD_MAINT_OPTIONS=hardening=-all,+format,+relro\n")
        self.assertIn("ifeq ($(DEB_HOST_ARCH),amd64)\n"
                      "export DEB_CFLAGS_MAINT_STRIP += -fno-cf-protection\n"
                      "export DEB_CXXFLAGS_MAINT_STRIP += -fno-cf-protection\n"
                      "endif\n", edits[0].new_text)
        self.assertIn("Recipe strip-buildflag expanded", notes[0])
        self.assertNotIn("\t", edits[0].new_text.split("hardening", 1)[1])   # make level, no recipe tab

    def test_refuses_when_the_rules_already_strip_it(self):
        done = EEGDEV_RULES + ("export DEB_CFLAGS_MAINT_STRIP = -fno-cf-protection\n"
                               "export DEB_CXXFLAGS_MAINT_STRIP = -fno-cf-protection\n")
        edits, notes = self.expand(done)
        self.assertEqual(edits, [])            # the flag is in the package now: nothing to offer
        self.assertIn("Recipe strip-buildflag refused", notes[0])

    def test_fortran_compilers_get_the_fortran_variables_too(self):
        log = ["dpkg-buildpackage: info: host architecture amd64",
               "gfortran: error: unrecognized command-line option '-fno-cf-protection'"]
        edits, _ = self.expand(log=log)
        self.assertIn("export DEB_FFLAGS_MAINT_STRIP += -fno-cf-protection", edits[0].new_text)


class Block(Worktree):
    def test_the_recipe_block_leads_with_the_preview_and_invocation(self):
        self.write("input/debian/rules", EEGDEV_RULES)
        log = self.write("build.log", "\n".join(EEGDEV_LOG) + "\n")
        block = patterns.recognise([], log, self.root, "x86_64")
        self.assertIn("Compiler option from dpkg-buildflags that this compiler rejects: `-fno-cf-protection`", block)
        self.assertIn("+export DEB_CFLAGS_MAINT_STRIP += -fno-cf-protection", block)
        self.assertIn('"old_text":"@@recipe:strip-buildflag@@"', block)
        self.assertLess(block.index("@@recipe:strip-buildflag@@"), block.index("Repair: strip exactly"))

    def test_a_package_set_option_gets_its_locations_and_no_recipe(self):
        self.write("input/debian/rules", "#!/usr/bin/make -f\n%:\n\tdh $@\n")
        self.write("input/configure.ac", "AC_INIT\n" * 3 + 'CFLAGS="$CFLAGS -msse -msse2 -mfpmath=sse"\n')
        log = self.write("build.log", "\n".join(QMIDIARP_LOG) + "\n")
        block = patterns.recognise([], log, self.root, "aarch64")
        self.assertIn("`-msse`", block)
        self.assertIn("input/configure.ac:4", block)
        self.assertIn("sibling options", block)
        self.assertNotIn("@@recipe:", block)

    def test_configure_version_probes_alone_give_no_block(self):
        self.write("input/debian/rules", "#!/usr/bin/make -f\n%:\n\tdh $@\n")
        log = self.write("build.log", "gcc: error: unrecognized command-line option '-V'\n"
                                      "gcc: error: unrecognized command-line option '-qversion'\n")
        self.assertEqual(patterns.recognise([], log, self.root, "x86_64"), "")


class Occurrences(Worktree):
    def test_whole_word_match_and_skipped_directories(self):
        self.write("input/a.mk", "X = -msse2\n")
        self.write("input/.pc/p/b.mk", "X = -msse\n")
        self.assertEqual(strip_buildflag.occurrences(self.root, "-msse"), [])
        self.write("input/c.mk", "\nY = -msse -O2\n")
        self.assertEqual(strip_buildflag.occurrences(self.root, "-msse"), ["input/c.mk:2"])

class NotSupportedForThisTarget(Worktree):
    """golf (questing, arm64): cc1 names the option with a value the Makefile
    does not spell out. The package sets it, so it is the block, not the recipe."""

    def test_the_block_finds_the_plain_flag_in_the_makefile(self):
        self.write("input/debian/rules", "#!/usr/bin/make -f\n%:\n\tGG_DEBIAN_BUILD=1 dh $@\n")
        self.write("input/Makefile", "OPT=-O2\nCFLAGS=$(OPT) -Wall -fstack-clash-protection -fcf-protection $(MAP)\n")
        self.assertEqual(strip_buildflag.rejected_flags(GOLF_LOG)[0][0], "-fcf-protection=full")
        self.assertIsNone(strip_buildflag.recognise(GOLF_LOG, self.root, "arm64"))
        block = patterns.rejected_compiler_flag(GOLF_LOG, self.root, "arm64")
        self.assertIn("input/Makefile:2", block)
        self.assertIn("sets it itself", block)
        self.assertNotIn("@@recipe:", block)
