"""0.5.2: a dh_makeshlibs failure repaired from dpkg-gensymbols's diff, and the
"dh $@" check of debian/rules.

From the 7 November local run of 0.5.1: libbpp-core resent one refused blanket
"(optional)" edit seven times and failed; html-xml-utils turned "dh $@" into
"dh $". The log excerpt is shortened from libbpp-core's initial build log.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
sys.path.insert(0, str(ROOT))

from src import rules_guard, symbols_diff  # noqa: E402
from src.contracts import AgentConfig  # noqa: E402
from src.types import CaseContext  # noqa: E402
from src.main import run_agent  # noqa: E402
from test_wiring import Script, reply  # noqa: E402

SYMBOLS = "input/debian/libbpp-core4.symbols.amd64"
FILE = """libbpp-core.so.4 libbpp-core4 #MINVER#
 _ZN3bpp11MatrixTools4copyINS_9RowMatrixIdEES3_EEvRKT_RT0_@Base 2.4.1
 _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vectorIS3_SaIS3_EES6_RS4_@Base 2.4.1
 _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EES6_RS4_@Base 2.4.1
 _ZN3bpp11VectorTools4testEv@Base 2.4.1
 _ZN3bpp11VectorTools6breaksERKSt6vectorIdSaIdEEj@Base 2.4.1
 (c++)"bpp::Number<int>::clone() const@Base" 2.4.1
 (optional)_ZN3bpp9old_inlineEv@Base 2.4.1
 _ZN3bpp12BracketPointD0Ev@Base 2.4.1
"""
LOG = """   dh_makeshlibs -a
[ 2275s] dpkg-gensymbols: warning: some new symbols appeared in the symbols file: see diff output below
[ 2275s] dpkg-gensymbols: error: some symbols or patterns disappeared in the symbols file: see diff output below
[ 2275s] --- debian/libbpp-core4.symbols.amd64 (libbpp-core4_2.4.1-6_amd64)
[ 2275s] +++ dpkg-gensymbolsXOuVXk	2021-10-31 12:14:30.749271710 +0000
[ 2275s] @@ -2,4 +2,4 @@
[ 2275s]   _ZN3bpp11MatrixTools4copyINS_9RowMatrixIdEES3_EEvRKT_RT0_@Base 2.4.1
[ 2275s] - _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vectorIS3_SaIS3_EES6_RS4_@Base 2.4.1
[ 2275s] +#MISSING: 2.4.1-6# _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vectorIS3_SaIS3_EES6_RS4_@Base 2.4.1
[ 2275s]   _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EES6_RS4_@Base 2.4.1
[ 2275s]   _ZN3bpp11VectorTools4testEv@Base 2.4.1
[ 2275s] + _ZN3bpp11VectorTools5pasteIjEENSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEERKSt6vectorIT_SaIS9_EERKS7_@Base 2.4.1-6
[ 2275s]   _ZN3bpp11VectorTools6breaksERKSt6vectorIdSaIdEEj@Base 2.4.1
[ 2275s] @@ -7,3 +7,3 @@
[ 2275s] - (c++)"bpp::Number<int>::clone() const@Base" 2.4.1
[ 2275s] +#MISSING: 2.4.1-6# (c++)"bpp::Number<int>::clone() const@Base" 2.4.1
[ 2275s] - (optional)_ZN3bpp9old_inlineEv@Base 2.4.1
[ 2275s] +#MISSING: 2.4.1-6# (optional)_ZN3bpp9old_inlineEv@Base 2.4.1
[ 2275s]   _ZN3bpp12BracketPointD0Ev@Base 2.4.1
[ 2275s] dh_makeshlibs: error: failing due to earlier errors
[ 2275s] make: *** [debian/rules:10: binary] Error 25
"""
BLANKET = {"path": SYMBOLS, "old_text": " _", "new_text": " (optional)_", "replace_all": True}
STOP = reply(action="stop", reason="done")


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.worktree = root / "work"
        (self.worktree / "input/debian").mkdir(parents=True)
        (self.worktree / SYMBOLS).write_text(FILE)
        self.log = root / "initial-build.log"
        self.log.write_text(LOG)

    def run_with(self, script, config=None):
        context = CaseContext("libbpp-core", self.worktree, "", {"target_arch": "amd64"})
        return run_agent(context, self.log, script, config=config or AgentConfig())


class Prepare(Case):
    def test_the_diff_becomes_one_edit_per_hunk(self):
        edits = symbols_diff.prepare(self.log, self.worktree)
        self.assertEqual(len(edits), 2)
        text = FILE
        for e in edits:
            self.assertEqual(e.path, SYMBOLS)
            self.assertIsNone(rules_guard.check(e.path, e.old_text, e.new_text))
            text = text.replace(e.old_text, e.new_text)
        # disappeared: marked optional; appeared: added; the rest unchanged
        self.assertIn(" (optional)_ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vector", text)
        self.assertIn(" _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EES6_RS4_@Base", text)
        self.assertIn(" _ZN3bpp11VectorTools5pasteIjEE", text)
        self.assertIn(' (c++|optional)"bpp::Number<int>::clone() const@Base"', text)
        self.assertIn(" (optional)_ZN3bpp9old_inlineEv@Base", text)
        self.assertNotIn("MISSING", text)
        self.assertEqual(text.count("optional"), 3)

    def test_no_error_or_no_matching_file_means_nothing(self):
        self.log.write_text(LOG.replace("error:", "warning:"))
        self.assertEqual(symbols_diff.prepare(self.log, self.worktree), [])
        self.log.write_text(LOG)
        (self.worktree / SYMBOLS).write_text("libbpp-core.so.4 libbpp-core4 #MINVER#\n")
        self.assertEqual(symbols_diff.prepare(self.log, self.worktree), [])

    def test_the_evidence_shows_the_prepared_edits(self):
        block = symbols_diff.render(symbols_diff.prepare(self.log, self.worktree))
        self.assertIn(symbols_diff.TITLE, block)
        self.assertIn("2 prepared edit(s)", block)
        self.assertIn('"path": "input/debian/libbpp-core4.symbols.amd64"', block)


class Loop(Case):
    def test_a_refused_blanket_edit_is_replaced_by_the_prepared_edits(self):
        script = Script(reply(action="edit", hypothesis="h", evidence=["e"], edits=[BLANKET]),
                        STOP, STOP)
        result, _ = self.run_with(script)
        self.assertIn(symbols_diff.TITLE, script.user(0))
        self.assertEqual(result.attempts[0].applied, 2)
        text = (self.worktree / SYMBOLS).read_text()
        self.assertEqual(text.count("optional"), 3)
        self.assertIn(" _ZN3bpp11VectorTools5pasteIjEE", text)
        events = [e["event"] for e in result.review_events]
        self.assertIn("rules_guard", events)
        self.assertIn("symbols_diff", events)

    def test_submitting_one_prepared_edit_brings_the_rest(self):
        first = symbols_diff.prepare(self.log, self.worktree)[0]
        script = Script(reply(action="edit", hypothesis="h", evidence=["e"], edits=[
            {"path": first.path, "old_text": first.old_text, "new_text": first.new_text}]),
            STOP, STOP)
        result, _ = self.run_with(script)
        self.assertEqual(result.attempts[0].applied, 2)

    def test_the_switch_turns_it_off(self):
        script = Script(reply(action="edit", hypothesis="h", evidence=["e"], edits=[BLANKET]),
                        STOP, STOP, STOP)
        result, _ = self.run_with(script, config=AgentConfig(use_symbols_diff=False))
        self.assertNotIn(symbols_diff.TITLE, script.user(0))
        self.assertEqual(result.attempts[0].applied, 0)
        self.assertEqual((self.worktree / SYMBOLS).read_text(), FILE)


class DhTarget(unittest.TestCase):
    def test_a_broken_dh_call_is_refused(self):
        for new in ("%:\n\tdh $\n", "%:\n\tdh $$@ --with autoreconf\n"):
            with self.subTest(new=new):
                self.assertIn('"dh $@"', rules_guard.check("input/debian/rules", "%:\n\tdh $@\n", new))

    def test_options_after_dh_target_are_allowed(self):
        for new in ("%:\n\tdh $@ --with autoreconf\n", "%:\n\tdh $@ --buildsystem=cmake\n"):
            self.assertIsNone(rules_guard.check("input/debian/rules", "%:\n\tdh $@\n", new))


if __name__ == "__main__":
    unittest.main()
