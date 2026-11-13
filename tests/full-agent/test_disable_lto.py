"""disable-lto: tests linked with -Wl,--wrap in a build that uses LTO
(globalplatform, amd64). Log lines are from that public case. No model,
network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import patterns  # noqa: E402
from src.recipes import disable_lto, registry  # noqa: E402

LOG = [
    "dpkg-buildpackage: info: host architecture amd64",
    "/usr/bin/cc -g -O2 -flto=auto -ffat-lto-objects -fstack-protector-strong -c -o crypto.c.o crypto.c",
    "/usr/bin/cc -flto=auto -Wl,--wrap=RAND_bytes -o scp02Test scp02Test.c.o libglobalplatform.so -lcmocka",
    "/usr/bin/ld: /tmp/ccbUlIHk.ltrans0.ltrans.o: in function `mutual_authentication.constprop.0':",
    "/usr/src/globalplatform-2.3.1+dfsg-3/globalplatform/src/crypto.c:2264:(.text+0x5978): undefined reference to `__wrap_RAND_bytes'",
    "collect2: error: ld returned 1 exit status",
]
RULES = "#!/usr/bin/make -f\n\nexport DEB_BUILD_MAINT_OPTIONS = hardening=+all\n\n%:\n\tdh $@\n"


class Lto(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write(RULES)

    def write(self, text):
        path = self.root / "input/debian/rules"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def applied(self):
        edits, note = registry.run(disable_lto.ID, self.root, LOG, "amd64", {})
        text = (self.root / "input/debian/rules").read_text()
        self.assertEqual(text.count(edits[0].old_text), 1)
        return text.replace(edits[0].old_text, edits[0].new_text), note

    def test_a_wrap_symbol_missing_from_an_lto_object(self):
        match = disable_lto.recognise(LOG, self.root, "amd64")
        self.assertEqual(match.params["symbol"], "__wrap_RAND_bytes")
        text, note = self.applied()
        self.assertIn("export DEB_BUILD_MAINT_OPTIONS = hardening=+all optimize=-lto\n", text)
        self.assertIn("appended", note)
        self.assertIn("@@recipe:disable-lto@@", patterns.lto_wrap_link(LOG, self.root, "amd64"))

    def test_other_undefined_references_and_other_lto_noise_do_not_match(self):
        real = [line.replace("__wrap_RAND_bytes", "ksw_extz2_sse2") for line in LOG]       # rapmap
        self.assertIsNone(disable_lto.recognise(real, self.root, "arm64"))
        plain = [line.replace(".ltrans0.ltrans.o", ".o") for line in LOG]                    # no LTO object
        self.assertIsNone(disable_lto.recognise(plain, self.root, "amd64"))
        noisy = LOG[:2] + ["camlprim.c:451:14: warning: type of 'x' does not match original declaration "
                           "[-Wlto-type-mismatch]", "dh_auto_test: error: make test returned exit code 2"]
        self.assertIsNone(disable_lto.recognise(noisy, self.root, "arm64"))                  # tasmanian, ocaml
        without = [line.replace("-flto=auto", "").replace("-ffat-lto-objects", "") for line in LOG]
        self.assertIsNone(disable_lto.recognise(without, self.root, "amd64"))

    def test_tests_that_fail_on_an_lto_warning_in_compiler_output(self):
        # ocaml: 41 tests, all with the same shape
        log = [LOG[1],
               " ... testing 'custom.ml' with line 4 (bytecode) => failed (The file ./ocamlc.byte.output was "
               "expected to be empty because the compiler should not generate any output while compiling "
               "custom.ml but it is not:",
               "camlprim.c:451:14: warning: type of 'caml_zstd_initialize' does not match original declaration "
               "[-Wlto-type-mismatch]",
               "make[2]: *** [Makefile:906: tests] Error 2"]
        self.write("#!/usr/bin/make -f\nexport DEB_BUILD_MAINT_OPTIONS=hardening=+bindnow\n\n%:\n\tdh $@\n")
        match = disable_lto.recognise(log, self.root, "arm64")
        self.assertEqual(match.params["kind"], "output")
        [edit] = disable_lto.expand(self.root, match, {})
        self.assertEqual(edit.new_text, "export DEB_BUILD_MAINT_OPTIONS=hardening=+bindnow optimize=-lto\n")
        block = patterns.lto_wrap_link(log, self.root, "arm64")
        self.assertIn("Tests fail only because of link-time optimisation", block)
        far = log[:2] + ["x"] * 20 + log[2:]                 # the warning is not this test's output
        self.assertIsNone(disable_lto.recognise(far, self.root, "arm64"))

    def test_a_program_that_crashes_when_help2man_runs_it(self):
        # kissat: debian/rules asks for LTO, the linked solver dies on --help
        log = ["gcc -W -Wall -O3 -flto=auto -ffat-lto-objects -c main.c",
               "gcc -Wl,-Bsymbolic-functions -flto=auto -ffat-lto-objects -Wl,-z,relro -o kissat main.o libkissat.a -lm",
               'help2man --version-string=4.0.2 --name="Kissat Satisfiability Solver" -N -o kissat.1 build/kissat',
               "help2man: can't get `--help' info from build/kissat",
               "Try `--no-discard-stderr' if option outputs to stderr",
               "make[1]: *** [debian/rules:18: execute_after_dh_auto_build] Error 139"]
        self.write("#!/usr/bin/make -f\nexport DEB_BUILD_MAINT_OPTIONS = hardening=+all optimize=+lto\n\n%:\n\tdh $@\n")
        match = disable_lto.recognise(log, self.root, "amd64")
        self.assertEqual((match.params["kind"], match.params["symbol"]), ("startup", "build/kissat"))
        [edit] = disable_lto.expand(self.root, match, {})
        self.assertEqual(edit.new_text, "export DEB_BUILD_MAINT_OPTIONS = hardening=+all optimize=-lto\n")
        self.assertIn("do not add `|| true`", patterns.lto_wrap_link(log, self.root, "amd64"))
        missing = [line.replace("Error 139", "Error 1") for line in log]      # help2man simply not found
        self.assertIsNone(disable_lto.recognise(missing, self.root, "amd64"))
        other = [log[0], log[1].replace("-o kissat", "-o tissat")] + log[2:]  # this program was not the LTO link
        self.assertIsNone(disable_lto.recognise(other, self.root, "amd64"))

    def test_an_explicit_request_for_lto_is_turned_round(self):
        self.write(RULES.replace("hardening=+all", "hardening=+all optimize=+lto"))
        text, _ = self.applied()
        self.assertIn("export DEB_BUILD_MAINT_OPTIONS = hardening=+all optimize=-lto\n", text)
        self.assertNotIn("+lto", text)

    def test_no_options_line_gets_one_and_an_existing_option_is_left_alone(self):
        self.write("#!/usr/bin/make -f\n\n%:\n\tdh $@\n")
        text, _ = self.applied()
        self.assertIn("export DEB_BUILD_MAINT_OPTIONS = optimize=-lto\n", text)
        self.assertLess(text.index("optimize=-lto"), text.index("%:"))
        self.write(RULES.replace("hardening=+all", "hardening=+all optimize=-lto"))
        self.assertIsNone(disable_lto.recognise(LOG, self.root, "amd64"))


if __name__ == "__main__":
    unittest.main()
