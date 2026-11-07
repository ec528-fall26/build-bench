"""0.5.0: the no-build control and the rules guard.

From the 0.3.3 baseline on the 111 small public cases (6 November): runs that
hit the 6-tool-turn wall (46 of 111), stacked guesses over 8 attempts (34 runs,
20% passed), mistook their own edits for the package's, stopped with nothing
changed (8 runs), and edited tests or .pc/ in ways the rules or dpkg-source
reject. The guard excerpts are shortened from those runs.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
sys.path.insert(0, str(ROOT))

from src import rules_guard  # noqa: E402
from src.contracts import AgentConfig, Budget  # noqa: E402
from src.loop import EMPTY_STOP  # noqa: E402
from src.model_adapter import NO_BUILD_TOOL_TURNS  # noqa: E402
import test_wiring  # noqa: E402
from test_wiring import CONFIG, Script, edit, reply  # noqa: E402

NEW = AgentConfig()  # a submitted run: every switch on, no build adapter
READ = reply(action="tool", requests=[{"tool": "read_file", "args": {
    "path": CONFIG, "start_line": 1, "end_line": 20}}], why="read")
STOP = reply(action="stop", reason="done")
FIX = edit("#define HAVE_MM_PREFETCH 1\n", "#define HAVE_MM_PREFETCH 0\n")


class Guard(unittest.TestCase):
    def refused(self, path, old, new):
        return rules_guard.check(path, old, new)

    def test_the_baseline_bypasses_are_refused(self):
        cases = [
            # lua-struct: the failing assertions deleted
            ("input/teststruct.lua", 'a, i = lib.unpack("s", x, 1)\nassert(a == "hi")\n'
             'a, i = lib.unpack("s", x, i)\nassert(a == "hello")\n', "", "removes 2 test assertion"),
            # libapache2-mod-auth-openidc: a test made to return early on aarch64
            ("input/test/test.c", "static char *test_plaintext_decrypt(apr_pool_t *pool) {\n",
             "static char *test_plaintext_decrypt(apr_pool_t *pool) {\n"
             "#if defined(__aarch64__) || defined(__arm__)\n    return 0;\n#endif\n", "return early"),
            # python-pyppmd: a test skipped
            ("input/tests/test_benchmark.py", "def test_compress(benchmark):\n",
             "@pytest.mark.skip(reason='slow on arm64')\ndef test_compress(benchmark):\n", "skips"),
            # node-re2: a failing command made to pass
            ("input/debian/rules", "\tchmod +x debian/x/usr/lib/x86_64-linux-gnu/a/*.js\n",
             "\tchmod +x debian/x/usr/lib/$(DEB_HOST_MULTIARCH)/a/*.js || true\n", "|| true"),
            ("input/debian/rules", "%:\n\tdh $@\n",
             "%:\n\tdh $@\n\noverride_dh_auto_test:\n\t-dh_auto_test\n", 'prefixes'),
            ("input/debian/rules", "%:\n\tdh $@\n",
             "export DEB_BUILD_OPTIONS += nocheck\n%:\n\tdh $@\n", "nocheck"),
            ("input/debian/rules", "%:\n\tdh $@\n",
             "%:\n\tdh $@\n\noverride_dh_auto_test:\n\techo skipped\n", "does not run the tests"),
            ("input/debian/rules", "\tdh_auto_test\n",
             "\tdh_auto_test -- -k 'not test_fuzzer'\n", "deselects"),
            ("input/tests/Makefile.am", "TESTS = a.sh b.sh\n", "", "test list"),
            # pyfftw, python-msgspec, golang-v2ray-core: quilt's copies edited
            ("input/.pc/dask2.8.patch/test/test_x.py", "x = 1\n", "x = 2\n", ".pc/"),
            ("input/debian/patches/prepare.patch", "-a\n", "-b\n", "debian/patches/"),
        ]
        for path, old, new, why in cases:
            with self.subTest(path=path, why=why):
                reason = self.refused(path, old, new)
                self.assertIsNotNone(reason)
                self.assertIn(why, reason)

    def test_marking_every_symbol_optional_is_refused(self):
        # libbpp-core, 0.5.0 locally (7 November): a replace_all over the whole file.
        path = "input/debian/libbpp-core4.symbols.amd64"
        self.assertIn("replace_all", rules_guard.check(
            path, " _Z", " (optional)_Z", replace_all=True))
        self.assertIn("replace_all", rules_guard.check(       # jsonnet, 0.3.3
            "input/debian/libjsonnet0.symbols.amd64", " _", " (optional) _", replace_all=True))
        old = "".join(f" _ZN3bpp{n}E@Base 2.4.1\n" for n in range(60))
        new = "".join(f" (optional)_ZN3bpp{n}E@Base 2.4.1\n" for n in range(60))
        self.assertIn("marks 60 symbols", rules_guard.check(path, old, new))

    def test_marking_the_reported_symbols_optional_is_allowed(self):
        # libbpp-qt and libbpp-core, 0.3.3: passed Development Validation.
        old = (" _ZN5QListIP15QAbstractButtonE6detachEv@Base 2.4.1\n"
               " _ZN9QtPrivate8RefCount5derefEv@Base 2.4.1\n")
        new = (" (optional) _ZN5QListIP15QAbstractButtonE6detachEv@Base 2.4.1\n"
               " (optional) _ZN9QtPrivate8RefCount5derefEv@Base 2.4.1\n")
        self.assertIsNone(rules_guard.check("input/debian/libbpp-qt2t64.symbols.amd64", old, new))
        # libleidenalg, 0.3.3 (passed): the C++ standard-library instantiations only.
        self.assertIsNone(rules_guard.check("input/debian/liblibleidenalg1.symbols.amd64",
                                            " _ZNSt", " (optional) _ZNSt", replace_all=True))
        self.assertIsNone(rules_guard.check("input/debian/libfoo1.symbols",
                                            " old@Base 1\n", " (arch=amd64)old@Base 1\n"))

    def test_legitimate_repairs_are_allowed(self):
        cases = [
            # an expected value corrected, the check kept
            ("input/tests/test_num.py", "assert parse('0.1') == 0.1\n",
             "assert parse('0.1') == pytest.approx(0.1)\n"),
            # cowsql: a code fix in a test helper
            ("input/test/lib/server.c", "strcpy(addr.sun_path + 1, address + 1);\n",
             "strncpy(addr.sun_path + 1, address + 1, sizeof(addr.sun_path) - 2);\n"),
            # libunwind: the Debian flag fix
            ("input/debian/rules", "%:\n\tdh $@\n",
             "ifeq ($(DEB_HOST_ARCH),arm64)\nexport DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n"
             "endif\n%:\n\tdh $@\n"),
            # common rules idioms that are not bypasses
            ("input/debian/rules", "override_dh_auto_clean:\n\tdh_auto_clean\n",
             "override_dh_auto_clean:\n\t-rm -f config.log\n\tdh_auto_clean\n"),
            ("input/debian/rules", "\tsed 's/a/b/' x\n", "\tsed -E 's/a+/b/' x\n"),
            ("input/debian/rules", "%:\n\tdh $@\n",
             "%:\n\tdh $@\n\noverride_dh_auto_test:\n\tdh_auto_test -- -timeout 30m\n"),
            # source code that is not a test
            ("input/src/struct.c", "assert(n > 0);\n", "if (n <= 0) return 0;\n"),
        ]
        for path, old, new in cases:
            with self.subTest(path=path, new=new[:30]):
                self.assertIsNone(self.refused(path, old, new))


class CompilerSwitch(unittest.TestCase):
    def test_the_compiler_check_can_be_switched_off(self):
        import os
        from unittest.mock import patch
        from src import syntax_guard
        with patch.dict(os.environ, {"BB_SYNTAX_GUARD_COMPILER": "0"}):
            self.assertIsNone(syntax_guard._compiler("c"))
            self.assertIsNone(syntax_guard._compiler("c++"))


class Control(unittest.TestCase):
    setUp = test_wiring.Adapter.setUp
    run_with = test_wiring.Adapter.run_with

    def test_a_refused_edit_is_dropped_with_its_reason_and_the_rest_applied(self):
        bad = {"path": "input/.pc/x/src/crc32c/crc32c_config.h", "old_text": "a", "new_text": "b"}
        good = {"path": CONFIG, "old_text": "#define HAVE_MM_PREFETCH 1\n",
                "new_text": "#define HAVE_MM_PREFETCH 0\n"}
        script = Script(reply(action="edit", hypothesis="h", evidence=["e"], edits=[bad, good]), STOP)
        result, _ = self.run_with(script, config=NEW)
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertIn("HAVE_MM_PREFETCH 0", (self.worktree / CONFIG).read_text())
        self.assertIn("Refused edit 1 (input/.pc/x/", script.user(1))
        self.assertIn("quilt's record", script.user(1))

    def test_the_guard_can_be_switched_off(self):
        (self.worktree / "input/tests").mkdir()
        (self.worktree / "input/tests/t.c").write_text("assert(x == 1);\nassert(y == 2);\n")
        bad = {"path": "input/tests/t.c", "old_text": "assert(x == 1);\nassert(y == 2);\n",
               "new_text": "assert(y == 2);\n"}
        for config, applied in ((NEW, 0), (AgentConfig(use_rules_guard=False), 1)):
            with self.subTest(guard=config.use_rules_guard):
                (self.worktree / "input/tests/t.c").write_text("assert(x == 1);\nassert(y == 2);\n")
                script = Script(reply(action="edit", hypothesis="h", evidence=["e"], edits=[bad]),
                                STOP, STOP, STOP)
                result, _ = self.run_with(script, config=config)
                self.assertEqual(result.attempts[0].applied, applied)

    def test_an_empty_stop_is_sent_back_once(self):
        script = Script(STOP, FIX, STOP)
        result, _ = self.run_with(script, config=NEW)
        self.assertIn(EMPTY_STOP, script.user(1))
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertIn("HAVE_MM_PREFETCH 0", (self.worktree / CONFIG).read_text())
        # a second empty stop ends the run
        script = Script(STOP, STOP)
        self.setUp()
        result, _ = self.run_with(script, config=NEW)
        self.assertEqual((result.termination_reason, len(script.sent)), ("model_stop", 2))

    def test_without_the_control_an_empty_stop_ends_the_run(self):
        script = Script(STOP)
        result, _ = self.run_with(script, config=AgentConfig(use_no_build_control=False))
        self.assertEqual(len(script.sent), 1)

    def test_with_builds_nothing_changes(self):
        script = Script(STOP)
        self.run_with(script, config=NEW, build=test_wiring.FakeBuild())
        self.assertEqual(len(script.sent), 1)

    def test_the_model_sees_its_own_untested_edits(self):
        script = Script(FIX, STOP)
        self.run_with(script, config=NEW)
        second = script.user(1)
        self.assertIn("=== Your own edits so far (untested", second)
        self.assertIn("+#define HAVE_MM_PREFETCH 0", second)
        self.assertIn("-#define HAVE_MM_PREFETCH 1", second)
        self.assertIn("These changes are YOURS", second)
        self.assertNotIn("Your own edits so far", script.user(0))

    def test_tools_stay_on_past_six_turns_until_the_run_budget(self):
        script = Script(*([READ] * 8), FIX, STOP)
        result, _ = self.run_with(script, config=NEW)
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertNotIn("Tools are switched off", "".join(script.user(n) for n in range(9)))

    def test_the_investigation_budget_ends_tool_use(self):
        script = Script(*([READ] * NO_BUILD_TOOL_TURNS), READ, FIX, STOP)
        self.run_with(script, config=NEW, budget=Budget(max_seconds=100_000))
        self.assertIn(f"You have used all {NO_BUILD_TOOL_TURNS} investigation turns",
                      script.user(NO_BUILD_TOOL_TURNS))

    def test_at_most_three_attempts_without_builds(self):
        script = Script(*[edit("#define HAVE_MM_PREFETCH 1\n" if n == 0 else
                               f"#define HAVE_MM_PREFETCH {n}\n",
                               f"#define HAVE_MM_PREFETCH {n + 2}\n", f"try {n}")
                          for n in range(6)])
        result, _ = self.run_with(script, config=NEW)
        self.assertEqual((result.termination_reason, len(result.attempts)), ("attempt_budget", 3))


if __name__ == "__main__":
    unittest.main()
