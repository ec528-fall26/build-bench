"""Mechanical batch repair before a correction is requested (edit_repair.py).

Each case here is one of the no-build failures of 2-3 November
(docs/no-build-live-findings.md): whitespace-only mismatches, no-op and
duplicate edits, an unexported DEB_*_MAINT_* flag, a line inserted into a
backslash continuation, and a batch lost because one item could not be fixed.
No model, network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src.candidates import CandidateSnapshot  # noqa: E402
from src.contracts import AgentConfig  # noqa: E402
from src.edit_preflight import CONTINUATION_BREAK, continuation_breaks, focused_feedback  # noqa: E402
from src.edit_repair import (  # noqa: E402
    already_made, exported_maint_flags, prune_batch, relax_whitespace, retarget_path, static_warnings)
from src.loop import run_loop  # noqa: E402
from src import edit_repair  # noqa: E402
from src.types import Edit  # noqa: E402


def edit(old, new, path="input/a.c", replace_all=False):
    return SimpleNamespace(path=path, old_text=old, new_text=new, replace_all=replace_all)


def turn(edits, hypothesis="repair"):
    return SimpleNamespace(action="edit", edits=edits, hypothesis=hypothesis,
                           evidence=[], reason="", tool_requests=[])


STOP = SimpleNamespace(action="stop", reason="done", edits=[], tool_requests=[])


class RelaxWhitespace(unittest.TestCase):
    FILE = "int a;\n\t\t\tfoo(x);\n\t\t\tbar(y);  \nint b;\n"

    def test_two_tabs_for_three_is_corrected_in_old_and_new_text(self):
        # libunwind, round 4: the real line has three tabs; the model wrote two.
        fixed = relax_whitespace(self.FILE, edit("\t\tfoo(x);\n\t\tbar(y);\n",
                                                 "\t\tfoo(x);\n\t\tbaz();\n\t\tbar(y);\n"))
        self.assertEqual(fixed.old_text, "\t\t\tfoo(x);\n\t\t\tbar(y);  \n")
        self.assertEqual(fixed.new_text, "\t\t\tfoo(x);\n\t\t\tbaz();\n\t\t\tbar(y);\n")

    def test_spaces_for_tabs(self):
        fixed = relax_whitespace(self.FILE, edit("      foo(x);\n      bar(y);",
                                                 "      foo(z);\n      bar(y);"))
        self.assertEqual(fixed.old_text, "\t\t\tfoo(x);\n\t\t\tbar(y);  ")
        self.assertEqual(fixed.new_text, "\t\t\tfoo(z);\n\t\t\tbar(y);")

    def test_lines_the_model_wrote_at_column_zero_are_left_to_it(self):
        # Review, 3 November: with the recipe lines written at column 0, every
        # column-0 line of new_text (ifeq, else, endif) would have got a tab.
        self.assertIsNone(relax_whitespace(self.FILE, edit("foo(x);\n      bar(y);", "x")))
        rules = "override_dh_auto_configure:\n\tdh_auto_configure -- \\\n\t\t--with-foo\n"
        self.assertIsNone(relax_whitespace(rules, edit(
            "dh_auto_configure -- \\\n--with-foo\n",
            "ifeq ($(DEB_HOST_ARCH),arm64)\ndh_auto_configure -- \\\n--with-foo --no-asm\nendif\n")))

    def test_a_column_zero_line_that_really_is_at_column_zero_is_fine(self):
        rules = "#!/usr/bin/make -f\n%:\n\tdh $@\n"
        fixed = relax_whitespace(rules, edit("%:\n  dh $@\n", "X = 1\n\n%:\n  dh $@\n"))
        self.assertEqual((fixed.old_text, fixed.new_text), ("%:\n\tdh $@\n", "X = 1\n\n%:\n\tdh $@\n"))

    def test_a_new_makefile_line_never_gains_a_tab(self):
        # A tab-led line in a Makefile is a recipe line; an inserted make-level
        # line must not be re-indented into one.
        rules = "#!/usr/bin/make -f\n%:\n\tdh $@\n"
        self.assertIsNone(relax_whitespace(rules, edit(
            "%:\n  dh $@\n", "ifeq (a,b)\n  export X = 1\nendif\n%:\n  dh $@\n", path="input/debian/rules")))
        # Kept lines, and files that are not Makefiles, are fine.
        fixed = relax_whitespace(rules, edit("%:\n  dh $@\n", "%:\n  dh $@ --no-parallel\n",
                                             path="input/debian/rules"))
        self.assertIsNone(fixed)  # the recipe line changed: not a kept line
        fixed = relax_whitespace("f()\n\tx\n", edit("f()\n  x\n", "f()\n  x\n  y\n", path="input/a.py"))
        self.assertEqual(fixed.new_text, "f()\n\tx\n\ty\n")

    def test_an_inserted_line_with_its_own_indentation_is_left_to_the_model(self):
        self.assertIsNone(relax_whitespace(self.FILE, edit("  foo(x);", "  foo(x);\n    extra();")))
        # Blank inserted lines are fine.
        fixed = relax_whitespace(self.FILE, edit("  foo(x);", "  foo(x);\n\n  extra();"))
        self.assertEqual(fixed.new_text, "\t\t\tfoo(x);\n\n\t\t\textra();")

    def test_whitespace_only_lines_and_crlf_are_left_to_the_model(self):
        self.assertIsNone(relax_whitespace("import os\n   \nif True:\n\tpass\n",
                                           edit("\nif True:\n  pass\n", "\nif True:\n  pass\nV = 2\n")))
        self.assertIsNone(relax_whitespace("a\r\n\tb\r\nc\r\n", edit("  b\n", "  c\n")))

    def test_a_single_line_with_fewer_tabs_is_a_plain_substring(self):
        # "\t\tfoo" is inside "\t\t\tfoo", so the applier accepts it unchanged
        # and the extra tab stays in place: nothing for the relaxation to do.
        self.assertIsNone(relax_whitespace(self.FILE, edit("\t\tfoo(x);", "\t\tfoo(y);")))

    def test_exact_match_ambiguity_and_mid_line_text_are_left_alone(self):
        self.assertIsNone(relax_whitespace(self.FILE, edit("\t\t\tfoo(x);", "y")))  # exact: no need
        self.assertIsNone(relax_whitespace("  a;\n\ta;\n", edit("a;", "b;")))      # two places
        self.assertIsNone(relax_whitespace(self.FILE, edit("foo(x", "foo(z")))      # mid-line
        self.assertIsNone(relax_whitespace(self.FILE, edit("nothing here", "x")))
        self.assertIsNone(relax_whitespace(self.FILE, edit("\n\n", "x")))           # blank only
        self.assertIsNone(relax_whitespace(self.FILE, edit("foo(x);", "y", replace_all=True)))

    def test_one_model_indent_for_two_real_indents_is_ambiguous(self):
        # Review, 3 November: with "if x:" and its body both written at one tab,
        # the kept body line would have been dedented and the file broken.
        source = "def f(x):\n\tif x:\n\t\treturn 1\n\treturn 2\n"
        self.assertIsNone(relax_whitespace(source, edit(
            "\tif x:\n\treturn 1\n\treturn 2\n",
            "\tif x:\n\treturn 1\n\tprint(x)\n\treturn 2\n")))

    def test_trailing_newline_is_matched_to_the_file(self):
        fixed = relax_whitespace("a\n\tb\n", edit("  b\n", "  c\n"))
        self.assertEqual((fixed.old_text, fixed.new_text), ("\tb\n", "\tc\n"))
        self.assertIsNone(relax_whitespace("a\n\tb", edit("  b\n", "  c\n")))  # no newline to match


class ExportedFlags(unittest.TestCase):
    def test_an_added_maint_flag_in_debian_rules_gets_export(self):
        # libunwind, round 4: the flag reached no compile command.
        fixed, names = exported_maint_flags(edit(
            "%:\n\tdh $@\n",
            "ifeq ($(DEB_HOST_ARCH),arm64)\n  DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n"
            "endif\n%:\n\tdh $@\n", path="input/debian/rules"))
        self.assertEqual(names, ["DEB_CFLAGS_MAINT_APPEND"])
        self.assertIn("\n  export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n", fixed.new_text)

    def test_already_exported_kept_recipe_lines_or_other_files_are_untouched(self):
        for path, old, new in (
            ("input/debian/rules", "", "export DEB_LDFLAGS_MAINT_APPEND = -lgcc\n"),
            ("input/debian/rules", "DEB_CFLAGS_MAINT_APPEND = -O2\n", "DEB_CFLAGS_MAINT_APPEND = -O2\nX=1\n"),
            ("input/debian/rules", "DEB_CFLAGS_MAINT_APPEND = -O2\r\n", "DEB_CFLAGS_MAINT_APPEND = -O2\r\nX=1\r\n"),
            # A tab-indented line is a shell recipe: "VAR=value command" runs
            # the command; "export VAR=value command" would not (review).
            ("input/debian/rules", "\tdh_auto_build\n", "\tDEB_CFLAGS_MAINT_APPEND=-fPIC dh_auto_build\n"),
            ("input/Makefile", "", "DEB_CFLAGS_MAINT_APPEND = -O2\n"),
        ):
            with self.subTest(path=path, new=new):
                item = edit(old, new, path=path)
                self.assertIs(exported_maint_flags(item)[0], item)


class ContinuationBreaks(unittest.TestCase):
    BEFORE = ("noinst_PROGRAMS_cdep += forker Gperf-simple Lperf-simple \\\n"
              "\t\t\tGperf-trace Lperf-trace\n\n# comment\n")

    def test_an_assignment_inserted_inside_a_continued_variable(self):
        # The 19 October libunwind diff, exactly.
        after = self.BEFORE.replace("Lperf-simple \\\n", "Lperf-simple \\\nLperf-simple_LDFLAGS = -lgcc_s\n")
        self.assertEqual(continuation_breaks("input/tests/Makefile.am", self.BEFORE, after), [2])

    def test_an_assignment_swallowed_as_the_last_item_is_caught(self):
        after = self.BEFORE.replace("Lperf-trace\n", "Lperf-trace \\\nX = 1\n")
        self.assertEqual(continuation_breaks("input/tests/Makefile.am", self.BEFORE, after), [3])
        self.assertEqual(continuation_breaks("input/rules.mk", self.BEFORE, after), [3])
        self.assertEqual(continuation_breaks("input/CMakeLists.txt", self.BEFORE, after), [])

    def test_shell_continuations_in_recipes_are_not_rejected(self):
        # Review round 2: in a recipe, "VAR=value" as the last continued
        # argument is shell (configure-style), not a make assignment.
        before = ("override_dh_auto_configure:\n\tdh_auto_configure -- \\\n"
                  "\t\t--disable-static \\\n\t\t--enable-shared\n")
        after = before.replace("\t\t--enable-shared\n",
                               "\t\t--enable-shared \\\n\t\tLDFLAGS=\"$(LDFLAGS) -lgcc_s\"\n")
        self.assertEqual(continuation_breaks("input/debian/rules", before, after), [])
        # At make level an indented continued item is plausible too ...
        flags = "CONFIGURE_FLAGS = --prefix=/usr \\\n\t--enable-x\n"
        self.assertEqual(continuation_breaks("input/Makefile", flags,
                                             flags.replace("--enable-x\n", "--enable-x \\\n\tLDFLAGS=-lgcc_s\n")), [])
        # ... but "X = 1" or a column-0 line is a new statement.
        self.assertEqual(continuation_breaks("input/Makefile", flags,
                                             flags.replace("--enable-x\n", "--enable-x \\\n\tX = 1\n")), [3])

    def test_a_line_after_the_variable_or_a_continued_item_is_fine(self):
        after = self.BEFORE.replace("Lperf-trace\n", "Lperf-trace\nLperf-simple_LDFLAGS = -lgcc_s\n")
        self.assertEqual(continuation_breaks("input/tests/Makefile.am", self.BEFORE, after), [])
        after = self.BEFORE.replace("Lperf-simple \\\n", "Lperf-simple \\\n\t\t\tnew-prog \\\n")
        self.assertEqual(continuation_breaks("input/tests/Makefile.am", self.BEFORE, after), [])
        self.assertEqual(continuation_breaks("input/src/a.c", self.BEFORE,
                                             self.BEFORE.replace("\\\n", "\\\nX = 1\n")), [])

    def test_the_batch_check_rejects_it_with_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input/tests").mkdir(parents=True)
            (root / "input/tests/Makefile.am").write_text(self.BEFORE)
            item = edit("Lperf-simple \\\n", "Lperf-simple \\\nLperf-simple_LDFLAGS = -lgcc_s\n",
                        path="input/tests/Makefile.am")
            snapshot = CandidateSnapshot.capture(root, [item.path])
            index, feedback = focused_feedback(snapshot, [item], "link flags")
        self.assertEqual(index, 0)
        self.assertIn(CONTINUATION_BREAK, feedback)
        self.assertIn("after the variable's last line", feedback)

    def test_a_whitespace_mismatch_the_fix_declines_shows_the_exact_text(self):
        # Column-0 old_text: not auto-corrected, but the model gets the real
        # lines with their whitespace made visible.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / "input/a.c").write_text("int a;\n\t\t\tfoo(x);\n\t\t\tbar(y);  \nint b;\n")
            item = edit("foo(x);\nbar(y);", "foo(z);\nbar(y);")
            snapshot = CandidateSnapshot.capture(root, [item.path])
            index, feedback = focused_feedback(snapshot, [item], "h")
        self.assertIn("matches lines 2-3 of the file apart from leading or trailing whitespace", feedback)
        self.assertIn("2: ->->->foo(x);", feedback)
        self.assertIn("3: ->->->bar(y);\u00b7\u00b7", feedback)


class Warnings(unittest.TestCase):
    def test_make_conditional_in_makefile_am_is_a_warning_not_a_rejection(self):
        warnings = static_warnings("input/Makefile.am", "a = 1\n",
                                   "a = 1\nifeq ($(ARCH),arm64)\nb = 2\nendif\n")
        self.assertEqual(len(warnings), 1)
        self.assertIn("debian/rules", warnings[0])
        self.assertIn("automake", warnings[0])
        self.assertEqual(static_warnings("input/Makefile", "a = 1\n", "a = 1\nifeq (a,b)\nendif\n"), [])


class PruneBatch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input").mkdir()
        self.file = self.root / "input/a.c"
        self.file.write_text("int a;\n\t\t\tfoo(x);\nint b;\n")

    def prune(self, edits, **kwargs):
        snapshot = CandidateSnapshot.capture(self.root, [e.path for e in edits])
        kept, notes, dropped = prune_batch(snapshot, edits, **kwargs)
        return kept, " ".join(notes), dropped

    def test_no_ops_duplicates_and_whitespace_are_handled_in_one_pass(self):
        kept, notes, dropped = self.prune([
            edit("int a;", "int a;"),                 # no change
            edit("  foo(x);", "  foo(y);"),           # whitespace
            edit("int b;", "long b;"),
            edit("int b;", "long b;"),                # duplicate
        ])
        self.assertEqual(dropped, 2)
        self.assertEqual([(e.old_text, e.new_text) for e in kept],
                         [("\t\t\tfoo(x);", "\t\t\tfoo(y);"), ("int b;", "long b;")])
        for expected in ("Dropped edit 1 (input/a.c): its old_text and new_text are identical",
                         "Corrected the whitespace of edit 2",
                         "Dropped edit 4 (input/a.c): it repeats edit 3 exactly"):
            self.assertIn(expected, notes)

    def test_an_unfixable_item_is_left_to_the_loop(self):
        edits = [edit("int a;", "int c;"), edit("absent", "x")]
        kept, notes, dropped = self.prune(edits)
        self.assertEqual((len(kept), dropped, notes), (2, 0, ""))

    def test_a_repeat_of_a_whitespace_corrected_edit_is_still_a_duplicate(self):
        kept, notes, dropped = self.prune([edit("  foo(x);", "  foo(y);"), edit("  foo(x);", "  foo(y);")])
        self.assertEqual((len(kept), dropped), (1, 1))
        self.assertIn("repeats edit 1 exactly", notes)

    def test_whitespace_correction_is_tried_once_per_item(self):
        # A corrected old_text that is then ambiguous must not loop.
        self.file.write_text("\tfoo(x);\n\t\tfoo(x);\n")
        kept, notes, dropped = self.prune([edit("foo(x);", "bar();")])
        self.assertEqual((len(kept), dropped, notes), (1, 0, ""))


class _Salvage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input/debian").mkdir(parents=True)
        self.rules = self.root / "input/debian/rules"
        self.rules.write_text("#!/usr/bin/make -f\n%:\n\tdh $@\n")
        self.source = self.root / "input/a.c"
        self.source.write_text("int a;\n\t\t\tfoo(x);\nint b;\n")

    def run_script(self, proposals):
        self.requests = []
        proposals = iter(proposals)

        def next_turn(request):
            self.requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}

        return run_loop(SimpleNamespace(worktree=self.root), next_turn=next_turn,
                        config=AgentConfig(use_no_build_control=False, use_build_feedback=False))


class LoopSalvage(_Salvage):
    def test_whitespace_mismatch_is_applied_without_a_correction_turn(self):
        result = self.run_script([turn([edit("  foo(x);", "  foo(y);")]), STOP])
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertEqual(self.source.read_text(), "int a;\n\t\t\tfoo(y);\nint b;\n")
        self.assertEqual(self.requests[1].edit_validation_feedback, "")
        self.assertIn("Corrected the whitespace of edit 1", self.requests[1].feedback)

    def test_maint_flag_is_exported_and_reported(self):
        result = self.run_script([turn([edit(
            "%:\n", "DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n%:\n",
            path="input/debian/rules")]), STOP])
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertIn("\nexport DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n", self.rules.read_text())
        self.assertIn('Added "export" before DEB_CFLAGS_MAINT_APPEND', self.requests[1].feedback)

    def test_a_repeated_rejected_correction_drops_only_that_item(self):
        # libunwind, round 4: the same wrong old_text ten times in a row.
        result = self.run_script([
            turn([edit("int a;", "int c;"), edit("nowhere", "x"), edit("int b;", "int d;")]),
            turn([edit("still nowhere", "x")]),
            turn([edit("still nowhere", "x")]),
            STOP,
        ])
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual(result.attempts[0].applied, 2)
        self.assertEqual(self.source.read_text(), "int c;\n\t\t\tfoo(x);\nint d;\n")
        self.assertEqual(len(self.requests), 4)
        self.assertIn("Dropped edit 2 (input/a.c, old_text starting 'still nowhere') because "
                      "its correction repeated", self.requests[3].feedback)

    def test_only_the_repeated_item_is_dropped_and_the_next_one_gets_corrections(self):
        # Review, 3 November: a repeat must not drop every other failing item.
        result = self.run_script([
            turn([edit("int a;", "int c;"), edit("nowhere", "x"), edit("elsewhere", "y")]),
            turn([edit("still nowhere", "x")]),
            turn([edit("still nowhere", "x")]),      # repeat: drops that item only
            turn([edit("int b;", "int d;")]),         # the next item's own correction
            STOP,
        ])
        self.assertEqual(result.attempts[0].applied, 2)
        self.assertEqual(self.source.read_text(), "int c;\n\t\t\tfoo(x);\nint d;\n")
        focused = [r.edit_validation_feedback.split("FOCUSED INDEX: ")[1][:1]
                   for r in self.requests if "FOCUSED INDEX" in r.edit_validation_feedback]
        self.assertEqual(focused, ["1", "1", "1"])

    def test_a_bad_path_item_is_dropped_after_its_corrections(self):
        # Review, 3 November: the path check ran before the salvage and the
        # cycle restarted every 12 turns.
        batch = turn([edit("int a;", "int c;"), edit("x", "y", path="src/a.c")])
        # The same bad path again: dropped at once, the valid edit applied.
        result = self.run_script([batch, turn([edit("x", "y", path="src/a.c")]), STOP])
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual([a.applied for a in result.attempts], [1])
        self.assertEqual(len(self.requests), 3)
        # Twelve different wrong paths: the budget path, same outcome.
        self.source.write_text("int a;\n\t\t\tfoo(x);\nint b;\n")
        wrong = [turn([edit(f"x{n}", "y", path="src/a.c")]) for n in range(12)]
        result = self.run_script([batch] + wrong + [STOP])
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertEqual([a.applied for a in result.attempts], [1])
        self.assertEqual(len(self.requests), 14)

    def test_the_twelfth_correction_is_not_lost_to_the_turn_limit(self):
        corrections = [turn([edit(f"absent {n}", "x")]) for n in range(12)]
        proposals = iter([turn([edit("int a;", "int c;"), edit("absent", "x")])] + corrections)

        def next_turn(request):
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}

        result = run_loop(SimpleNamespace(worktree=self.root), next_turn=next_turn,
                          config=AgentConfig(use_no_build_control=False, use_build_feedback=False), max_turns=13)
        self.assertEqual(result.termination_reason, "turn_budget")
        self.assertEqual([a.applied for a in result.attempts], [1])
        self.assertEqual(self.source.read_text(), "int c;\n\t\t\tfoo(x);\nint b;\n")

    def test_notes_reach_the_correction_request(self):
        self.run_script([
            turn([edit("int a;", "int a;"), edit("nowhere", "x")]),
            turn([]),
            STOP,
        ])
        feedback = self.requests[1].edit_validation_feedback
        self.assertIn("FOCUSED INDEX: 0", feedback)
        self.assertIn("Before this check: Dropped edit 1", feedback)

    def test_notes_reach_the_model_with_the_attempt_result(self):
        # The adapter never renders request.feedback; the notes travel in
        # request.notes and are worded into the attempt's result.
        from src.contracts import Budget
        from src.model_adapter import ModelAdapter
        replies = iter([
            '{"action": "edit", "hypothesis": "h", "evidence": [], "edits": [{"path": "input/a.c", '
            '"old_text": "  foo(x);", "new_text": "  foo(y);"}, {"path": "input/a.c", '
            '"old_text": "int b;", "new_text": "int b;"}]}',
            '{"action": "stop", "reason": "done"}',
        ])
        sent = []

        def complete(messages):
            sent.append(messages)
            return next(replies), "stop", {"input_tokens": 1, "output_tokens": 1}

        context = SimpleNamespace(worktree=self.root, case_id="c", log_tail="",
                                  task_metadata={"target_arch": "arm64"})
        adapter = ModelAdapter(complete, context, config=AgentConfig(use_no_build_control=False, use_build_feedback=False),
                               budget=Budget(), diagnostics=[], log_path=None)
        self.addCleanup(adapter.close)
        result = run_loop(context, next_turn=adapter, config=AgentConfig(use_no_build_control=False, use_build_feedback=False))
        self.assertEqual(result.attempts[0].applied, 1)
        user = sent[1][1]["content"]
        self.assertIn("=== Changes the controller made to this batch before applying it ===", user)
        self.assertIn("- Corrected the whitespace of edit 1", user)
        self.assertIn("- Dropped edit 2 (input/a.c): its old_text and new_text are identical", user)
        self.assertLess(user.index("Changes the controller made"), user.index("Individual edit outcomes"))

    def test_notes_survive_a_correction_turn(self):
        # Review round 2: notes made before the batch went pending were lost.
        self.run_script([
            turn([edit("int a;", "int a;"), edit("int b;", "int d;"), edit("nowhere", "x")]),
            turn([]),                     # remove the failing item
            STOP,
        ])
        notes = " ".join(self.requests[2].notes)
        self.assertIn("Dropped edit 1 (input/a.c): its old_text and new_text are identical", notes)
        self.assertEqual(self.requests[2].latest_attempt.applied, 1)

    def test_a_discarded_batch_is_explained_to_the_model(self):
        self.run_script([
            turn([edit("nowhere", "x")]),
            turn([edit("still nowhere", "x")]),
            turn([edit("still nowhere", "x")]),
            STOP,
        ])
        self.assertEqual(self.requests[3].latest_attempt.applied, 0)
        self.assertIn("No edit of that batch could be applied", " ".join(self.requests[3].notes))
        self.assertEqual(self.requests[1].notes, [])

    def test_review_mode_keeps_the_notes_until_the_batch_is_approved(self):
        # Review, 3 November: with BB_REVIEW_EDITS=1 the drop note was lost.
        approve = SimpleNamespace(action="approve", reason="looks right", edits=[], tool_requests=[])
        proposals = iter([turn([edit("int a;", "int a;"), edit("int b;", "int d;")]), approve, STOP])
        requests = []

        def next_turn(request):
            requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}

        result = run_loop(SimpleNamespace(worktree=self.root), next_turn=next_turn,
                          config=AgentConfig(use_no_build_control=False, use_build_feedback=False), review_edits=True)
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertTrue(requests[1].proposal_review)
        self.assertIn("Dropped edit 1 (input/a.c): its old_text and new_text are identical",
                      " ".join(requests[2].notes))

    def test_a_repeated_anchor_is_hopeless_but_a_new_text_problem_is_not(self):
        # Review round 2: the same old_text with a different new_text defect
        # must still get its corrections.
        (self.root / "input/tests").mkdir()
        am = self.root / "input/tests/Makefile.am"
        am.write_text("noinst_PROGRAMS_cdep += forker Gperf-simple \\\n\t\t\tGperf-trace\n")
        bad = lambda new: edit("Gperf-simple \\\n", new, path="input/tests/Makefile.am")
        result = self.run_script([
            turn([bad("Gperf-simple \\\nX_LDFLAGS = -lgcc_s\n")]),   # continuation break
            turn([bad("Gperf-simple \\\n\0")]),                       # same anchor, NUL
            turn([bad("Gperf-simple \\\n\t\t\tX \\\n")]),           # fixed
            STOP,
        ])
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertIn("\t\t\tX \\\n", am.read_text())

    def test_notes_are_not_repeated_on_a_later_empty_proposal(self):
        self.run_script([turn([edit("  foo(x);", "  foo(y);")]), turn([]), STOP])
        self.assertTrue(self.requests[1].notes)
        self.assertEqual(self.requests[2].notes, [])

    def test_a_missing_file_is_one_items_problem(self):
        # Review round 2 (pre-existing): the whole batch was discarded without
        # an attempt, and a repeat could go on until the turn limit.
        result = self.run_script([
            turn([edit("int a;", "int c;"), edit("x", "y", path="input/missing.c")]),
            turn([edit("x", "y", path="input/missing.c")]),   # repeat: dropped
            STOP,
        ])
        self.assertEqual(result.attempts[0].applied, 1)
        self.assertEqual(self.source.read_text(), "int c;\n\t\t\tfoo(x);\nint b;\n")
        self.assertIn("file_not_capturable", self.requests[1].edit_validation_feedback)
        self.assertIn("Dropped edit 2 (input/missing.c", " ".join(self.requests[2].notes))

    def test_export_is_only_added_once_the_anchor_matches(self):
        # Review round 2: a space-indented "VAR=value command" recipe line with
        # a mis-written tab must not get export before the model fixes it.
        self.rules.write_text("#!/usr/bin/make -f\noverride_dh_auto_build:\n\t./configure\n\tmake\n")
        self.run_script([
            turn([edit("  ./configure\n", "  DEB_CFLAGS_MAINT_APPEND=-fPIC ./configure\n",
                       path="input/debian/rules")]),
            turn([]),
            STOP,
        ])
        feedback = self.requests[1].edit_validation_feedback
        self.assertNotIn("export", feedback.split("Proposed NEW text")[1][:120])
        self.assertNotIn("Added \"export\"", feedback)

    def test_notes_keep_the_models_numbering_after_a_loop_drop(self):
        result = self.run_script([
            turn([edit("nowhere", "x"), edit("int a;", "int a;"), edit("int b;", "int d;")]),
            turn([edit("still nowhere", "x")]),
            turn([edit("still nowhere", "x")]),
            STOP,
        ])
        self.assertEqual(result.attempts[0].applied, 1)
        notes = " ".join(self.requests[3].notes)
        self.assertIn("Dropped edit 1 (input/a.c, old_text starting 'still nowhere')", notes)
        self.assertIn("Dropped edit 2 (input/a.c): its old_text and new_text are identical", notes)

    def test_salvage_counts_no_tokens_and_keeps_usage_known(self):
        corrections = [turn([edit(f"absent {n}", "x")]) for n in range(12)]
        result = self.run_script([turn([edit("int a;", "int c;"), edit("absent", "x")])]
                                 + corrections + [STOP])
        self.assertEqual(result.usage, {"input_tokens": 14, "output_tokens": 14})
        self.assertEqual(result.attempts[0].applied, 1)


if __name__ == "__main__":
    unittest.main()


class ExportRemoval(_Salvage):
    """libunwind, 3 November: the model decided "export VAR += value" was
    invalid make and spent 37 turns taking the export off; each attempt was
    re-exported into a no-op and rejected as no_change."""

    def setUp(self):
        super().setUp()
        self.rules.write_text("#!/usr/bin/make -f\nexport DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n%:\n\tdh $@\n")

    def test_removing_the_export_is_dropped_with_the_reason_not_rejected(self):
        snapshot = CandidateSnapshot.capture(self.root, ["input/debian/rules"])
        kept, notes, dropped = prune_batch(snapshot, [edit(
            "export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n",
            "DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\nexport DEB_CFLAGS_MAINT_APPEND\n",
            path="input/debian/rules")])
        # new_text keeps the flag line and adds an export line: not a pure removal,
        # so it is exported and applied as the model wrote it otherwise.
        self.assertEqual(dropped, 0)
        self.assertEqual(kept[0].new_text,
                         "export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\nexport DEB_CFLAGS_MAINT_APPEND\n")
        kept, notes, dropped = prune_batch(snapshot, [edit(
            "export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n",
            "DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n", path="input/debian/rules")])
        self.assertEqual((kept, dropped), ([], 1))
        self.assertIn('Dropped edit 1 (input/debian/rules): it would only remove "export" from '
                      'DEB_CFLAGS_MAINT_APPEND. "export VAR += value" is valid GNU make', notes[0])

    def test_in_the_loop_the_batch_ends_in_one_turn_with_the_note(self):
        result = self.run_script([turn([edit(
            "export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n",
            "DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n", path="input/debian/rules")]), STOP])
        self.assertEqual([a.applied for a in result.attempts], [0])
        self.assertEqual(self.requests[1].edit_validation_feedback, "")
        self.assertIn("Do not propose this change again", self.requests[1].feedback)
        self.assertIn("export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics", self.rules.read_text())

    def test_the_export_note_says_the_one_line_form_is_valid(self):
        self.rules.write_text("#!/usr/bin/make -f\n%:\n\tdh $@\n")
        self.run_script([turn([edit("%:\n", "DEB_CFLAGS_MAINT_APPEND += -x\n%:\n",
                                    path="input/debian/rules")]), STOP])
        self.assertIn("is valid GNU make; do not split it", self.requests[1].feedback)


class Retarget(_Salvage):
    """rust-subversion, 3 November: 16 edits named src/client.rs for functions
    that live in src/delta.rs; each cost one to three correction turns."""

    def setUp(self):
        super().setUp()
        (self.root / "input/src").mkdir()
        (self.root / "input/src/client.rs").write_text('extern "C" fn wrap_filter_callback(\n')
        (self.root / "input/src/delta.rs").write_text('fn a() {}\nextern "C" fn wrap_editor_open_root(\n')
        (self.root / "input/src/other.c").write_text('extern "C" fn wrap_editor_open_root(\n')

    def test_the_one_file_holding_the_text_with_the_same_suffix(self):
        self.assertEqual(retarget_path(self.root, edit('extern "C" fn wrap_editor_open_root(', "x",
                                                       path="input/src/client.rs")), "input/src/delta.rs")
        # Too short to be specific; present in the stated file; in two files.
        self.assertIsNone(retarget_path(self.root, edit("fn a() {}", "x", path="input/src/client.rs")))
        self.assertIsNone(retarget_path(self.root, edit('extern "C" fn wrap_filter_callback(', "x",
                                                        path="input/src/client.rs")))
        (self.root / "input/src/more.rs").write_text('extern "C" fn wrap_editor_open_root(\n')
        self.assertIsNone(retarget_path(self.root, edit('extern "C" fn wrap_editor_open_root(', "x",
                                                        path="input/src/client.rs")))

    def test_a_missing_stated_file_is_retargeted_too(self):
        self.assertEqual(retarget_path(self.root, edit('extern "C" fn wrap_editor_open_root(', "x",
                                                       path="input/src/missing.rs")), "input/src/delta.rs")

    def test_policy_and_pc_directory_are_respected(self):
        (self.root / "input/.pc/p/src").mkdir(parents=True)
        (self.root / "input/.pc/p/src/delta.rs").write_text('extern "C" fn wrap_editor_open_root(\n')
        self.assertEqual(retarget_path(self.root, edit('extern "C" fn wrap_editor_open_root(', "x",
                                                       path="input/src/client.rs")), "input/src/delta.rs")
        policy = {"allowed_paths": ["input/src/client.rs"], "forbidden_paths": []}
        self.assertIsNone(retarget_path(self.root, edit('extern "C" fn wrap_editor_open_root(', "x",
                                                        path="input/src/client.rs"), patch_policy=policy))

    def test_in_the_loop_the_edit_is_moved_and_applied_without_a_correction(self):
        result = self.run_script([turn([
            edit('extern "C" fn wrap_filter_callback(', 'unsafe extern "C" fn wrap_filter_callback(',
                 path="input/src/client.rs"),
            edit('extern "C" fn wrap_editor_open_root(', 'unsafe extern "C" fn wrap_editor_open_root(',
                 path="input/src/client.rs"),
        ]), STOP])
        self.assertEqual([a.applied for a in result.attempts], [2])
        self.assertEqual((self.root / "input/src/delta.rs").read_text(),
                         'fn a() {}\nunsafe extern "C" fn wrap_editor_open_root(\n')
        self.assertEqual(self.requests[1].edit_validation_feedback, "")
        self.assertIn("Moved edit 2 from input/src/client.rs to input/src/delta.rs", self.requests[1].feedback)

    def test_a_moved_edit_that_still_fails_goes_to_the_model_once(self):
        # The text is in delta.rs but the model's replace is a no-op there.
        result = self.run_script([turn([
            edit('extern "C" fn wrap_editor_open_root(', 'extern "C" fn wrap_editor_open_root(',
                 path="input/src/client.rs")]), STOP])
        self.assertEqual([a.applied for a in result.attempts], [0])


class AlreadyMade(_Salvage):
    """rust-subversion, 0.3.2 live: after the batch that replaced every i8
    with c_char, the model proposed the same replace_all 35 more times."""

    def setUp(self):
        super().setUp()
        self.source.write_text("fn f(p: *const std::ffi::c_char, q: *mut std::ffi::c_char) {}\n")

    def test_predicate(self):
        text = self.source.read_text()
        self.assertTrue(already_made(text, edit("*const i8", "*const std::ffi::c_char", replace_all=True)))
        self.assertFalse(already_made(text, edit("*const std::ffi::c_char", "*const i8")))  # old present
        self.assertFalse(already_made(text, edit("*const i8", "c_char")))                   # too short
        self.assertFalse(already_made(text, edit("*const i8", "*const libc::c_char")))      # not made

    def test_dropped_with_the_note_and_no_correction_turn(self):
        result = self.run_script([turn([
            edit("*const i8", "*const std::ffi::c_char", replace_all=True),
            edit("*mut i8", "*mut std::ffi::c_char", replace_all=True),
        ]), STOP])
        self.assertEqual([a.applied for a in result.attempts], [0])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[1].edit_validation_feedback, "")
        self.assertIn("Dropped edit 1 (input/a.c): its old_text occurs nowhere in the file any more "
                      "and its new_text already does", self.requests[1].feedback)
        self.assertIn("Dropped edit 2", self.requests[1].feedback)
        self.assertIn("No edit of that batch could be applied", self.requests[1].feedback)

    def test_a_genuinely_missing_text_still_goes_to_the_model(self):
        self.run_script([turn([edit("*const i8", "*const libc::c_char", replace_all=True)]),
                         turn([]), STOP])
        self.assertIn("FOCUSED INDEX: 0", self.requests[1].edit_validation_feedback)


class SymbolsMacro(unittest.TestCase):
    """libbpp-core (round 10): the agent tags the missing symbols itself from
    the dpkg-gensymbols diff in the log; the model sends one macro edit."""

    LOG = ["dpkg-gensymbols: error: some symbols or patterns disappeared in the symbols file: see diff output below",
           "--- debian/libbpp-core4.symbols.amd64 (libbpp-core4_2.4.1-6_amd64)",
           "+++ dpkg-gensymbolsXOuVXk\t2021-10-31",
           "- _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vectorIS3_SaIS3_EES6_RS4_@Base 2.4.1",
           "+#MISSING: 2.4.1-6# _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vectorIS3_SaIS3_EES6_RS4_@Base 2.4.1",
           "-#MISSING: 2.4.1-6# (c++)\"bpp::B::b()@Base\" 2.4.1",
           "+ _ZN3bppNewE@Base 2.4.1-6",
           "dh_makeshlibs: error: failing due to earlier errors"]
    FILE = ("libbpp-core.so.4 libbpp-core4 #MINVER#\n"
            " _Z4compii@Base 2.4.1\n"
            " _ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vectorIS3_SaIS3_EES6_RS4_@Base 2.4.1\n"
            " (c++)\"bpp::B::b()@Base\" 2.4.1\n")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        path = self.root / "input/debian/libbpp-core4.symbols.amd64"
        path.parent.mkdir(parents=True)
        path.write_text(self.FILE)

    def macro(self, tag="optional"):
        from src.patterns import SYMBOLS_MACRO
        return Edit("input/debian/libbpp-core4.symbols.amd64", SYMBOLS_MACRO, tag)

    def test_expands_to_one_edit_per_missing_symbol_in_dpkg_syntax(self):
        edits, notes = edit_repair.expand_macros([self.macro()], self.root, self.LOG)
        self.assertEqual(len(edits), 2)
        self.assertEqual(edits[0].new_text, " (optional)_ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vectorIS3_SaIS3_EES6_RS4_@Base 2.4.1")
        self.assertEqual(edits[1].new_text, " (c++|optional)\"bpp::B::b()@Base\" 2.4.1")
        self.assertIn("Recipe symbols-tag expanded: tagged 2 of the 2 symbols", notes[0])
        text = self.FILE
        for e in edits:
            text = text.replace(e.old_text, e.new_text)
        self.assertIn(" _Z4compii@Base 2.4.1\n", text)   # untouched symbols stay

    def test_arch_tag_and_already_tagged_lines(self):
        (self.root / "input/debian/libbpp-core4.symbols.amd64").write_text(
            self.FILE.replace(" (c++)\"bpp::B", " (c++|arch=!amd64)\"bpp::B"))
        edits, notes = edit_repair.expand_macros([self.macro("arch=!amd64")], self.root, self.LOG)
        self.assertEqual(len(edits), 1)
        self.assertTrue(edits[0].new_text.startswith(" (arch=!amd64)_ZN3bpp11"))
        self.assertIn("1 were tagged already", notes[0])

    def test_without_a_symbols_diff_the_macro_is_dropped_with_a_note(self):
        edits, notes = edit_repair.expand_macros([self.macro()], self.root, ["unrelated"])
        self.assertEqual(edits, [])
        self.assertIn("Recipe symbols-tag refused", notes[0])

    def test_the_recipe_token_and_the_legacy_alias_are_the_same_macro(self):
        token = Edit("input/debian/libbpp-core4.symbols.amd64", "@@recipe:symbols-tag@@", "auto")
        alias = self.macro("optional")
        by_token, notes = edit_repair.expand_macros([token], self.root, self.LOG)
        by_alias, _ = edit_repair.expand_macros([alias], self.root, self.LOG)
        self.assertEqual(len(by_token), len(by_alias))
        self.assertIn("Recipe symbols-tag expanded", notes[0])
        # auto picks the tag per symbol: the std::vector template gets templinst, plain (c++) stays optional
        self.assertIn("(optional=templinst)_ZN3bpp11MatrixTools4multIdEEvRKNS_6MatrixIT_EERKSt6vector", by_token[0].new_text)
        self.assertIn(" (c++|optional)\"bpp::B::b()@Base\"", by_token[1].new_text)

    def test_a_refused_recipe_leaves_the_batch_without_the_macro_and_says_why(self):
        edits, notes = edit_repair.expand_macros([Edit("input/debian/rules", "@@recipe:strip-bsymbolic-functions@@", "")],
                                                 self.root, self.LOG)
        self.assertEqual(edits, [])
        self.assertIn("Recipe strip-bsymbolic-functions refused", notes[0])

    def test_ordinary_edits_pass_through(self):
        plain = Edit("input/debian/rules", "a", "b")
        self.assertEqual(edit_repair.expand_macros([plain], self.root, self.LOG), ([plain], []))


class InteriorWhitespace(unittest.TestCase):
    """xmbmon (public cases, rc.5 and rc.8): four tabs in the file, three in the
    model's old_text, four attempts, no change submitted."""

    FILE = ("#ifdef LINUX\t\t\t\t/* LINUX */\n#include <sys/io.h>\n#define   SET_IOPL()\tiopl(3)\n"
            "#define RESET_IOPL()\tiopl(0)\n\n#elif defined(__svr4__)\t\t/* Solaris for x86 */\n")
    OLD = "#ifdef LINUX\t\t\t/* LINUX */\n#include <sys/io.h>\n#define   SET_IOPL()\tiopl(3)\n"
    NEW = ("#ifdef LINUX\t\t\t/* LINUX */\n#if defined(__x86_64__)\n#include <sys/io.h>\n#endif\n"
           "#define   SET_IOPL()\tiopl(3)\n")

    def test_the_files_own_lines_replace_old_text_and_the_kept_lines_of_new_text(self):
        self.assertIsNone(edit_repair.relax_whitespace(self.FILE, Edit("input/io_cpu.h", self.OLD, self.NEW)))
        fixed = edit_repair.relax_interior_whitespace(self.FILE, Edit("input/io_cpu.h", self.OLD, self.NEW))
        self.assertEqual(self.FILE.count(fixed.old_text), 1)
        self.assertTrue(fixed.old_text.startswith("#ifdef LINUX\t\t\t\t/* LINUX */\n"))
        self.assertEqual(fixed.new_text, "#ifdef LINUX\t\t\t\t/* LINUX */\n#if defined(__x86_64__)\n"
                                         "#include <sys/io.h>\n#endif\n#define   SET_IOPL()\tiopl(3)\n")

    def test_it_refuses_what_it_cannot_decide(self):
        relax = edit_repair.relax_interior_whitespace
        twice = self.FILE + self.FILE
        self.assertIsNone(relax(twice, Edit("p", self.OLD, self.NEW)))                    # two places
        self.assertIsNone(relax(self.FILE, Edit("p", "  " + self.OLD, self.NEW)))        # indentation differs
        self.assertIsNone(relax(self.FILE, Edit("p", self.OLD, self.OLD)))               # nothing but whitespace
        self.assertIsNone(relax(self.FILE, Edit("p", "x  y\n", "z\n")))                  # too short to trust
        self.assertIsNone(relax(self.FILE, Edit("p", self.OLD, self.NEW, True)))         # replace_all
        self.assertIsNone(relax(self.FILE, Edit("p", "#include <sys/io.h>\n", "x\n")))   # already exact

    def test_prune_batch_uses_it_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / "input/io_cpu.h").write_text(self.FILE)
            snapshot = CandidateSnapshot.capture(root, ["input/io_cpu.h"])
            kept, notes, _ = prune_batch(snapshot, [Edit("input/io_cpu.h", self.OLD, self.NEW)])
            self.assertEqual(len(kept), 1)
            self.assertIn(kept[0].old_text, self.FILE)
            self.assertTrue(any("spaces and tabs inside the lines" in note for note in notes))
