"""Recipe adoption (campaign 2026-11-09, live run of 0.6.0-rc.4): the model never
invoked a recipe, hand-tagged 11 of 12 symbols and stopped; on pybigwig it wrote half of the
static-archive repair. Covered here: the dry-run preview and invocation paragraph in the opening
block, "auto"/"default"/empty new_text, full-detail verdicts, the controller completing a partial
symbols repair, the one-time stop bounce on a NOT-fixed verdict, and the static-shim verdict.
No model, network or compiler.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent -p test_recipe_adoption.py
"""

from copy import deepcopy
import itertools
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import edit_repair, loop, patterns, verify  # noqa: E402
from src.arch_model import lookup  # noqa: E402
from src.contracts import AgentConfig, Diagnostic  # noqa: E402
from src.loop import run_loop  # noqa: E402
from src.recipes import registry  # noqa: E402
from src.types import Edit  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "recipes"
INVOCATION_WORDING = ("To apply exactly these edits, reply with this single edit; its old_text is a command to "
                      "the controller, not text in the file, and new_text may be left empty: ")


def log_lines(case):
    return (FIX / "logs" / f"{case}.log").read_text(encoding="utf-8").splitlines()


def symbols_path(root):
    return sorted(str(p.relative_to(root)) for p in (root / "input/debian").glob("*.symbols*"))[0]


class OpeningBlock(unittest.TestCase):
    def block(self, case, arch="amd64"):
        found = patterns.recognise([], FIX / "logs" / f"{case}.log", FIX / "trees" / case, arch)
        self.assertTrue(found, case)
        return found

    def check(self, case):
        block = self.block(case)
        self.assertIn(INVOCATION_WORDING, block)
        self.assertIn('"old_text":"@@recipe:symbols-tag@@","new_text":""', block)
        self.assertIn("Do not retype the lines yourself.", block)
        self.assertIn("Dry run of recipe `symbols-tag` (verified)", block)
        preview = block[block.index("Dry run"):block.index(INVOCATION_WORDING)]
        self.assertIn("--- a/input/debian/", preview)
        self.assertIn("+ (optional", preview)
        self.assertLessEqual(len(preview.split("\n")), 44)
        # before any other repair text
        for later in ("Repair:", "(optional=templinst)` for template", "`#MISSING: <version>#` is dpkg's"):
            self.assertLess(block.index(INVOCATION_WORDING), block.index(later), later)
        return block

    def test_libbpp_core(self):
        block = self.check("libbpp-core")
        self.assertIn("it would make 12 edit(s) in 1 file(s)", block)
        self.assertIn("more diff lines not shown", block)   # bounded

    def test_libbpp_qt(self):
        self.check("libbpp-qt")

    def test_the_preview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "t"
            shutil.copytree(FIX / "trees" / "libbpp-core", root)
            before = (root / symbols_path(root)).read_text()
            patterns.recipe_preview("symbols-tag", log_lines("libbpp-core"), root, "amd64")
            self.assertEqual((root / symbols_path(root)).read_text(), before)

    def test_every_recipe_backed_block_leads_with_the_paragraph(self):
        for case, arch in (("libmceliece", "arm64"), ("libevtx", "amd64"), ("html-xml-utils", "arm64"),
                           ("ectrans", "arm64"), ("python-intbitset", "amd64"), ("libunwind", "arm64"),
                           ("pybigwig", "amd64")):
            block = self.block(case, arch)
            self.assertIn(INVOCATION_WORDING, block, case)
            self.assertLess(block.index(INVOCATION_WORDING), block.index("Repair"), case)


class DefaultsInNewText(unittest.TestCase):
    def test_empty_auto_and_default_mean_the_same(self):
        root, log = FIX / "trees" / "libbpp-core", log_lines("libbpp-core")
        path = symbols_path(root)
        results = []
        for text in ("", "auto", "default", " Default "):
            edits, notes = edit_repair.expand_macros([Edit(path, "@@recipe:symbols-tag@@", text)], root, log, "amd64")
            self.assertEqual(len(edits), 12, notes)
            results.append([(e.old_text, e.new_text) for e in edits])
        self.assertTrue(all(r == results[0] for r in results))

    def test_other_recipes_take_the_words_as_defaults(self):
        params = registry.parse_params("default")
        self.assertEqual((params["_raw"], params["_words"]), ("", []))
        edits, notes = edit_repair.expand_macros(
            [Edit("input/debian/rules", "@@recipe:arch-flags@@", "default")],
            FIX / "trees" / "libunwind", log_lines("libunwind"), "arm64")
        self.assertTrue(edits, notes)


class FullDetail(unittest.TestCase):
    def test_symbols_verdict_names_every_remaining_symbol_with_its_line(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "t"
            shutil.copytree(FIX / "trees" / "libbpp-core", root)
            path = symbols_path(root)
            log = log_lines("libbpp-core")
            text = (root / path).read_text()
            (root / path).write_text(text)          # untouched file: all 12 remain
            verdicts = verify._symbols(root, log, lookup("amd64"), {path}, {path: text})
            self.assertEqual(len(verdicts), 1)
            said = verdicts[0].text
            for name in patterns.missing_symbols(log):
                self.assertIn(name, said)           # whole, never cut at 80 characters
            long_one = max(patterns.missing_symbols(log), key=len)
            self.assertGreater(len(long_one), 80)
            number = text.split("\n").index(next(l for l in text.split("\n") if long_one in l)) + 1
            self.assertIn(f"line {number}: ", said)
            self.assertIn(long_one + " 2.4.1", said)


class Controller(unittest.TestCase):
    """The loop with a stub model that hand-tags 11 of the 12 symbols."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "t"
        shutil.copytree(FIX / "trees" / "libbpp-core", self.root)
        self.path = symbols_path(self.root)
        self.log = FIX / "logs" / "libbpp-core.log"
        self.context = SimpleNamespace(worktree=self.root, case_id="launchpad-resolute-amd64-arm64-libbpp-core-1",
                                       task_metadata={"target_arch": "amd64"}, log_tail="")
        full, _ = registry.run("symbols-tag", self.root, log_lines("libbpp-core"), "amd64", {"path": self.path})
        self.assertEqual(len(full), 12)
        self.left = full[-1]
        self.hand = full[:-1]
        self.requests = []
        # These cases are the model-driven path, which is what remains when the
        # controller's own first-turn application does not happen (AutoApply below).
        auto = patch.object(loop, "AUTO_APPLY_RECIPES", False)
        auto.start()
        self.addCleanup(auto.stop)

    @staticmethod
    def turn(edits):
        return SimpleNamespace(action="edit", edits=edits, hypothesis="tag the symbols", evidence=[],
                               reason="", tool_requests=[])

    STOP = SimpleNamespace(action="stop", reason="no further edits needed", edits=[], tool_requests=[])

    def run_script(self, proposals):
        proposals = itertools.chain(proposals, itertools.repeat(self.STOP))

        def next_turn(request):
            self.requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}
        diagnostic = Diagnostic("dpkg-gensymbols: error: some symbols or patterns disappeared", "build",
                                None, None, [], None, 0, "")
        return run_loop(self.context, next_turn=next_turn, config=AgentConfig(use_build_feedback=False),
                        initial_diagnostics=[diagnostic], log_path=self.log, max_turns=12)

    def hand_tagged(self):
        return self.turn([Edit(e.path, e.old_text, e.new_text) for e in self.hand])

    def test_the_controller_completes_the_last_symbol_and_the_stop_is_accepted(self):
        result = self.run_script([self.hand_tagged(), self.STOP])
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(len(self.requests), 2)            # no bounce
        after = self.requests[1]
        self.assertEqual(after.prediction_status, "fixed")
        self.assertTrue(any(n.startswith("Controller completed the symbols-tag recipe: tagged 1 remaining "
                                         "symbol(s): ") and self.left.old_text.split("@")[0].strip() in n
                            for n in after.notes), after.notes)
        text = (self.root / self.path).read_text()
        self.assertIn(self.left.new_text, text)
        self.assertEqual(text.count("(optional"), 12 + (self.original_tags() - 0))

    def original_tags(self):
        return (FIX / "trees" / "libbpp-core" / self.path).read_text().count("(optional")

    def test_it_touches_nothing_else_and_can_be_switched_off(self):
        with patch.object(loop, "COMPLETE_MECHANICAL_REPAIRS", False):
            self.run_script([self.hand_tagged(), self.STOP])
        self.assertEqual(self.requests[1].prediction_status, "not_fixed")
        self.assertIn(self.left.old_text, (self.root / self.path).read_text())

    def test_a_stop_with_not_fixed_outstanding_is_bounced_exactly_once(self):
        with patch.object(loop, "COMPLETE_MECHANICAL_REPAIRS", False):
            result = self.run_script([self.hand_tagged(), self.STOP, self.STOP])
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(len(self.requests), 3)            # edit, bounced stop, accepted stop
        note = self.requests[2].controller_note
        name = self.left.old_text.strip()
        self.assertIn(name, note)                          # the whole symbol, version included
        self.assertGreater(len(name.split()[0]), 80)
        self.assertIn("[NOT fixed]", note)
        self.assertIn("@@recipe:symbols-tag@@", note)
        self.assertIn(INVOCATION_WORDING, note)
        self.assertEqual(self.requests[1].controller_note, "")
        self.assertNotIn("NOT fixed", "".join(r.controller_note for r in self.requests[3:]))

    def test_the_two_bounces_compose_without_a_loop(self):
        with patch.object(loop, "COMPLETE_MECHANICAL_REPAIRS", False):
            result = self.run_script([self.STOP, self.hand_tagged(), self.STOP, self.STOP])
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(len(self.requests), 4)    # empty stop bounced, edit, unfixed stop bounced, stop accepted
        self.assertIn("You stopped without changing any file", self.requests[1].controller_note)
        self.assertIn("[NOT fixed]", self.requests[3].controller_note)
        self.assertNotIn("You stopped without changing any file", self.requests[3].controller_note)


class StaticShimVerdict(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "t"
        shutil.copytree(FIX / "trees" / "pybigwig", self.root)
        self.log = log_lines("pybigwig")
        self.setup = self.root / "input/setup.py"

    def verdicts(self):
        return verify._static_shim(self.root, self.log, {"input/setup.py"})

    def test_extra_objects_alone_is_not_enough(self):
        text = self.setup.read_text().replace(
            'libs=["BigWig", "m", "z"]\n',
            'import subprocess\nsubprocess.check_call(["ar", "x", "/usr/lib/x86_64-linux-gnu/libBigWig.a"])\n'
            'objs = ["libbigwig-static/bwRead.o"]\nlibs=["m", "z"]\n').replace(
            "sources = srcs,", "sources = srcs,\n                    extra_objects = objs,")
        self.setup.write_text(text)
        (verdict,) = self.verdicts()
        self.assertIs(verdict.ok, False)
        self.assertIn("(c) no hidden shim defines stderr", verdict.text)
        self.assertIn("(d) -Wl,-Bsymbolic is not passed", verdict.text)
        self.assertNotIn("(a)", verdict.text)
        self.assertNotIn("(b)", verdict.text)
        self.assertIn("@@recipe:static-archive-shim@@", verdict.text)
        self.assertIn(INVOCATION_WORDING, verdict.text)

    def test_an_untouched_link_still_names_the_library(self):
        (verdict,) = self.verdicts()
        self.assertIs(verdict.ok, False)
        self.assertIn("(a) setup.py still links libBigWig", verdict.text)
        self.assertIn("(b)", verdict.text)

    def test_the_verified_expansion_passes(self):
        edits, notes = edit_repair.expand_macros([Edit("input/setup.py", "@@recipe:static-archive-shim@@", "")],
                                                 self.root, self.log, "amd64")
        self.assertTrue(edits, notes)
        text = self.setup.read_text()
        for edit in edits:
            text = text.replace(edit.old_text, edit.new_text, 1)
        self.setup.write_text(text)
        (verdict,) = self.verdicts()
        self.assertIs(verdict.ok, True, verdict.text)

    def test_no_verdict_without_the_signature_or_the_edit(self):
        self.assertEqual(verify._static_shim(self.root, self.log, set()), [])
        self.assertEqual(verify._static_shim(self.root, ["nothing"], {"input/setup.py"}), [])


if __name__ == "__main__":
    unittest.main()


class AutoApply(unittest.TestCase):
    """Before the model's first turn the controller applies the best verified
    recipe itself (lib1305 in the rc.5 validation and the rc.8 public run: the
    recipe matched, the model wrote something else, the case was lost)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "t"
        shutil.copytree(FIX / "trees" / "libbpp-core", self.root)
        self.path = symbols_path(self.root)
        self.before = (self.root / self.path).read_text()
        self.log = FIX / "logs" / "libbpp-core.log"
        self.context = SimpleNamespace(worktree=self.root, case_id="launchpad-resolute-amd64-arm64-libbpp-core-1",
                                       task_metadata={"target_arch": "amd64"}, log_tail="")
        self.requests = []

    STOP = SimpleNamespace(action="stop", reason="no further edits needed", edits=[], tool_requests=[])

    def run_script(self, proposals, **config):
        proposals = itertools.chain(proposals, itertools.repeat(self.STOP))

        def next_turn(request):
            self.requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}
        diagnostic = Diagnostic("dpkg-gensymbols: error: some symbols or patterns disappeared", "build",
                                None, None, [], None, 0, "")
        return run_loop(self.context, next_turn=next_turn, config=AgentConfig(use_build_feedback=False, **config),
                        initial_diagnostics=[diagnostic], log_path=self.log, max_turns=12)

    def tagged(self):
        return (self.root / self.path).read_text().count("(optional") - self.before.count("(optional")

    def test_the_recipe_is_applied_and_the_run_ends_without_a_model_turn(self):
        result = self.run_script([self.STOP])
        self.assertEqual(self.tagged(), 12)
        self.assertEqual(self.requests, [])                 # lib1305: the model's next turn undid the fix
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertIn("verified repair `symbols-tag`", result.message)
        self.assertEqual([a.applied for a in result.attempts], [12])
        self.assertTrue(result.attempts[0].hypothesis.startswith("controller: verified repair"))
        self.assertEqual(result.review_events[-1]["event"], "controller_recipe")
        self.assertEqual(result.review_events[-1]["prediction"], "fixed")

    def test_an_incomplete_prediction_hands_over_to_the_model(self):
        def not_fixed(running, verdicts):
            running.all_fixed = False
            running.prediction_status, running.prediction_reason = "not_fixed", "one symbol is still missing"
        with patch.object(loop._Loop, "_summarise", not_fixed):
            result = self.run_script([self.STOP, self.STOP])
        self.assertEqual(self.tagged(), 12)
        self.assertGreaterEqual(len(self.requests), 1)
        self.assertIn("controller applied the verified repair `symbols-tag`", self.requests[0].controller_note)
        self.assertEqual(len(result.attempts), 1)

    def test_with_the_stop_switched_off_the_model_sees_the_applied_recipe_first(self):
        with patch.object(loop, "STOP_AFTER_CONTROLLER_RECIPE", False):
            result = self.run_script([self.STOP])
        self.assertEqual(len(self.requests), 1)
        first = self.requests[0]
        self.assertEqual(first.prediction_status, "fixed")
        self.assertIn("controller applied the verified repair `symbols-tag`", first.controller_note)
        self.assertEqual(result.termination_reason, "model_stop")

    def test_sending_the_same_recipe_again_costs_no_attempt(self):
        again = SimpleNamespace(action="edit", hypothesis="tag the symbols", evidence=[], reason="",
                                tool_requests=[], edits=[Edit(self.path, "@@recipe:symbols-tag@@", "")])
        with patch.object(loop, "STOP_AFTER_CONTROLLER_RECIPE", False):
            result = self.run_script([again, self.STOP])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(self.tagged(), 12)
        self.assertIn("already applied", self.requests[1].feedback)
        self.assertEqual(result.termination_reason, "model_stop")

    def test_the_model_can_still_add_to_it(self):
        other = "input/debian/rules"
        (self.root / other).write_text("#!/usr/bin/make -f\n%:\n\tdh $@\n")
        more = SimpleNamespace(action="edit", hypothesis="something else", evidence=[], reason="", tool_requests=[],
                               edits=[Edit(other, "\tdh $@\n", "\tdh $@ --buildsystem=cmake\n")])
        with patch.object(loop, "STOP_AFTER_CONTROLLER_RECIPE", False):
            result = self.run_script([more, self.STOP])
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(self.tagged(), 12)

    def test_a_recipe_that_refuses_leaves_the_run_as_it_was(self):
        with patch.object(loop, "expand_macros", return_value=([], ["refused"])):
            self.run_script([self.STOP])
        first = self.requests[0]
        self.assertEqual(self.tagged(), 0)
        self.assertFalse(first.controller_note and "controller applied" in first.controller_note)
        self.assertFalse(first.feedback)
        self.assertFalse(first.edit_validation_feedback)

    def test_only_listed_recipes_are_applied_unasked(self):
        self.assertNotIn("o2-on-test-segfault", loop.AUTO_APPLY_IDS)     # a symptom, not a cause
        self.assertNotIn("wno-error", loop.AUTO_APPLY_IDS)
        self.assertTrue(loop.AUTO_APPLY_IDS <= set(registry.ids()))
        with patch.object(loop, "AUTO_APPLY_IDS", frozenset()):
            self.run_script([self.STOP])
        self.assertEqual(self.tagged(), 0)
        self.assertEqual(len(self.requests) >= 1, True)

    def test_it_can_be_switched_off_and_never_runs_with_build_feedback_or_review(self):
        with patch.object(loop, "AUTO_APPLY_RECIPES", False):
            self.run_script([self.STOP])
        self.assertEqual(self.tagged(), 0)
