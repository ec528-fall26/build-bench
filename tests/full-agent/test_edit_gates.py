"""Edit gates (campaign 9 November, M4): symbols grammar, edit-size budget,
verify verdicts and rules_guard additions, checked against the edits the
validator accepted (harness/replays/*-verified-*.json) and the recorded runs.

Evidence-based tests read the export under the scratchpad and skip when it is
absent; everything else is self-contained. No model, network, compiler or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent -p test_edit_gates.py
"""

from collections import Counter
import glob
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import arch_model, edit_applier, patterns, rules_guard, syntax_guard, verify  # noqa: E402
from src.candidates import CandidateSnapshot  # noqa: E402
from src.contracts import Diagnostic  # noqa: E402
from src.edit_preflight import (MAX_EDIT_LINES, changed_line_count, inspect_batch,  # noqa: E402
                                validate_batch)

AMD = arch_model.lookup("amd64")
ARM = arch_model.lookup("arm64")
REPLAYS = sorted(glob.glob(str(ROOT / "harness/replays/*-verified-*.json")))
EVIDENCE = Path("/private/tmp/claude-501/-Users-owenzhang-Downloads-Build-Bench-Project/"
                "2210bc37-be95-42e4-9f36-c14dd9f4e919/scratchpad/evidence")
HAVE_EVIDENCE = EVIDENCE.is_dir()
# Runs whose directory says "repaired" but whose edit the gates must refuse: the
# validator passed them for other reasons (dpkg dropped the unparseable lines; the
# build still worked without the flags), and the campaign found the edits unsound.
KNOWN_BAD = {"libbpp-qt/runs/rc3-repaired", "html-xml-utils/runs/v032-repaired"}

HEADER = "libx.so.1 libx1 #MINVER#\n"


def edit(path, old, new, replace_all=False):
    return SimpleNamespace(path=path, old_text=old, new_text=new, replace_all=replace_all)


def lint(text):
    return [n for n, _, _ in syntax_guard.symbols_lint(text)]


def problems(path, before, after):
    return syntax_guard.file_problems(path, before, after)


def hunks(diff_text):
    """{path: [(old_lines, new_lines)]} from a unified diff (one entry per hunk)."""
    out, path, old, new = {}, None, None, None
    for line in diff_text.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
            out.setdefault(path, [])
            old = None
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            old, new = [], []
            out[path].append((old, new))
        elif old is not None and line[:1] == " ":
            old.append(line[1:]), new.append(line[1:])
        elif old is not None and line[:1] == "-":
            old.append(line[1:])
        elif old is not None and line[:1] == "+":
            new.append(line[1:])
    return out


def apply_unified(text, diff_text, path, reverse=False):
    """The text after (or, reversed, before) one file's hunks of a unified diff."""
    lines = text.split("\n")
    shift = 0
    for old, new in hunks(diff_text).get(path, []):
        want, put = (new, old) if reverse else (old, new)
        start = next((i for i in range(max(0, shift - 5), len(lines) - len(want) + 1)
                      if lines[i:i + len(want)] == want), None)
        if start is None:
            start = next(i for i in range(len(lines) - len(want) + 1) if lines[i:i + len(want)] == want)
        lines[start:start + len(want)] = put
        shift = start + len(put)
    return "\n".join(lines)


class SymbolsGrammar(unittest.TestCase):
    def test_every_dpkg_form_is_accepted(self):
        text = HEADER + "\n".join([
            " _Z1fv@Base 1.0",
            " (optional)_Z2fv@Base 1.0",
            " (optional=templinst)_Z3fv@Base 1.0",
            " (arch=amd64 i386|optional)_Z4fv@Base 1.0~rc1 1",
            " (arch=!arm64)_Z5fv@Base #MINVER#",
            ' (c++)"foo::bar()@Base" 1.0',
            ' (c++)"foo::bar()"@Base 1.0',
            ' (c++|optional)"foo(int, int)@Base" 1.0',
            ' (c++|arch=!arm64 !riscv64)"std::vector<int>::size() const@Base" 1.0',
            " *@Base 1.0",
            " (regex|c++)\"^foo.*@Base\" 1.0",
            "#include \"other.symbols\"",
            "# a comment",
            "#DEPRECATED: 1.0# _Z6fv@Base 1.0",
            "| libx1 #MINVER#",
            "* Build-Depends-Package: libx-dev",
            "",
        ])
        self.assertEqual(lint(text), [])
        self.assertEqual(problems("input/debian/libx1.symbols", HEADER, text), [])

    def test_rejected_forms_name_the_corrected_line(self):
        cases = {
            " (optional) _Z1fv@Base 1.0": " (optional)_Z1fv@Base 1.0",            # libbpp-qt rc3
            " (arch=!amd64)   _Z1fv@Base 1.0": " (arch=!amd64)_Z1fv@Base 1.0",
            " _Z1fv@Base 1.0 (optional)": " (optional)_Z1fv@Base 1.0",           # tag after the version
            ' (c++)"_Z1fv"@Base 2.4.1-6 (optional)': ' (c++|optional)"_Z1fv"@Base 2.4.1-6',  # libbpp-core rc3
            " _Z1fv@Base (optional) 2.4.1": " (optional)_Z1fv@Base 2.4.1",
            "(arch=!amd64) _Z1fv@Base 1.0": " (arch=!amd64)_Z1fv@Base 1.0",       # libbpp-core rc3b
            " _Z1fv@Base": None,                                                   # no version
            "#MISSING: 1.0# _Z1fv@Base 1.0": " _Z1fv@Base 1.0",
        }
        for line, fixed in cases.items():
            with self.subTest(line=line):
                found = syntax_guard.symbols_lint(HEADER + line + "\n")
                self.assertEqual(len(found), 1, found)
                self.assertEqual(found[0][2], fixed)

    def test_the_message_reports_the_whole_file_and_the_glued_line(self):
        before = HEADER + " _Z1fv@Base 1.0\n _Z2fv@Base 1.0\n _Z3fv@Base 1.0\n"
        after = (HEADER + " (optional) _Z1fv@Base 1.0\n _Z2fv@Base 1.0\n"
                 " (optional) _Z3fv@Base 1.0\n")
        [found] = problems("input/debian/libx1.symbols", before, after)
        self.assertIn("file has 2 unparseable lines: 2, 4", found.message)
        self.assertIn("' (optional)_Z1fv@Base 1.0'", found.message)
        self.assertEqual(found.line, 2)
        self.assertTrue(found.hard)

    def test_a_file_is_relinted_whole_even_when_the_bad_line_was_already_there(self):
        bad = HEADER + " (optional) _Z1fv@Base 1.0\n"
        found = problems("input/debian/libx1.symbols", bad, bad + " _Z2fv@Base 1.0\n")
        self.assertEqual(len(found), 1)
        self.assertIn("file has 1 unparseable lines: 2", found[0].message)

    def test_an_unchanged_resend_does_not_bypass_a_symbols_finding(self):
        path = "input/debian/libx1.symbols"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input/debian").mkdir(parents=True)
            (root / path).write_text(HEADER + " _Z1fv@Base 1.0\n")
            bad = edit(path, " _Z1fv@Base 1.0", " (optional) _Z1fv@Base 1.0")
            snapshot = CandidateSnapshot.capture(root, [path])
            found = syntax_guard.check_batch(snapshot, [bad])
            self.assertIsNotNone(found)
            allowed = {syntax_guard.edit_signature(bad)}
            again = syntax_guard.check_batch(snapshot, [bad], allow=allowed)
            self.assertIsNotNone(again, "an unchanged resend must still be blocked")
            index, found_problems, after = again
            text = syntax_guard.feedback(index, [bad], found_problems, after, "h")
            self.assertNotIn("resend it unchanged", text)
            self.assertIn("(optional)_Z1fv@Base 1.0", text)

    def test_a_soft_finding_can_still_be_resent(self):
        path = "input/a.c"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / path).write_text("int x;\n")
            bad = edit(path, "int x;\n", "#if A\nint x;\n")
            snapshot = CandidateSnapshot.capture(root, [path])
            self.assertIsNotNone(syntax_guard.check_batch(snapshot, [bad]))
            self.assertIsNone(syntax_guard.check_batch(snapshot, [bad],
                                                       allow={syntax_guard.edit_signature(bad)}))

    def test_an_impossible_arch_name_is_a_hard_block_too(self):
        [found] = problems("input/debian/libx1.symbols", HEADER + " _Z1fv@Base 1.0\n",
                           HEADER + " (arch=!aarch64)_Z1fv@Base 1.0\n")
        self.assertIn("arch=aarch64", found.message)
        self.assertTrue(found.hard)

    def test_a_wide_file_lints_in_bounded_time(self):
        text = HEADER + "".join(f" (optional) _ZN3bpp{i}fEv@Base 2.4.1\n" for i in range(20000))
        started = time.monotonic()
        self.assertEqual(len(syntax_guard.symbols_lint(text)), 20000)
        self.assertLess(time.monotonic() - started, 5)


class SymbolsNormaliser(unittest.TestCase):
    def test_whitespace_after_the_tag_group_is_removed_on_new_lines_only(self):
        before = HEADER + " _Z1fv@Base 1.0\n _Z2fv@Base 1.0\n (optional) _Z9fv@Base 1.0\n"
        after = (HEADER + " (optional) _Z1fv@Base 1.0\n (arch=!amd64)\t_Z2fv@Base 1.0\n"
                 " (optional) _Z9fv@Base 1.0\n")
        out = edit_applier.normalise_symbols("input/debian/libx1.symbols.amd64", before, after)
        self.assertEqual(out, HEADER + " (optional)_Z1fv@Base 1.0\n (arch=!amd64)_Z2fv@Base 1.0\n"
                              " (optional) _Z9fv@Base 1.0\n")     # the old line is not ours to touch

    def test_other_files_and_valid_lines_are_untouched(self):
        after = "a (b) c\n (optional)_Z1fv@Base 1.0\n"
        self.assertEqual(edit_applier.normalise_symbols("input/src/a.txt", "", after), after)
        self.assertEqual(edit_applier.normalise_symbols("input/debian/x.symbols", "", after), after)
        word = " (see note) not a symbol line\n"
        self.assertEqual(edit_applier.normalise_symbols("input/debian/x.symbols", "", word), word)

    def test_apply_edits_glues_the_tag(self):
        path = "input/debian/libx1.symbols.amd64"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input/debian").mkdir(parents=True)
            (root / path).write_text(HEADER + " _Z1fv@Base 1.0\n")
            n = edit_applier.apply_edits(root, [edit(path, " _Z1fv@Base 1.0", " (optional) _Z1fv@Base 1.0")])
            self.assertEqual(n, 1)
            self.assertEqual((root / path).read_text(), HEADER + " (optional)_Z1fv@Base 1.0\n")


class EditBudget(unittest.TestCase):
    def snapshot(self, files):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text)
        return CandidateSnapshot.capture(root, list(files))

    def test_changed_lines_count(self):
        self.assertEqual(changed_line_count("a\nb\nc\n", "a\nB\nc\n"), 1)
        self.assertEqual(changed_line_count("a\nb\n", "a\nb\nc\nd\n"), 2)
        self.assertEqual(changed_line_count("x\n", "x\n"), 0)
        big_old = "".join(f"l{i}\n" for i in range(3000))
        big_new = "".join(f"m{i}\n" for i in range(3000))
        self.assertEqual(changed_line_count(big_old, big_new), 3000)

    def test_symbols_edit_over_twice_the_missing_set_is_rejected(self):
        path = "input/debian/libx1.symbols.amd64"
        body = "".join(f" _ZN3bpp{i}fEv@Base 1.0\n" for i in range(60))
        snap = self.snapshot({path: HEADER + body})
        tag_all = edit(path, " _ZN3bpp", " (optional)_ZN3bpp", replace_all=True)
        self.assertEqual(inspect_batch(snap, [tag_all]), [])                    # default: rule is off
        self.assertEqual(len(inspect_batch(snap, [tag_all], missing_symbol_count=5)), 1)
        text = validate_batch(snap, [tag_all], missing_symbol_count=5)
        self.assertIn("symbols_edit_too_large", text)
        self.assertIn("@@recipe:symbols-tag@@", text)
        self.assertIn("allowed: 10", text)
        three = [edit(path, f" _ZN3bpp{i}fEv", f" (optional)_ZN3bpp{i}fEv") for i in range(3)]
        self.assertEqual(inspect_batch(snap, three, missing_symbol_count=5), [])
        self.assertEqual(inspect_batch(snap, three, missing_symbol_count=0), [])      # disabled

    def test_the_symbols_budget_is_cumulative_over_the_batch(self):
        path = "input/debian/libx1.symbols.amd64"
        snap = self.snapshot({path: HEADER + "".join(f" _ZN3bpp{i}fEv@Base 1.0\n" for i in range(60))})
        many = [edit(path, f" _ZN3bpp{i}fEv", f" (optional)_ZN3bpp{i}fEv") for i in range(12)]
        issues = inspect_batch(snap, many, missing_symbol_count=5)
        self.assertEqual([i[0] for i in issues], [10, 11])        # the 11th and 12th changed lines

    def test_a_large_single_edit_needs_many_sites(self):
        text = "".join(f"line {i}\n" for i in range(400))
        snap = self.snapshot({"input/src/a.txt": text})
        retype = edit("input/src/a.txt", text, text.replace("line", "item"))
        reason = inspect_batch(snap, [retype])[0][2]
        self.assertIn("edit_too_large", reason)
        self.assertIn("@@recipe:symbols-tag@@", reason)
        small = edit("input/src/a.txt", "line 3\n", "item 3\n")
        self.assertEqual(inspect_batch(snap, [small]), [])
        at_limit = edit("input/src/a.txt", "line 1", "item 1", replace_all=True)   # 111 sites of "line 1"
        self.assertGreaterEqual(text.count("line 1"), 100)
        self.assertEqual(inspect_batch(snap, [at_limit]), [])
        flag = edit("input/src/a.txt", "line", "item", replace_all=True)
        self.assertEqual(inspect_batch(snap, [flag]), [])                  # 400 sites
        few = "".join(f"line {i}\n" for i in range(150)) + "other\n" * 100
        snap2 = self.snapshot({"input/src/b.txt": few})
        mixed = edit("input/src/b.txt", few, few.replace("line", "item"))
        self.assertEqual(inspect_batch(snap2, [mixed]), [])                # 150 lines: under the limit

    def test_the_rc3b_retype_is_rejected_by_both_rules(self):
        path = "input/debian/libx1.symbols.amd64"
        old = HEADER + "".join(f" _ZN3bpp{i}fEv@Base 2.4.1\n" for i in range(4130))
        new = HEADER + "".join(f"(arch=!amd64) _ZN3bpp{i}fEv@Base 2.4.1\n" for i in range(4130))
        snap = self.snapshot({path: old})
        reason = inspect_batch(snap, [edit(path, old, new)], missing_symbol_count=12)[0][2]
        self.assertIn("symbols_edit_too_large", reason)
        self.assertIn("4130", reason)
        self.assertEqual(len(lint(new)), 4130)
        [found] = problems(path, old, new)
        self.assertIn("file has 4130 unparseable lines: 2, 3, 4", found.message)


class VerifyVerdicts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def diagnostic(self, message, path, line, quote):
        return Diagnostic(message, "build", path, line, [f"input/{path}"], quote, 0, "")

    def test_a_vanished_line_is_unknown_not_fixed(self):
        self.write("input/src/a.c", "int x;\nint y;\n")
        item = self.diagnostic("error: 'foo' undeclared", "src/a.c", 2, "foo(1);")
        [v] = verify.predict(self.root, [item], AMD, changed={"input/src/a.c"})
        self.assertIsNone(v.ok)
        self.assertIn("no longer in the file", v.text)
        self.assertIn("[unknown]", verify.render([v], AMD))

    def test_an_include_that_vanishes_is_unknown_too(self):
        self.write("input/src/a.h", "int y;\n")
        item = self.diagnostic("fatal error: xmmintrin.h: No such file or directory", "src/a.h", 1,
                               "#include <xmmintrin.h>")
        [v] = verify.predict(self.root, [item], ARM, changed={"input/src/a.h"})
        self.assertIsNone(v.ok)

    def test_the_decidable_constructs_still_say_fixed_and_not_fixed(self):
        header = "#if defined(__x86_64__)\n#include <xmmintrin.h>\n#endif\n"
        item = self.diagnostic("fatal error: xmmintrin.h: No such file or directory", "src/a.h", 2,
                               "#include <xmmintrin.h>")
        self.write("input/src/a.h", header)
        [v] = verify.predict(self.root, [item], ARM, changed={"input/src/a.h"})
        self.assertIs(v.ok, True)
        [v] = verify.predict(self.root, [item], AMD, changed={"input/src/a.h"})
        self.assertIs(v.ok, False)

    def test_an_unchanged_ordinary_line_is_not_a_verdict(self):
        self.write("input/src/a.c", "int x;\nint y = foo(1);\n")
        item = self.diagnostic("error: void value not ignored", "src/a.c", 2, "int y = foo(1);")
        [v] = verify.predict(self.root, [item], AMD, changed={"input/src/a.c"},
                             originals={"input/src/a.c": "int x;\nint y = foo(1);\n"})
        self.assertIsNone(v.ok)

    LOG = ["dpkg-gensymbols: error: some symbols or patterns disappeared in the symbols file: see diff output below",
           "-#MISSING: 1.0# _ZN3bppA@Base 1.0", "- _ZN3bppB@Base 1.0"]
    PATH = "input/debian/libp.symbols.amd64"
    BASE = "libp.so.1 libp #MINVER#\n _ZN3bppA@Base 1.0\n _ZN3bppB@Base 1.0\n _ZN3bppC@Base 1.0\n _ZN3bppD@Base 1.0\n"

    def symbols(self, text, original=None):
        self.write(self.PATH, text)
        return verify.predict(self.root, [], AMD, changed={self.PATH}, log_lines=self.LOG,
                              originals={self.PATH: self.BASE if original is None else original})

    def test_tagging_exactly_the_missing_set_is_fixed_and_says_what_was_checked(self):
        [v] = self.symbols(self.BASE.replace(" _ZN3bppA", " (optional)_ZN3bppA")
                               .replace(" _ZN3bppB", " (optional)_ZN3bppB"))
        self.assertIs(v.ok, True)
        self.assertIn("every reported-missing symbol tagged", v.text)
        self.assertIn("nothing else changed", v.text)

    def test_over_tagging_is_not_fixed(self):
        text = self.BASE.replace(" _ZN3bpp", " (optional)_ZN3bpp")
        vs = self.symbols(text)
        self.assertEqual([v.ok for v in vs], [False])
        self.assertIn("over-tagging", vs[0].text)
        self.assertIn("not in the log's list of 2 missing symbols", vs[0].text)

    def test_removing_a_line_instead_of_tagging_it_is_not_fixed(self):
        text = self.BASE.replace(" _ZN3bppA@Base 1.0\n", "").replace(" _ZN3bppB", " (optional)_ZN3bppB")
        [v] = self.symbols(text)
        self.assertIs(v.ok, False)
        self.assertIn("removed or retyped", v.text)

    def test_the_misplaced_tag_and_unparseable_lines_are_found(self):
        vs = self.symbols(self.BASE.replace(" _ZN3bppA@Base 1.0", " _ZN3bppA@Base 1.0 (optional)")
                              .replace(" _ZN3bppB@Base 1.0", " (optional) _ZN3bppB@Base 1.0"))
        texts = " ".join(v.text for v in vs)
        self.assertTrue(all(v.ok is False for v in vs))
        self.assertIn("file has 2 unparseable lines: 2, 3", texts)
        self.assertIn("is not a valid version", texts)

    def test_rules_report_unknown_buildflags_tokens(self):
        self.write("input/debian/rules",
                   "#!/usr/bin/make -f\nexport DEB_BUILD_MAINT_OPTIONS = noopt nolto hardening=+all\n"
                   "export DEB_CFLAG_MAINT_APPEND = -O1\nexport DEB_LDFLAGS_MAINT_ADD = -x\n"
                   "export DEB_CFLAGS_MAINT_APPEND = -O1\n%:\n\tdh $@\n")
        vs = verify._rules(self.root, "", ARM, {"input/debian/rules"}, None)
        bad = [v.text for v in vs if v.ok is False]
        self.assertTrue(any("'noopt' is a DEB_BUILD_OPTIONS token" in t for t in bad), bad)
        self.assertTrue(any("'nolto'" in t for t in bad), bad)
        self.assertTrue(any("DEB_CFLAG_MAINT_APPEND is not a dpkg-buildflags variable" in t for t in bad), bad)
        self.assertTrue(any("DEB_LDFLAGS_MAINT_ADD" in t for t in bad), bad)
        self.assertFalse(any("DEB_CFLAGS_MAINT_APPEND is not" in t for t in bad))
        self.write("input/debian/rules",
                   "#!/usr/bin/make -f\nexport DEB_BUILD_MAINT_OPTIONS = hardening=+all,-pie optimize=-lto\n"
                   "export DEB_CFLAGS_MAINT_APPEND = -O1\n%:\n\tdh $@\n")
        vs = verify._rules(self.root, "", ARM, {"input/debian/rules"}, None)
        self.assertFalse([v for v in vs if v.ok is False])


class BuildflagsTable(unittest.TestCase):
    def test_valid_and_invalid_values(self):
        self.assertEqual(rules_guard.maint_option_problems("hardening=+all,-pie optimize=-lto qa=+bug"), [])
        self.assertEqual(rules_guard.maint_option_problems("future=+lfs reproducible=+all abi=+whatever"), [])
        self.assertEqual(rules_guard.maint_option_problems("hardening=$(HARDEN) $(OTHER)"), [])
        for bad in ("noopt", "nolto", "parallel=2", "lto", "optimize=+fast", "speed=+on", "hardening="):
            with self.subTest(bad=bad):
                self.assertTrue(rules_guard.maint_option_problems(bad), bad)

    def test_variable_names(self):
        for good in ("DEB_CFLAGS_MAINT_APPEND", "DEB_LDFLAGS_MAINT_STRIP", "DEB_CXXFLAGS_MAINT_SET",
                     "DEB_CPPFLAGS_MAINT_PREPEND", "DEB_CFLAGS_FOR_BUILD_MAINT_APPEND",
                     "DEB_BUILD_MAINT_OPTIONS"):
            self.assertTrue(rules_guard.maint_variable_valid(good), good)
        for bad in ("DEB_CFLAG_MAINT_APPEND", "DEB_CFLAGS_MAINT_ADD", "DEB_FLAGS_MAINT_APPEND",
                    "DEB_CFLAGS_APPEND"):
            self.assertFalse(rules_guard.maint_variable_valid(bad), bad)

    def test_buildflags_problems_gives_line_numbers(self):
        text = "a\nexport DEB_BUILD_MAINT_OPTIONS += noopt\nexport DEB_CFLAGS_MAINT_APPEND = -O1\n"
        [(line, message)] = rules_guard.buildflags_problems(text)
        self.assertEqual(line, 2)
        self.assertIn("noopt", message)


class RulesGuard(unittest.TestCase):
    RULES = "input/debian/rules"

    def check(self, old, new, path=RULES):
        return rules_guard.check(path, old, new)

    def test_html_xml_utils_v032_flags_edit_is_refused(self):
        why = self.check("override_dh_auto_install:\n",
                         '\noverride_dh_auto_build:\n\tdh_auto_build -- CFLAGS="-fno-lto" LDFLAGS="-fno-lto"\n\n'
                         "override_dh_auto_install:\n")
        self.assertIn("CFLAGS=", why)
        self.assertIn("DEB_CFLAGS_MAINT_APPEND", why)

    def test_flag_assignments_on_build_commands_are_refused(self):
        for line in ('\t$(MAKE) CXXFLAGS=-O0', '\tdh_auto_configure -- LDFLAGS=-Wl,--no-as-needed',
                     '\tmake -C src CFLAGS="-g"', '\tdh_auto_build -- \\\n\t  CFLAGS=-O0'):
            with self.subTest(line=line):
                self.assertIsNotNone(self.check("x\n", f"x\n{line}\n"))

    def test_flag_assignments_that_keep_the_build_flags_are_allowed(self):
        for line in ('\tdh_auto_build -- CFLAGS="$(CFLAGS) -fno-lto"',
                     '\tdh_auto_build -- CFLAGS="$$(dpkg-buildflags --get CFLAGS) -O1"',
                     '\t./configure CFLAGS=-O2', '\tdh_auto_build -- V=1',
                     'CFLAGS += -O1'):
            with self.subTest(line=line):
                self.assertIsNone(self.check("x\n", f"x\n{line}\n"))

    def test_noopt_and_nolto_in_maint_options_are_refused(self):
        why = self.check("#export DH_VERBOSE=1\n", "#export DH_VERBOSE=1\nexport DEB_BUILD_MAINT_OPTIONS += noopt nolto\n")
        self.assertIn("noopt, nolto are DEB_BUILD_OPTIONS token(s)", why)
        self.assertIn("optimize=-lto", why)
        self.assertIsNone(self.check("a\n", "a\nexport DEB_BUILD_MAINT_OPTIONS = optimize=-lto hardening=+all\n"))

    def test_true_after_cleanup_commands_is_allowed_but_not_after_build_or_test(self):
        for line in ("\trm -f debian/tmp/foo || true", "\t-rm -rf build || true", "\tmkdir -p out || true",
                     "\tln -s a b || :", "\tdh_clean || true", "\t$(RM) x || true",
                     "\ttest -d x && rm -rf x || true",
                     "\tfor f in a b; do rm -f $$f || true; done"):
            with self.subTest(line=line):
                self.assertIsNone(self.check("x\n", f"x\n{line}\n"), line)
        for line in ("\tdh_auto_build || true", "\tdh_auto_test || true", "\t$(MAKE) check || true",
                     "\tmake && rm x || true", "\tpytest || exit 0", "\trm x; make || true"):
            with self.subTest(line=line):
                self.assertIsNotNone(self.check("x\n", f"x\n{line}\n"), line)

    def test_dash_prefixed_dh_auto_test_is_refused(self):
        for line in ("\t-dh_auto_test", "\t@-dh_auto_test", "\t- dh_auto_test"):
            with self.subTest(line=line):
                self.assertIsNotNone(self.check("override_dh_auto_test:\n\tdh_auto_test\n",
                                                f"override_dh_auto_test:\n{line}\n"))

    def test_existing_refusals_still_hold(self):
        self.assertIsNotNone(self.check("a\n", "a\n\tdh_auto_test || true\n"))
        self.assertIsNotNone(self.check("a\n", "a\nexport DEB_BUILD_OPTIONS += nocheck\n"))
        self.assertIsNotNone(self.check("", "", path="input/debian/patches/x.patch"))


class ReplayOracles(unittest.TestCase):
    """Every edit the validator accepted must pass every gate: zero blocks."""

    def gate(self, path, old, new, replace_all=False):
        found = []
        why = rules_guard.check(path, old, new)
        if why:
            found.append(("rules_guard", why))
        found += [("syntax_guard", p.message) for p in problems(path, old, new) if p.hard]
        lines = changed_line_count(old, new)
        if lines > MAX_EDIT_LINES and not (replace_all and old.count(old) >= 100):
            found.append(("size", lines))
        found += [("flags", m) for _, m in rules_guard.buildflags_problems(new)
                  if "is not a dpkg-buildflags" in m or "DEB_BUILD_OPTIONS token" in m
                  or "not area=feature" in m]
        return found

    def test_there_are_replays(self):
        self.assertGreaterEqual(len(REPLAYS), 9)

    def test_every_verified_replay_edit_produces_no_block(self):
        for replay in REPLAYS:
            data = json.loads(Path(replay).read_text())
            for item in data["edits"]:
                with self.subTest(replay=Path(replay).name, path=item["path"]):
                    self.assertEqual(self.gate(item["path"], item["old_text"], item["new_text"],
                                               bool(item.get("replace_all"))), [])

    def test_the_libbpp_qt_replay_applies_through_the_whole_batch(self):
        data = json.loads(Path(ROOT / "harness/replays/libbpp-qt-verified-optional-templinst.json").read_text())
        path = data["edits"][0]["path"]
        before = HEADER + "".join(e["old_text"] + "\n" for e in data["edits"]) + " _Zother@Base 1.0\n"
        after = before
        for e in data["edits"]:
            after = after.replace(e["old_text"], e["new_text"], 1)
        self.assertEqual(problems(path, before, after), [])


@unittest.skipUnless(HAVE_EVIDENCE, "evidence export not present")
class RecordedRuns(unittest.TestCase):
    def diff(self, label):
        return (EVIDENCE / label / "repair.diff").read_text()

    def organizer_log(self, short):
        return [l for l in next(EVIDENCE.joinpath("_organizer-logs").glob(f"*-{short}-*.log"))
                .read_text(errors="replace").split("\n")]

    def pristine_symbols(self, short, label, name):
        """The symbols file as the agent first saw it: the source tree after the
        first run's edits, with that run's diff reversed."""
        current = (EVIDENCE / short / "source/debian" / name).read_text()
        return apply_unified(current, self.diff(f"{short}/runs/{label}"), f"input/debian/{name}", reverse=True)

    def test_every_repaired_run_without_a_known_flaw_produces_no_hard_block(self):
        checked = 0
        for path in sorted(glob.glob(str(EVIDENCE / "*/runs/*-repaired*/repair.diff"))):
            label = str(Path(path).parent.relative_to(EVIDENCE))
            if label in KNOWN_BAD:
                continue
            for file, parts in hunks(Path(path).read_text()).items():
                for old, new in parts:
                    old_text, new_text = "\n".join(old), "\n".join(new)
                    with self.subTest(run=label, file=file):
                        self.assertIsNone(rules_guard.check(file, old_text, new_text))
                        self.assertEqual([p.message for p in problems(file, old_text, new_text) if p.hard], [])
                        self.assertLessEqual(changed_line_count(old_text, new_text), MAX_EDIT_LINES)
                        checked += 1
        self.assertGreaterEqual(checked, 20)

    def test_libbpp_qt_rc3_is_blocked_and_the_verified_form_passes(self):
        label = "libbpp-qt/runs/rc3-repaired"
        name = "libbpp-qt2t64.symbols.amd64"
        before = self.pristine_symbols("libbpp-qt", "rc3-repaired", name)
        after = apply_unified(before, self.diff(label), f"input/debian/{name}")
        [found] = problems(f"input/debian/{name}", before, after)
        self.assertIn("file has 2 unparseable lines", found.message)
        self.assertTrue(found.hard)
        replay = json.loads((ROOT / "harness/replays/libbpp-qt-verified-optional-templinst.json").read_text())
        good = before
        for item in replay["edits"]:
            good = good.replace(item["old_text"], item["new_text"], 1)
        self.assertEqual(problems(f"input/debian/{name}", before, good), [])
        self.assertEqual(lint(before), [])
        missing = patterns.missing_symbols(self.organizer_log("libbpp-qt"))
        self.assertEqual(len(missing), 2)
        self.assertEqual(changed_line_count(before, good), 2)

    def test_libbpp_core_retypes_are_blocked_and_the_macro_run_passes(self):
        name = "libbpp-core4.symbols.amd64"
        path = f"input/debian/{name}"
        before = self.pristine_symbols("libbpp-core", "rc3-miss-retyped-file", name)
        self.assertEqual(lint(before), [])
        missing = len(patterns.missing_symbols(self.organizer_log("libbpp-core")))
        self.assertEqual(missing, 12)
        for label in ("rc3-miss-retyped-file", "rc3b-degenerate-pass"):
            with self.subTest(run=label):
                after = apply_unified(before, self.diff(f"libbpp-core/runs/{label}"), path)
                found = problems(path, before, after)
                self.assertTrue(found and found[0].hard)
                self.assertIn("unparseable lines", found[0].message)
                self.assertGreater(len(lint(after)), 4000)
                self.assertGreater(changed_line_count(before, after), 2 * missing)
        macro = apply_unified(before, self.diff("libbpp-core/runs/rc3c-repaired-macro"), path)
        self.assertEqual(problems(path, before, macro), [])
        self.assertLessEqual(changed_line_count(before, macro), 2 * missing)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input/debian").mkdir(parents=True)
            (root / path).write_text(before)
            snap = CandidateSnapshot.capture(root, [path])
            retype = edit(path, before, apply_unified(before, self.diff("libbpp-core/runs/rc3b-degenerate-pass"), path))
            self.assertIn("symbols_edit_too_large",
                          inspect_batch(snap, [retype], missing_symbol_count=missing)[0][2])

    def test_html_xml_utils_v032_is_refused(self):
        refused = []
        for file, parts in hunks(self.diff("html-xml-utils/runs/v032-repaired")).items():
            for old, new in parts:
                why = rules_guard.check(file, "\n".join(old), "\n".join(new))
                if why:
                    refused.append(why)
        self.assertTrue(any("noopt" in w for w in refused), refused)
        self.assertTrue(any("CFLAGS=" in w for w in refused), refused)


if __name__ == "__main__":
    unittest.main()
