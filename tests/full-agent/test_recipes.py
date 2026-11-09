"""Recipe registry (src/recipes): each verified repair is recognised from its
case's log, not from the other ten, and expands on the case's pristine tree to
the edit the real validator accepted (harness/replays/*-verified-*.json, or the
agent's own accepted diff under fixtures/recipes/oracles). No model, network or
build.

Fixtures (fixtures/recipes) are excerpts of the organizers' logs and the
pristine files the recipes read; the trees were rebuilt by reversing the first
run's diff of each evidence case. When the full organizer logs are available
(BB_EVIDENCE_DIR, default: this session's evidence export) the same matrix is
run on them too.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent -p test_recipes.py
"""

import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import edit_repair, patterns  # noqa: E402
from src.recipes import registry, rules_mk  # noqa: E402
from src.types import Edit  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "recipes"
REPLAYS = ROOT / "harness" / "replays"
FULL_LOGS = Path(os.environ.get(
    "BB_EVIDENCE_DIR",
    "/private/tmp/claude-501/-Users-owenzhang-Downloads-Build-Bench-Project/"
    "2210bc37-be95-42e4-9f36-c14dd9f4e919/scratchpad/evidence")) / "_organizer-logs"

TARGET = {"libbpp-core": "amd64", "rcran": "arm64", "ectrans": "arm64", "libmceliece": "arm64",
          "libbpp-qt": "amd64", "python-intbitset": "amd64", "libunwind": "arm64",
          "rust-subversion": "arm64", "libevtx": "amd64", "html-xml-utils": "arm64", "pybigwig": "amd64"}
LOG_KEY = {"rcran": "r-cran-digest"}
EXPECTED = {"libbpp-core": {"symbols-tag"}, "libbpp-qt": {"symbols-tag"},
            "libmceliece": {"strip-bsymbolic-functions"}, "libevtx": {"o2-on-test-segfault", "optimize-guard-test-memory"},
            "html-xml-utils": {"serial-helper-build"}, "ectrans": {"cmake-redirect-imported-dirs"},
            "python-intbitset": {"replace-removed-command"}, "libunwind": {"arch-flags"},
            "pybigwig": {"static-archive-shim"}, "rcran": set(), "rust-subversion": set()}


def log_lines(case):
    return (FIXTURES / "logs" / f"{case}.log").read_text(encoding="utf-8").splitlines()


def tree(case) -> Path:
    path = FIXTURES / "trees" / case
    return path if path.is_dir() else Path(tempfile.gettempdir()) / "bb-no-such-tree"


def replay(name):
    return [Edit(e["path"], e["old_text"], e["new_text"]) for e in json.loads((REPLAYS / name).read_text())["edits"]]


def norm(text):
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def apply(worktree, edits):
    """The files an edit list produces, each old_text matching exactly once."""
    files = {}
    for edit in edits:
        text = files.get(edit.path)
        if text is None:
            text = (Path(worktree) / edit.path).read_text(encoding="utf-8")
        assert text.count(edit.old_text) == 1, (edit.path, edit.old_text[:60], text.count(edit.old_text))
        files[edit.path] = text.replace(edit.old_text, edit.new_text, 1)
    return files


def apply_diff(worktree, diff_name):
    """Apply a small unified diff (hunk text matched by content, not line number)."""
    files, current = {}, None
    hunks = {}
    for line in (FIXTURES / "oracles" / diff_name).read_text(encoding="utf-8").split("\n"):
        if line.startswith("--- a/"):
            current = line[6:]
            hunks[current] = []
        elif line.startswith(("+++ ", "diff ", "index ")):
            continue
        elif line.startswith("@@"):
            hunks[current].append(([], []))
        elif current and hunks[current] and line[:1] in (" ", "-", "+"):
            old, new = hunks[current][-1]
            if line[0] in " -":
                old.append(line[1:])
            if line[0] in " +":
                new.append(line[1:])
    for path, items in hunks.items():
        text = (Path(worktree) / path).read_text(encoding="utf-8")
        for old, new in items:
            old_text = "\n".join(old) + "\n"
            assert text.count(old_text) == 1, (path, old[:2])
            text = text.replace(old_text, "\n".join(new) + "\n", 1)
        files[path] = text
    return files


def expand(case, recipe_id, new_text="", path="input/debian/rules", log=None, arch=None):
    edit = Edit(path, registry.macro_token(recipe_id), new_text)
    return edit_repair.expand_macros([edit], tree(case), log if log is not None else log_lines(case),
                                     arch if arch is not None else TARGET[case])


class Recognition(unittest.TestCase):
    def test_each_case_gets_its_recipes_and_no_other(self):
        for case, expected in EXPECTED.items():
            found = {recipe.id for recipe, _ in registry.menu(log_lines(case), tree(case), TARGET[case])}
            self.assertEqual(found, expected, case)

    def test_the_menu_ranks_by_distance_from_the_first_fatal_error(self):
        menu = registry.menu(log_lines("libevtx"), tree("libevtx"), "amd64")
        self.assertEqual([r.id for r, _ in menu][0], "o2-on-test-segfault")
        self.assertTrue(all(m.offset is not None and m.offset >= 0 for _, m in menu))

    def test_every_recipe_has_a_tier_and_a_one_line_note(self):
        self.assertEqual(len(registry.ids()), 9)
        for recipe_id in registry.ids():
            recipe = registry.get(recipe_id)
            self.assertIn(recipe.tier, registry.TIERS)
            self.assertNotIn("\n", recipe.note)
        self.assertEqual(registry.get("static-archive-shim").tier, "verified-template")

    @unittest.skipUnless(FULL_LOGS.is_dir(), "organizer logs not available")
    def test_the_full_organizer_logs_give_the_same_matrix(self):
        for case, expected in EXPECTED.items():
            path = next(FULL_LOGS.glob(f"*-{LOG_KEY.get(case, case)}-*.log"))
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            found = {r.id for r, _ in registry.menu(lines, tree(case), TARGET[case])}
            self.assertEqual(found, expected, case)

    def test_the_wrong_architecture_is_not_recognised(self):
        self.assertEqual({r.id for r, _ in registry.menu(log_lines("libevtx"), tree("libevtx"), "arm64")},
                         {"optimize-guard-test-memory"})  # the -O2 recipe is amd64/ppc64el only
        self.assertEqual(registry.menu(log_lines("libmceliece"), tree("libmceliece"), "amd64")[0][0].id,
                         "strip-bsymbolic-functions")  # the BFD assertion names aarch64 itself


class SymbolsTag(unittest.TestCase):
    PATH = "input/debian/libbpp-qt2t64.symbols.amd64"

    def test_libbpp_qt_equals_the_verified_replay(self):
        edits, notes = expand("libbpp-qt", "symbols-tag", "auto", self.PATH)
        self.assertEqual([(e.path, e.old_text, e.new_text) for e in edits],
                         [(e.path, e.old_text, e.new_text) for e in replay("libbpp-qt-verified-optional-templinst.json")])
        self.assertTrue(notes[0].startswith("Recipe symbols-tag expanded: tagged 2 of the 2 symbols"), notes)

    def test_legacy_alias_still_works(self):
        edit = Edit(self.PATH, patterns.SYMBOLS_MACRO, "")
        edits, notes = edit_repair.expand_macros([edit], tree("libbpp-qt"), log_lines("libbpp-qt"), "amd64")
        self.assertEqual(len(edits), 2)
        self.assertIn("Recipe symbols-tag expanded", notes[0])

    def test_libbpp_core_tags_the_same_lines_the_accepted_run_changed(self):
        path = "input/debian/libbpp-core4.symbols.amd64"
        edits, notes = expand("libbpp-core", "symbols-tag", "auto", path)
        diff = (FIXTURES / "oracles" / "libbpp-core-rc3c.diff").read_text().split("\n")
        accepted = {l[1:] for l in diff if l.startswith("-") and not l.startswith("---")}
        self.assertEqual(len(accepted), 12)
        self.assertEqual({e.old_text for e in edits}, accepted)  # the same 12 lines, not 4,130
        by_old = {e.old_text: e.new_text for e in edits}
        for old, new in by_old.items():
            if "_ZNSt" in old:
                self.assertIn("(optional=templinst)", new)
            self.assertRegex(new, r"^ \((optional|optional=templinst)\)_Z")  # glued, no space after ")"
        self.assertIn("(optional)_ZNK3bpp8RGBColorltERKS0_@Base", " ".join(by_old.values()))

    def test_tag_choice_follows_the_symbol_and_the_file(self):
        from src.recipes import symbols_tag as s
        self.assertEqual(s.choose_tag("_ZNSt6vectorIiE9push_backEi@Base", "", "input/debian/x.symbols", "arm64"),
                         "optional=templinst")
        self.assertEqual(s.choose_tag("_ZN9QtPrivate8RefCount5derefEv@Base", "", "input/debian/x.symbols.amd64", "amd64"),
                         "optional=templinst")
        self.assertEqual(s.choose_tag('bpp::A<int>::f()@Base"', ' (c++)"bpp::A<int>::f()@Base" 1', "input/debian/x.symbols", "arm64"),
                         "optional=templinst")
        self.assertEqual(s.choose_tag("_ZN3foo3barEv@Base", "", "input/debian/x.symbols.amd64", "amd64"), "optional")
        self.assertEqual(s.choose_tag("_ZN3foo3barEv@Base", "", "input/debian/x.symbols", "arm64"), "arch=!arm64")
        self.assertEqual(s.choose_tag("_ZN3foo3barEv@Base", "", "input/debian/x.symbols", ""), "optional")

    def test_an_explicit_tag_is_honoured(self):
        edits, _ = expand("libbpp-qt", "symbols-tag", "optional", self.PATH)
        self.assertTrue(all(e.new_text.startswith(" (optional)_Z") for e in edits))

    def test_the_harmless_new_symbols_warning_alone_does_not_fire(self):
        lines = [l for l in log_lines("libbpp-qt") if "disappeared" not in l]
        self.assertIsNone(registry.get("symbols-tag").recognise(lines, tree("libbpp-qt"), "amd64"))
        self.assertEqual(patterns.debian_symbols(lines, tree("libbpp-qt"), "amd64"), "")

    def test_refuses_when_the_named_file_is_not_a_symbols_file_or_has_none_of_them(self):
        edits, notes = expand("libbpp-qt", "symbols-tag", "auto", "input/debian/rules")
        self.assertEqual(len(edits), 2)  # not a symbols file: the one dpkg named is used
        self.assertTrue(all(e.path.endswith("libbpp-qt2t64.symbols.amd64") for e in edits))
        edits, notes = expand("libbpp-core", "symbols-tag", "auto", "input/debian/libbpp-core4.symbols.amd64",
                              log=["unrelated"])
        self.assertEqual(edits, [])
        self.assertIn("refused", notes[0])


class StripBsymbolic(unittest.TestCase):
    def test_equals_the_verified_replay(self):
        edits, notes = expand("libmceliece", "strip-bsymbolic-functions")
        self.assertEqual([(e.path, e.old_text, e.new_text) for e in edits],
                         [(e.path, e.old_text, e.new_text) for e in replay("libmceliece-verified-strip-bsymbolic-functions.json")])
        self.assertIn("Recipe strip-bsymbolic-functions expanded", notes[0])

    def test_only_this_flag_is_proposed(self):
        edits, _ = expand("libmceliece", "strip-bsymbolic-functions")
        text = edits[0].new_text
        for banned in ("bindnow", "-z,now", "lto", "-fPIC", "hardening"):
            self.assertNotIn(banned, text.replace("hardening=+all", ""))

    def test_not_a_shared_link_is_not_this_signature(self):
        lines = ["gcc -o prog main.o", "/usr/bin/ld: BFD (GNU Binutils for Ubuntu) 2.41 assertion fail ../../bfd/elfnn-aarch64.c:9754"]
        self.assertIsNone(registry.get("strip-bsymbolic-functions").recognise(lines, tree("libmceliece"), "arm64"))

    def test_refuses_when_already_stripped_and_merges_with_an_existing_strip(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "input/debian/rules"
            rules.parent.mkdir(parents=True)
            rules.write_text("export DEB_LDFLAGS_MAINT_STRIP = -Wl,-Bsymbolic-functions\n%:\n\tdh $@\n")
            edit = Edit("input/debian/rules", "@@recipe:strip-bsymbolic-functions@@", "")
            edits, notes = edit_repair.expand_macros([edit], temp, log_lines("libmceliece"), "arm64")
            self.assertEqual(edits, [])
            self.assertIn("already strips", notes[0])
            rules.write_text("export DEB_LDFLAGS_MAINT_STRIP = -Wl,-z,relro\n%:\n\tdh $@\n")
            edits, _ = edit_repair.expand_macros([edit], temp, log_lines("libmceliece"), "arm64")
            self.assertIn("export DEB_LDFLAGS_MAINT_STRIP += -Wl,-Bsymbolic-functions", edits[0].new_text)

    def test_the_arm64_guard_is_available_on_request(self):
        edits, _ = expand("libmceliece", "strip-bsymbolic-functions", "arm64")
        self.assertIn("ifeq ($(DEB_HOST_ARCH),arm64)\nexport DEB_LDFLAGS_MAINT_STRIP = -Wl,-Bsymbolic-functions\nendif",
                      edits[0].new_text)


class O2OnTestSegfault(unittest.TestCase):
    def test_equals_the_verified_replay(self):
        edits, notes = expand("libevtx", "o2-on-test-segfault")
        oracle = replay("libevtx-verified-o2-on-amd64.json")
        self.assertEqual(norm(edits[0].new_text), norm(oracle[0].new_text))
        self.assertEqual(edits[0].old_text, oracle[0].old_text)
        self.assertEqual(apply(tree("libevtx"), edits), apply(tree("libevtx"), oracle))
        self.assertIn("-O3", edits[0].new_text)

    def test_optimize_guard_equals_the_verified_replay(self):
        edits, notes = expand("libevtx", "optimize-guard-test-memory", "", "input/tests/evtx_test_memory.h")
        oracle = replay("libevtx-verified-optimize-guard.json")
        self.assertEqual([(e.path, norm(e.old_text), norm(e.new_text)) for e in edits],
                         [(e.path, norm(e.old_text), norm(e.new_text)) for e in oracle])
        self.assertEqual(apply(tree("libevtx"), edits), apply(tree("libevtx"), oracle))

    def test_needs_o3_a_test_segfault_and_an_architecture_where_it_applies(self):
        recipe = registry.get("o2-on-test-segfault")
        lines = log_lines("libevtx")
        self.assertIsNotNone(recipe.recognise(lines, tree("libevtx"), "amd64"))
        self.assertIsNone(recipe.recognise([l.replace("-O3", "-O2") for l in lines], tree("libevtx"), "amd64"))
        self.assertIsNone(recipe.recognise([l for l in lines if "Segmentation" not in l and "139" not in l and "core dumped" not in l],
                                           tree("libevtx"), "amd64"))
        self.assertIsNone(recipe.recognise(lines, tree("libevtx"), "arm64"))
        self.assertIsNotNone(recipe.recognise(lines, tree("libevtx"), "ppc64el"))

    def test_the_cxx_twin_only_for_a_cpp_package(self):
        lines = log_lines("libevtx") + ["g++ -DHAVE_CONFIG_H -O3 -c foo.cpp -o foo.o"]
        edits, _ = expand("libevtx", "o2-on-test-segfault", log=lines)
        self.assertIn("DEB_CXXFLAGS_MAINT_STRIP", edits[0].new_text)
        edits, _ = expand("libevtx", "o2-on-test-segfault")
        self.assertNotIn("CXXFLAGS", edits[0].new_text)

    def test_refuses_when_o3_is_already_stripped(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "input/debian/rules"
            rules.parent.mkdir(parents=True)
            rules.write_text("export DEB_CFLAGS_MAINT_STRIP = -O3\n%:\n\tdh $@\n")
            edits, notes = edit_repair.expand_macros([Edit("x", "@@recipe:o2-on-test-segfault@@", "")], temp,
                                                     log_lines("libevtx"), "amd64")
            self.assertEqual(edits, [])
            self.assertIn("already strips -O3", notes[0])

    def test_an_existing_assignment_is_extended_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "input/debian/rules"
            rules.parent.mkdir(parents=True)
            rules.write_text("export DEB_BUILD_MAINT_OPTIONS = hardening=+all\nexport DEB_CFLAGS_MAINT_APPEND = -Wall\n%:\n\tdh $@\n")
            edits, _ = edit_repair.expand_macros([Edit("x", "@@recipe:o2-on-test-segfault@@", "")], temp,
                                                 log_lines("libevtx"), "amd64")
            self.assertIn("export DEB_CFLAGS_MAINT_APPEND += -O2", edits[0].new_text)


class SerialHelper(unittest.TestCase):
    def test_equals_the_verified_replay(self):
        edits, notes = expand("html-xml-utils", "serial-helper-build")
        oracle = replay("html-xml-utils-verified-serial-cexport.json")
        self.assertEqual([(e.path, e.old_text, e.new_text) for e in edits],
                         [(e.path, e.old_text, e.new_text) for e in oracle])
        self.assertIn("cexport", notes[0])

    def test_uses_tabs_and_never_strips_lto(self):
        text = expand("html-xml-utils", "serial-helper-build")[0][0].new_text
        self.assertIn("\n\tdh_auto_build -- cexport\n\tdh_auto_build\n", text)
        for banned in ("lto", "-O0", "noopt", "hardening"):
            self.assertNotIn(banned, text)

    def test_needs_the_helper_in_the_log(self):
        recipe = registry.get("serial-helper-build")
        quiet = [l for l in log_lines("html-xml-utils") if "cexport" not in l and "lto" not in l]
        self.assertIsNone(recipe.recognise(quiet, tree("html-xml-utils"), "arm64"))

    def test_merges_with_an_existing_override_keeping_its_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "input/debian/rules"
            rules.parent.mkdir(parents=True)
            rules.write_text("%:\n\tdh $@\n\noverride_dh_auto_build:\n\tdh_auto_build -- CFLAGS=\"-g\"\n")
            for name in ("Makefile.am",):
                (Path(temp) / "input" / name).write_text((tree("html-xml-utils") / "input" / name).read_text())
            edits, _ = edit_repair.expand_macros([Edit("x", "@@recipe:serial-helper-build@@", "")], temp,
                                                 log_lines("html-xml-utils"), "arm64")
            text = apply(temp, edits)["input/debian/rules"]
            self.assertIn("\tdh_auto_build -- cexport CFLAGS=\"-g\"\n\tdh_auto_build -- CFLAGS=\"-g\"\n", text)

    def test_refuses_an_override_that_does_not_call_dh_auto_build(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "input/debian/rules"
            rules.parent.mkdir(parents=True)
            rules.write_text("%:\n\tdh $@\n\noverride_dh_auto_build:\n\t$(MAKE) all\n")
            (Path(temp) / "input/Makefile.am").write_text((tree("html-xml-utils") / "input/Makefile.am").read_text())
            edits, notes = edit_repair.expand_macros([Edit("x", "@@recipe:serial-helper-build@@", "")], temp,
                                                     log_lines("html-xml-utils"), "arm64")
            self.assertEqual(edits, [])
            self.assertIn("does not call dh_auto_build", notes[0])


class CMakeRedirect(unittest.TestCase):
    def test_equals_the_verified_replay(self):
        edits, notes = expand("ectrans", "cmake-redirect-imported-dirs", path="input/CMakeLists.txt")
        oracle = replay("ectrans-verified-cmake-redirect.json")
        self.assertEqual([(e.path, e.old_text, e.new_text) for e in edits],
                         [(e.path, e.old_text, e.new_text) for e in oracle])
        self.assertIn("fiat, parkind_sp, parkind_dp", notes[0])

    def test_the_glob_is_restricted_to_the_gfortran_mod_directory(self):
        text = expand("ectrans", "cmake-redirect-imported-dirs")[0][0].new_text
        self.assertIn("/usr/include/${_dep_triplet}/fortran/gfortran-mod-*/${_dep_leaf}", text)
        self.assertNotIn("FMODDIR", text)
        self.assertNotIn("/usr/lib/${_dep_triplet}", text)

    def test_refuses_without_a_find_package_call_or_when_applied(self):
        with tempfile.TemporaryDirectory() as temp:
            edit = Edit("x", "@@recipe:cmake-redirect-imported-dirs@@", "")
            path = Path(temp) / "input/CMakeLists.txt"
            path.parent.mkdir(parents=True)
            path.write_text("project(x)\nadd_subdirectory(src)\n")
            edits, notes = edit_repair.expand_macros([edit], temp, log_lines("ectrans"), "arm64")
            self.assertEqual(edits, [])
            self.assertIn("refused", notes[0])

    def test_extra_targets_can_be_named(self):
        text = expand("ectrans", "cmake-redirect-imported-dirs", "extra_target")[0][0].new_text
        self.assertIn("foreach(_dep_target fiat parkind_sp parkind_dp extra_target)", text)


class ReplaceRemovedCommand(unittest.TestCase):
    def test_equals_the_accepted_diff(self):
        edits, notes = expand("python-intbitset", "replace-removed-command")
        self.assertEqual(apply(tree("python-intbitset"), edits),
                         {k: v for k, v in apply_diff(tree("python-intbitset"), "python-intbitset-rc3b.diff").items()})
        self.assertIn("python3 -m sphinx -b html docs/ docs/_build/html", notes[0])

    def test_keeps_the_target_and_refuses_a_disabled_one(self):
        text = apply(tree("python-intbitset"), expand("python-intbitset", "replace-removed-command")[0])["input/debian/rules"]
        self.assertIn("override_dh_auto_build-indep:\n\tpython3 -m sphinx", text)
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "input/debian/rules"
            rules.parent.mkdir(parents=True)
            rules.write_text("override_dh_auto_build-indep-disabled:\n\t# python3 setup.py build_sphinx\n")
            edits, notes = edit_repair.expand_macros([Edit("x", "@@recipe:replace-removed-command@@", "")], temp,
                                                     log_lines("python-intbitset"), "amd64")
            self.assertEqual(edits, [])
            self.assertIn("refused", notes[0])


class ArchFlags(unittest.TestCase):
    def test_equals_the_accepted_diff(self):
        edits, notes = expand("libunwind", "arch-flags", "arm64 -mno-outline-atomics")
        self.assertEqual(apply(tree("libunwind"), edits), apply_diff(tree("libunwind"), "libunwind-rc3.diff"))

    def test_unlisted_flags_and_other_architectures_are_refused(self):
        edits, notes = expand("libunwind", "arch-flags", "arm64 -mcpu=nonsense")
        self.assertEqual(edits, [])
        self.assertIn("not on the verified list", notes[0])
        edits, notes = expand("libunwind", "arch-flags", "amd64")
        self.assertEqual(edits, [])

    def test_the_lines_are_exported_make_level_and_inside_the_ifeq(self):
        text = expand("libunwind", "arch-flags")[0][0].new_text
        self.assertEqual(rules_mk.lint(text, text[text.index("ifeq"):]), [])
        self.assertNotIn("\t", text.split("\n", 1)[1].split("ifneq")[0][:0])

    def test_refuses_when_the_flag_is_already_there(self):
        patched = apply(tree("libunwind"), expand("libunwind", "arch-flags")[0])["input/debian/rules"]
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "input/debian/rules"
            rules.parent.mkdir(parents=True)
            rules.write_text(patched)
            edits, notes = edit_repair.expand_macros([Edit("x", "@@recipe:arch-flags@@", "")], temp,
                                                     log_lines("libunwind"), "arm64")
            self.assertEqual(edits, [])
            self.assertIn("already has", notes[0])


class StaticShim(unittest.TestCase):
    PATH = "input/setup.py"

    def test_equals_the_verified_replay(self):
        edits, notes = expand("pybigwig", "static-archive-shim", path=self.PATH)
        oracle = replay("pybigwig-verified-static-shim.json")
        self.assertEqual([(e.path, norm(e.old_text), norm(e.new_text)) for e in edits],
                         [(e.path, norm(e.old_text), norm(e.new_text)) for e in oracle])
        self.assertEqual(norm(apply(tree("pybigwig"), edits)[self.PATH]), norm(apply(tree("pybigwig"), oracle)[self.PATH]))

    def test_the_generated_setup_py_is_valid_python(self):
        edits, _ = expand("pybigwig", "static-archive-shim", path=self.PATH)
        compile(apply(tree("pybigwig"), edits)[self.PATH], "setup.py", "exec")

    def test_generalised_to_another_library_and_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            setup = Path(temp) / "input/setup.py"
            setup.parent.mkdir(parents=True)
            setup.write_text('libs=["foo", "m"]\next = Extension("x",\n                    sources = srcs,\n'
                             '                    libraries = libs)\n')
            log = ["/usr/bin/ld: /usr/lib/x86_64-linux-gnu/libfoo.a(a.o): relocation R_X86_64_PC32 against symbol "
                   "`stdout@@GLIBC_2.2.5' can not be used when making a shared object; recompile with -fPIC"]
            edits, _ = edit_repair.expand_macros([Edit("x", "@@recipe:static-archive-shim@@", "")], temp, log, "amd64")
            text = apply(temp, edits)["input/setup.py"]
            self.assertIn('"libfoo.a"', text)
            self.assertIn("FILE *stdout;", text)
            self.assertIn('libs = ["m"] if _extra_objs else ["foo", "m"]', text)
            compile(text, "setup.py", "exec")

    def test_refuses_what_the_template_does_not_cover(self):
        with tempfile.TemporaryDirectory() as temp:
            setup = Path(temp) / "input/setup.py"
            setup.parent.mkdir(parents=True)
            base = ['/usr/bin/ld: /usr/lib/x86_64-linux-gnu/libBigWig.a(a.o): relocation R_X86_64_PC32 against symbol '
                    "`%s' can not be used when making a shared object"]
            body = 'libs=["BigWig"]\next = Extension("x",\n                    sources = srcs,\n                    libraries = libs)\n'
            setup.write_text(body)
            edit = Edit("x", "@@recipe:static-archive-shim@@", "")
            edits, notes = edit_repair.expand_macros([edit], temp, [base[0] % "some_other_symbol"], "amd64")
            self.assertEqual((edits, "some_other_symbol" in notes[0]), ([], True))
            setup.write_text(body + "extra_link_args = []\n")
            edits, notes = edit_repair.expand_macros([edit], temp, [base[0] % "stderr@@GLIBC_2.2.5"], "amd64")
            self.assertEqual(edits, [])
            self.assertIn("extra_objects or extra_link_args", notes[0])

    def test_no_setup_py_means_no_recipe_and_the_block_keeps_its_stop_advice(self):
        lines = log_lines("pybigwig")
        self.assertIsNone(registry.get("static-archive-shim").recognise(lines, tree("rust-subversion"), "amd64"))
        block = patterns.nonpic_static_archive(lines, tree("rust-subversion"), "amd64")
        self.assertIn("make no edit and reply stop", block)
        self.assertNotIn("@@recipe", block)

    def test_the_block_offers_the_recipe_when_setup_py_is_present(self):
        block = patterns.nonpic_static_archive(log_lines("pybigwig"), tree("pybigwig"), "amd64")
        self.assertIn("@@recipe:static-archive-shim@@", block)
        self.assertNotIn("The repair belongs in the library's package", block)


class RulesLinter(unittest.TestCase):
    def test_problems_are_named(self):
        text = "export A = 1\nifeq ($(X),y)\nDEB_CFLAGS_MAINT_APPEND += -O2\n"
        problems = rules_mk.lint(text, "ifeq ($(X),y)\nDEB_CFLAGS_MAINT_APPEND += -O2\n", ("-O2", "-O3"))
        self.assertTrue(any("missing export" in p for p in problems))
        self.assertTrue(any("without a matching endif" in p for p in problems))
        self.assertTrue(any("-O3" in p for p in problems))
        self.assertTrue(any("leading tab" in p for p in rules_mk.lint("", "\texport DEB_CFLAGS_MAINT_APPEND = -O2\n")))
        self.assertEqual(rules_mk.lint("export DEB_CFLAGS_MAINT_APPEND = -O2\n", "export DEB_CFLAGS_MAINT_APPEND = -O2\n"), [])
        # a tab-indented shell assignment inside a recipe is not make-level
        self.assertEqual(rules_mk.lint("", "\tDEB_X=1 cmd\n"), [])

    def test_insertion_goes_after_the_preferred_export_and_skips_continuations(self):
        text = "export DEB_BUILD_MAINT_OPTIONS = hardening=+all\nexport DEB_X_MAINT_APPEND = a \\\n  b\n\n%:\n\tdh $@\n"
        old, new = rules_mk.insertion(text, "export Y = 1\n", ("DEB_X_MAINT_APPEND",))
        self.assertTrue(old.endswith("  b\n"))
        self.assertEqual(text.replace(old, new), text.replace("  b\n", "  b\nexport Y = 1\n"))
        old, new = rules_mk.insertion(text, "export Y = 1\n", ())
        self.assertTrue(new.endswith("export Y = 1\n"))

    def test_without_exports_it_goes_before_the_first_make_line(self):
        text = "#!/usr/bin/make -f\n# c\nLDFLAGS ?=\n%:\n\tdh $@\n"
        old, new = rules_mk.insertion(text, "export Y = 1\n", ("DEB_BUILD_MAINT_OPTIONS",))
        self.assertEqual(old, "LDFLAGS ?=\n")
        self.assertEqual(new, "export Y = 1\nLDFLAGS ?=\n")

    def test_recipe_lines_must_start_with_a_tab(self):
        from src.recipes.registry import Refusal
        rules_mk.check_recipe_lines("override_dh_auto_build:\n\tdh_auto_build -- x\n\tdh_auto_build\n")
        with self.assertRaises(Refusal):
            rules_mk.check_recipe_lines("override_dh_auto_build:\n    dh_auto_build -- x\n")

    def test_a_conditional_anchor_is_refused(self):
        text = "ifeq ($(A),b)\nexport DEB_BUILD_MAINT_OPTIONS = x\nendif\n"
        from src.recipes.registry import Refusal
        with self.assertRaises(Refusal):
            rules_mk.insertion(text, "export Y = 1\n", ("DEB_BUILD_MAINT_OPTIONS",))

    def test_a_last_line_without_newline_is_handled(self):
        text = "export DEB_BUILD_MAINT_OPTIONS = x"
        old, new = rules_mk.insertion(text, "export Y = 1\n", ("DEB_BUILD_MAINT_OPTIONS",))
        self.assertEqual(text.replace(old, new), "export DEB_BUILD_MAINT_OPTIONS = x\nexport Y = 1")


class MacroChannel(unittest.TestCase):
    def test_ordinary_edits_pass_through_untouched(self):
        plain = Edit("input/debian/rules", "a", "b")
        self.assertEqual(edit_repair.expand_macros([plain], tree("libunwind"), []), ([plain], []))

    def test_an_unknown_recipe_is_refused_with_the_list(self):
        edits, notes = edit_repair.expand_macros([Edit("x", "@@recipe:nope@@", "")], tree("libunwind"), [])
        self.assertEqual(edits, [])
        self.assertIn("unknown", notes[0])
        self.assertIn("symbols-tag", notes[0])

    def test_a_signature_that_is_not_in_the_log_is_refused_without_guessing(self):
        edits, notes = expand("libunwind", "strip-bsymbolic-functions")
        self.assertEqual(edits, [])
        self.assertIn("refused", notes[0])

    def test_a_repeated_macro_in_one_batch_is_expanded_once(self):
        edit = Edit("input/debian/rules", "@@recipe:arch-flags@@", "")
        edits, notes = edit_repair.expand_macros([edit, Edit(edit.path, edit.old_text, edit.new_text)],
                                                 tree("libunwind"), log_lines("libunwind"), "arm64")
        self.assertEqual(len(edits), 1)
        self.assertEqual(len(notes), 2)

    def test_two_recipes_on_one_rules_file_both_apply_in_order(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "input/debian/rules"
            rules.parent.mkdir(parents=True)
            rules.write_text((tree("libevtx") / "input/debian/rules").read_text())
            lines = log_lines("libevtx") + [l for l in log_lines("libunwind") if "hidden symbol" in l]
            edits, notes = edit_repair.expand_macros(
                [Edit("x", "@@recipe:o2-on-test-segfault@@", ""), Edit("x", "@@recipe:arch-flags@@", "")],
                temp, lines, "amd64")
            self.assertEqual(len(edits), 1)  # arch-flags is arm64 only
            self.assertIn("refused", notes[1])

    def test_nothing_raises_on_garbage(self):
        for recipe_id in registry.ids():
            for log in ([None, 3, "x"], []):
                edits, note = registry.run(recipe_id, "/no/such/dir", log, "amd64", {"path": None})
                self.assertEqual(edits, [])
                self.assertTrue(note.startswith("Recipe"))
        self.assertEqual(registry.menu([None, 1], "/no/such/dir", ""), [])
        self.assertEqual(registry.macro_id(None), None)
        self.assertEqual(registry.macro_id("@@recipe:Bad Id@@"), None)


class Blocks(unittest.TestCase):
    """patterns.py: the invocation line comes first in the repair section and
    parses to the macro edit; the best-ranked block wins."""

    def block(self, case):
        lines = log_lines(case)
        found = patterns.recognise([], FIXTURES / "logs" / f"{case}.log", tree(case), TARGET[case])
        self.assertTrue(found, case)
        return found

    def invocation_of(self, block):
        decoder = json.JSONDecoder()
        lines, parsed = [], []
        for line in block.split("\n"):
            at = line.find('{"action"')
            if at >= 0:
                lines.append(line)
                parsed.append(decoder.raw_decode(line[at:])[0])
        self.assertTrue(lines)
        return lines, parsed

    def test_every_block_leads_its_repair_with_the_invocation(self):
        for case, expected in EXPECTED.items():
            if not expected or case in ("libbpp-core",):
                continue
            block = self.block(case)
            lines, parsed = self.invocation_of(block)
            self.assertIn(parsed[0]["edits"][0]["old_text"], {registry.macro_token(r) for r in expected}, case)
            at = block.index(lines[0])
            self.assertEqual(block.count("Repair:"), 1, case)
            # the invocation paragraph comes before any repair text, and before any manual form
            self.assertLess(at, block.index("Repair:"), case)
            self.assertLess(at, block.index("By hand") if "By hand" in block else len(block), case)
            self.assertIn("To apply exactly these edits", lines[0], case)

    def test_the_libevtx_block_offers_the_second_choice(self):
        lines, parsed = self.invocation_of(self.block("libevtx"))
        self.assertEqual([p["edits"][0]["old_text"] for p in parsed],
                         ["@@recipe:o2-on-test-segfault@@", "@@recipe:optimize-guard-test-memory@@"])
        self.assertEqual(parsed[1]["edits"][0]["path"], "input/tests/evtx_test_memory.h")

    def test_the_symbols_block_names_the_file_and_offers_auto(self):
        lines, parsed = self.invocation_of(self.block("libbpp-qt"))
        self.assertEqual(parsed[0]["edits"][0]["new_text"], "")
        self.assertTrue(parsed[0]["edits"][0]["path"].endswith("libbpp-qt2t64.symbols.amd64"))

    def test_the_best_ranked_block_wins(self):
        symbols = [l for l in log_lines("libbpp-qt")]
        sphinx = ["python3 setup.py build_sphinx", "error: invalid command 'build_sphinx'"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input/debian").mkdir(parents=True)
            (root / "input/debian/rules").write_text("override_dh_auto_build-indep:\n\tpython3 setup.py build_sphinx\n")
            # the sphinx error is the first fatal error: it outranks a symbols diff that follows
            log = root / "a.log"
            log.write_text("\n".join(["noise"] * 3 + sphinx + symbols))
            self.assertIn("setup.py build_sphinx", patterns.recognise([], log, root, "amd64").split("\n")[1])
            # and the other way round
            log.write_text("\n".join(["noise"] * 3 + symbols + sphinx))
            self.assertIn("symbols file mismatch", patterns.recognise([], log, root, "amd64").split("\n")[1])

    def test_a_warning_before_the_failure_ranks_behind_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "a.log"
            warning = ["dpkg-gensymbols: warning: some new symbols appeared in the symbols file: see diff output below",
                       "dpkg-gensymbols: error: some symbols or patterns disappeared in the symbols file: see diff output below",
                       "-#MISSING: 1# _Zx@Base 1"]
            hidden = ["/usr/bin/ld: libgcc.a(cas_8_4.o): hidden symbol `__aarch64_cas8_acq_rel' in libgcc.a(cas_8_4.o) is referenced by DSO"]
            log.write_text("\n".join(["gcc warning: foo"] + warning + ["x"] * 10 + hidden))
            self.assertIn("symbols file mismatch", patterns.recognise([], log, root, "arm64").split("\n")[1])

    def test_the_new_symbols_warning_alone_yields_no_block(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "a.log"
            log.write_text("dpkg-gensymbols: warning: some new symbols appeared in the symbols file: see diff output below\n"
                           "+ _Zx@Base 1\n")
            self.assertEqual(patterns.recognise([], log, Path(temp), "arm64"), "")

    def test_first_fatal_index_ignores_probes_and_warnings(self):
        lines = ["configure: error: something", "conftest.c:1: error: x", "foo.c:3: warning: y",
                 "bar.c:4: error: z"]
        self.assertEqual(patterns.first_fatal_index(lines), 3)
        self.assertEqual(patterns.first_fatal_index(["nothing"]), 0)
        self.assertEqual(patterns.first_fatal_index(["error: invalid command 'x'"]), 0)


if __name__ == "__main__":
    unittest.main()
