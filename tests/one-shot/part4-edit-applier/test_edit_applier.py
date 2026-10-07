"""Part 4 checks: legitimate repairs apply, bypasses and unsafe paths are rejected.

The one-shot agent is the project's naive baseline. It gets one attempt, so a
false rejection here makes the baseline fail for a reason that is not the
model's — and makes every later version look better by comparison. The first
test class guards against that; the rest keep the anti-cheat layer honest.

Run from the repository root:  python3 -m unittest discover -s tests/one-shot/part4-edit-applier
"""

import importlib.util
import logging
import os
from pathlib import Path
import tempfile
import unittest


MODULE = (
    Path(__file__).resolve().parents[3]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/one-shot/src/edit_applier.py"
)
spec = importlib.util.spec_from_file_location("edit_applier", MODULE)
edit_applier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edit_applier)


class Edit:
    def __init__(self, path, old_text, new_text):
        self.path, self.old_text, self.new_text = path, old_text, new_text


RULES = """#!/usr/bin/make -f
export DEB_BUILD_MAINT_OPTIONS = hardening=+all
export DEB_CFLAGS_MAINT_APPEND = -msse4.2

%:
\tdh $@

override_dh_auto_test:
\tdh_auto_test -- -j1
"""

CONTROL = """Source: foo
Build-Depends: debhelper-compat (= 13),
               libssl-dev

Package: foo
Architecture: any
Description: example
"""

APOSTROPHE_MAKEFILE = (
    "CFLAGS = -O2 -msse4.2\n\nall:\n"
    "\t@echo Can't find libfoo, using bundled copy\n"
    "\t$(CC) $(CFLAGS) -o foo foo.c\n\n"
    "check:\n\t$(MAKE) test\n"
)

RCRAN_CONFIG = (Path(__file__).resolve().parents[3]
                / "harness/tests/fixtures/crc32c_config.h").read_text()

RCRAN_POLICY = {
    "allowed_paths": ["input/**"],
    "forbidden_paths": ["manifest.json", "config/**", "dependencies/**"],
}


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "worktree"
        self.root.mkdir()
        self.messages = []

        class Collect(logging.Handler):
            def emit(handler, record):
                self.messages.append(record.getMessage())

        handler = Collect(level=logging.WARNING)
        edit_applier.LOGGER.addHandler(handler)
        self.addCleanup(edit_applier.LOGGER.removeHandler, handler)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def apply(self, *edits, **kwargs):
        return edit_applier.apply_edits(self.root, list(edits), **kwargs)

    def rejection(self):
        reasons = [m.split("reason=", 1)[1] for m in self.messages if "edit_rejected" in m]
        return reasons[-1] if reasons else None

    def assertApplied(self, rel, before, old, new, **kwargs):
        path = self.write(rel, before)
        self.assertEqual(self.apply(Edit(rel, old, new), **kwargs), 1, self.rejection())
        self.assertEqual(path.read_text(), before.replace(old, new))

    def assertRejected(self, reason, rel, before, old, new, **kwargs):
        path = self.write(rel, before)
        self.assertEqual(self.apply(Edit(rel, old, new), **kwargs), 0)
        self.assertEqual(self.rejection(), reason)
        self.assertEqual(path.read_text(), before, "a rejected edit must not touch the file")


class LegitimateRepairsApply(Case):
    """False rejections would sink the baseline unfairly."""

    def test_rcran_known_fix(self):
        self.assertApplied(
            "input/src/crc32c/crc32c_config.h", RCRAN_CONFIG,
            "#define HAVE_MM_PREFETCH 1\n",
            "#if defined(__x86_64__) || defined(__i386__) || defined(_M_X64) || defined(_M_IX86)\n"
            "#define HAVE_MM_PREFETCH 1\n#else\n#define HAVE_MM_PREFETCH 0\n#endif\n",
            patch_policy=RCRAN_POLICY,
        )

    def test_architecture_guard_in_source(self):
        self.assertApplied(
            "input/src/fast.c", '#include <immintrin.h>\nint f(void);\n',
            "#include <immintrin.h>\n",
            "#if defined(__x86_64__)\n#include <immintrin.h>\n#endif\n",
        )

    def test_dropping_an_x86_only_flag_in_debian_rules(self):
        self.assertApplied("input/debian/rules", RULES,
                           "export DEB_CFLAGS_MAINT_APPEND = -msse4.2\n", "")

    def test_making_an_x86_only_flag_conditional_in_debian_rules(self):
        self.assertApplied(
            "input/debian/rules", RULES,
            "export DEB_CFLAGS_MAINT_APPEND = -msse4.2\n",
            "ifeq ($(DEB_HOST_ARCH),amd64)\n"
            "export DEB_CFLAGS_MAINT_APPEND = -msse4.2\nendif\n",
        )

    def test_adding_a_missing_build_dependency(self):
        self.assertApplied("input/debian/control", CONTROL,
                           "               libssl-dev\n",
                           "               libssl-dev,\n               zlib1g-dev\n")

    def test_restricting_a_dependency_to_architectures_that_have_it(self):
        self.assertApplied("input/debian/control", CONTROL,
                           "               libssl-dev\n",
                           "               libssl-dev,\n               libnuma-dev [amd64 arm64]\n")

    def test_dropping_an_x86_flag_in_cmake(self):
        self.assertApplied(
            "input/CMakeLists.txt",
            "project(foo C)\nadd_compile_options(-O2 -msse4.2)\nadd_library(foo foo.c)\n",
            "add_compile_options(-O2 -msse4.2)", "add_compile_options(-O2)",
        )

    def test_fix_elsewhere_in_a_build_file_with_an_unparseable_line(self):
        """Regression: one unparseable line used to block every edit to the file.

        An unquoted apostrophe ("Can't") is not valid shell, so Part 4 rejected
        any edit to the whole Makefile, here a textbook x86 -> ARM fix. On the
        200 development cases, 72 had at least one such build file.
        """
        self.assertApplied("input/Makefile", APOSTROPHE_MAKEFILE, "-O2 -msse4.2", "-O2")


class BypassesAreRejected(Case):
    """Making the build pass by not testing is not a repair."""

    def test_removing_the_test_invocation(self):
        self.assertRejected("test_invocation_removed", "input/debian/rules", RULES,
                            "\tdh_auto_test -- -j1\n", "")

    def test_ignoring_test_failures(self):
        self.assertRejected("test_failure_masked", "input/debian/rules", RULES,
                            "dh_auto_test -- -j1", "dh_auto_test -- -j1 || true")

    def test_adding_nocheck(self):
        self.assertRejected("test_bypass_added", "input/debian/rules", RULES,
                            "export DEB_BUILD_MAINT_OPTIONS = hardening=+all\n",
                            "export DEB_BUILD_MAINT_OPTIONS = hardening=+all\n"
                            "export DEB_BUILD_OPTIONS += nocheck\n")

    def test_adding_an_empty_test_override(self):
        rules = RULES.split("override_dh_auto_test:")[0]
        self.assertRejected("test_override_disabled", "input/debian/rules", rules,
                            "\tdh $@\n", "\tdh $@\n\noverride_dh_auto_test:\n")

    def test_restricting_the_package_to_another_architecture(self):
        self.assertRejected("architecture_restriction_changed", "input/debian/control",
                            CONTROL, "Architecture: any", "Architecture: amd64")

    def test_excluding_the_target_architecture_from_a_dependency(self):
        self.assertRejected("architecture_exclusion_added", "input/debian/control", CONTROL,
                            "               libssl-dev\n",
                            "               libssl-dev,\n               libfoo-dev [!arm64]\n")

    def test_emptying_a_file(self):
        self.assertRejected("file_emptied", "input/src/foo.c", "int main(void){return 0;}\n",
                            "int main(void){return 0;}\n", "")

    def test_commenting_out_an_install_list(self):
        self.assertRejected("install_list_emptied", "input/debian/foo.install",
                            "usr/lib/libfoo.so*\n", "usr/lib/libfoo.so*\n", "# usr/lib/libfoo.so*\n")


    def test_cheat_is_still_caught_in_a_file_with_an_unparseable_line(self):
        self.assertRejected("test_invocation_removed", "input/Makefile", APOSTROPHE_MAKEFILE,
                            "\t$(MAKE) test\n", "\t@true\n")

    def test_introducing_an_unparseable_line_is_rejected(self):
        # Syntax Part 4 cannot read could hide a bypass, so the agent may not add any.
        self.assertRejected("unparseable_build_command", "input/debian/rules", RULES,
                            "\tdh_auto_test -- -j1\n", "\tdh_auto_test -- -j1 '\n")

    def test_editing_inside_an_unparseable_line_is_rejected(self):
        # Conservative: the edited line cannot be checked, so it is refused.
        self.assertRejected("unparseable_build_command", "input/Makefile", APOSTROPHE_MAKEFILE,
                            "Can't find libfoo", "Can't locate libfoo")


class UnsafePathsAreRejected(Case):
    def test_parent_directory_traversal(self):
        self.write("manifest.json", "{}")
        self.assertEqual(self.apply(Edit("input/../manifest.json", "{}", "[]")), 0)
        self.assertTrue(self.rejection().startswith("invalid_path"))

    def test_absolute_path(self):
        self.assertEqual(self.apply(Edit("/etc/hostname", "a", "b")), 0)
        self.assertTrue(self.rejection().startswith("invalid_path"))

    def test_path_outside_input(self):
        self.assertRejected("path_outside_allowed_prefix", "config/buildconfig", "x\n", "x", "y")

    def test_path_excluded_by_case_policy(self):
        policy = {"allowed_paths": ["input/src/**"], "forbidden_paths": []}
        self.assertRejected("path_not_allowed_by_policy", "input/debian/rules", RULES,
                            "-msse4.2", "", patch_policy=policy)

    def test_symlinked_file_is_not_followed(self):
        outside = Path(self.temp.name) / "secret.txt"
        outside.write_text("secret\n")
        (self.root / "input").mkdir()
        os.symlink(outside, self.root / "input" / "evil.h")
        self.assertEqual(self.apply(Edit("input/evil.h", "secret", "pwned")), 0)
        self.assertEqual(outside.read_text(), "secret\n")

    def test_symlinked_directory_is_not_followed(self):
        outside = Path(self.temp.name) / "elsewhere"
        outside.mkdir()
        (outside / "f.c").write_text("int x;\n")
        (self.root / "input").mkdir()
        os.symlink(outside, self.root / "input" / "link")
        self.assertEqual(self.apply(Edit("input/link/f.c", "int x;", "int y;")), 0)
        self.assertEqual((outside / "f.c").read_text(), "int x;\n")


class EditMechanics(Case):
    def test_old_text_not_found(self):
        self.assertRejected("old_text_not_found", "input/a.c", "int x;\n", "int y;", "int z;")

    def test_ambiguous_old_text(self):
        self.assertRejected("old_text_ambiguous", "input/a.c", "int x;\nint x;\n", "int x;", "int y;")

    def test_no_op_edit(self):
        self.assertRejected("no_change", "input/a.c", "int x;\n", "int x;", "int x;")

    def test_non_utf8_file_is_not_edited(self):
        path = self.root / "input" / "blob.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\xfe binary")
        self.assertEqual(self.apply(Edit("input/blob.bin", "binary", "text")), 0)
        self.assertEqual(path.read_bytes(), b"\xff\xfe binary")

    def test_count_is_accepted_edits_and_earlier_edits_survive(self):
        self.write("input/a.c", "int a;\n")
        self.write("input/b.c", "int b;\n")
        applied = self.apply(Edit("input/a.c", "int a;", "long a;"),
                             Edit("input/b.c", "missing", "x"))
        self.assertEqual(applied, 1)
        self.assertEqual((self.root / "input/a.c").read_text(), "long a;\n")


if __name__ == "__main__":
    unittest.main()
