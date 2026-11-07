"""0.5.1: the syntax guard sends back an edit that leaves its file unparseable.

The shapes come from the 0.3.3 Development Validation run (111 small public
cases, 6 November), where edits that nothing built broke files the original
package compiled: xmbmon, sphde, pam-tmpdir, libmbim. Each check compares the
file before and after the batch, so a file that already fails is never blamed
on the edit.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
sys.path.insert(0, str(ROOT))

from src import syntax_guard  # noqa: E402
from src.contracts import AgentConfig  # noqa: E402
import test_wiring  # noqa: E402
from test_wiring import CONFIG, Script, reply  # noqa: E402

NEW = AgentConfig()
STOP = reply(action="stop", reason="done")


def problems(path, before, after, **kw):
    return [p.message for p in syntax_guard.file_problems(path, before, after, **kw)]


class Files(unittest.TestCase):
    def test_unbalanced_braces_in_c_are_found(self):
        before = "int f(void) {\n  return inb(0x80);\n}\n"
        after = "int f(void) {\n#ifdef __x86_64__\n  return inb(0x80);\n#else\n  return 0;\n#endif\n"
        found = problems("input/io_cpu.h", before, after)
        self.assertTrue(any("braces" in m for m in found), found)

    def test_unmatched_if_is_found(self):
        before = "#include <sys/io.h>\nint x;\n"
        after = "#if defined(__x86_64__)\n#include <sys/io.h>\nint x;\n"
        self.assertTrue(any("#if" in m for m in problems("input/a.c", before, after)))

    def test_a_balanced_repair_passes(self):
        before = "#include <sys/io.h>\nint x;\n"
        after = "#if defined(__x86_64__) || defined(__i386__)\n#include <sys/io.h>\n#endif\nint x;\n"
        self.assertEqual(problems("input/a.c", before, after), [])

    def test_braces_in_comments_strings_and_macros_do_not_count(self):
        before = "int x;\n"
        after = ('int x; /* { */\n// {\nconst char *s = "{(";\nchar c = \'{\';\n'
                 "#define OPEN {\n")
        self.assertEqual(problems("input/a.c", before, after), [])

    def test_a_file_that_was_already_unbalanced_is_judged_against_itself(self):
        before = "#define BEGIN {\nvoid f() BEGIN\n}\n}\n"   # already one extra }
        after = before.replace("void f()", "static void f()")
        self.assertEqual(problems("input/a.c", before, after), [])

    def test_python_syntax(self):
        before = "import platform\nARCH = platform.machine()\n"
        after = "import platform\nif ARCH == 'aarch64'\n    pass\n"
        self.assertTrue(problems("input/setup.py", before, after)[0].startswith("Python syntax"))
        self.assertEqual(problems("input/setup.py", before, before + "X = 1\n"), [])

    def test_python2_files_have_no_baseline(self):
        before = "print 'hello'\n"
        self.assertEqual(problems("input/old.py", before, before + "print 'x'\n"), [])

    def test_make_recipe_with_spaces_is_found(self):
        before = "%:\n\tdh $@\n"
        after = "%:\n\tdh $@\n\noverride_dh_auto_configure:\n    dh_auto_configure -- -DX=1\n"
        found = problems("input/debian/rules", before, after)
        self.assertTrue(any("missing separator" in m for m in found), found)

    def test_make_recipe_with_a_tab_and_conditionals_pass(self):
        before = "%:\n\tdh $@\n"
        after = ("%:\n\tdh $@\n\nifeq ($(DEB_HOST_ARCH),arm64)\n"
                 "  export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\nendif\n"
                 "override_dh_auto_configure:\n\tdh_auto_configure -- \\\n"
                 "    -DX=1\n")
        self.assertEqual(problems("input/debian/rules", before, after), [])

    def test_make_missing_endif_is_found(self):
        before = "%:\n\tdh $@\n"
        after = "ifeq ($(DEB_HOST_ARCH),arm64)\nexport X = 1\n%:\n\tdh $@\n"
        self.assertTrue(any("endif" in m for m in problems("input/debian/rules", before, after)))

    def test_cmake_blocks(self):
        before = "project(x)\nadd_library(a a.c)\n"
        after = "project(x)\nif(CMAKE_SYSTEM_PROCESSOR MATCHES \"x86\")\nadd_library(a a.c)\n"
        self.assertTrue(any("endif" in m for m in problems("input/CMakeLists.txt", before, after)))
        ok = after + "endif()\n"
        self.assertEqual(problems("input/CMakeLists.txt", before, ok), [])

    def test_symbols_line_without_a_version_is_found(self):
        # libbpp-seq, 6 November: "... is not a valid version" after the edit.
        before = "libbpp-seq12 libbpp-seq12 #MINVER#\n _ZN3bpp3Seq4sizeEv@Base 2.4.1\n"
        after = before + " (arch=!arm64)_ZNSt6vectorIiSaIiEE12emplace_backIJS5_EEEvDpOT_@Base\n"
        found = problems("input/debian/libbpp-seq12.symbols", before, after)
        self.assertTrue(any("cannot parse" in m for m in found), found)

    def test_symbols_tag_with_a_gnu_name_is_found(self):
        before = "libx1 libx1 #MINVER#\n _Z1fv@Base 1.0\n"
        after = before.replace(" _Z1fv", " (arch=!aarch64)_Z1fv")
        found = problems("input/debian/libx1.symbols", before, after)
        self.assertTrue(any("arch=aarch64" in m for m in found), found)

    def test_symbols_tags_with_debian_names_pass(self):
        before = "libx1 libx1 #MINVER#\n _Z1fv@Base 1.0\n"
        after = before + (' (c++|arch=!arm64 !riscv64)"foo::bar()@Base" 1.0\n'
                          " (optional=templinst|arch=amd64 i386)_Z1gv@Base 1.0~rc1 1\n")
        self.assertEqual(problems("input/debian/libx1.symbols", before, after), [])

    def test_json(self):
        self.assertTrue(problems("input/package.json", '{"a": 1}', '{"a": 1,}'))

    @unittest.skipUnless(shutil.which("sh"), "no sh")
    def test_shell(self):
        before = "#!/bin/sh\nif [ -x a ]; then\n  echo a\nfi\n"
        after = "#!/bin/sh\nif [ -x a ]; then\n  echo a\n"
        self.assertTrue(problems("input/configure.sh", before, after))

    @unittest.skipUnless(shutil.which("gcc") or shutil.which("cc"), "no C compiler")
    def test_compiler_finds_a_new_error_on_an_edited_line(self):
        # xmbmon, 6 November: the edit made a void function's result be used.
        before = ('#include "config.h"\nvoid outb(int v, int p);\n'
                  "int probe(void) { outb(1, 0x80); return 0; }\n")
        after = before.replace("outb(1, 0x80); return 0;", "return outb(1, 0x80);")
        import tempfile
        from time import monotonic
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "input").mkdir()
            found = problems("input/io_cpu.c", before, after, worktree=work,
                             deadline=monotonic() + 30)
        self.assertTrue(any("new error" in m and "void" in m for m in found), found)

    @unittest.skipUnless(shutil.which("gcc") or shutil.which("cc"), "no C compiler")
    def test_compiler_ignores_errors_the_file_already_had(self):
        before = '#include "missing.h"\nint f(void) { return undeclared; }\nint g;\n'
        after = before.replace("int g;", "int g = 1;")
        import tempfile
        from time import monotonic
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "input").mkdir()
            self.assertEqual(problems("input/a.c", before, after, worktree=work,
                                      deadline=monotonic() + 30), [])


@unittest.skipUnless(shutil.which("gcc") or shutil.which("cc"), "no C compiler")
class TargetCompile(unittest.TestCase):
    """xmbmon, 6 November: the edit made a macro void on ARM, and the file that
    uses the macro failed with "void value not ignored". The header alone
    compiles; only its includer, compiled for the target, shows the error."""

    HEADER = ("#ifdef LINUX\nint iopl(int);\n#if defined(__x86_64__)\n#define SET_IOPL() iopl(3)\n"
              "#else\n#define SET_IOPL() (0)\n#endif\n#endif\n")
    USER = '#include "io_cpu.h"\nint probe(void) { if (SET_IOPL() < 0) return 1; return 0; }\n'
    LOG = "gcc -c -g -O2 -I. -DHAVE_CONFIG_H -Wall -DLINUX getMBinfo.c\n"

    def check(self, after, target):
        import tempfile
        from time import monotonic
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "input").mkdir()
            (work / "input/io_cpu.h").write_text(self.HEADER)
            (work / "input/io_static.c").write_text(self.USER)
            (work / "log").write_text(self.LOG)
            return problems("input/io_cpu.h", self.HEADER, after, worktree=work,
                            deadline=monotonic() + 30, target=target, log_path=work / "log")

    def test_a_void_macro_on_the_target_is_found_in_its_includer(self):
        after = self.HEADER.replace("#define SET_IOPL() (0)", "#define SET_IOPL() ((void)0)")
        found = self.check(after, "arm64")
        self.assertTrue(any("void value not ignored" in m for m in found), found)

    def test_the_same_edit_is_harmless_when_the_branch_is_not_the_targets(self):
        after = self.HEADER.replace("#define SET_IOPL() (0)", "#define SET_IOPL() ((void)0)")
        self.assertEqual(self.check(after, "amd64"), [])

    def test_a_correct_edit_passes(self):
        after = self.HEADER.replace("#define SET_IOPL() (0)", "#define SET_IOPL() (-1)")
        self.assertEqual(self.check(after, "arm64"), [])


class Loop(unittest.TestCase):
    setUp = test_wiring.Adapter.setUp
    run_with = test_wiring.Adapter.run_with

    BROKEN = {"path": CONFIG, "old_text": "#define HAVE_MM_PREFETCH 1\n",
              "new_text": "#if defined(__x86_64__)\n#define HAVE_MM_PREFETCH 1\n"}
    FIXED = {"path": CONFIG, "old_text": "#define HAVE_MM_PREFETCH 1\n",
             "new_text": "#if defined(__x86_64__)\n#define HAVE_MM_PREFETCH 1\n#else\n"
                         "#define HAVE_MM_PREFETCH 0\n#endif\n"}

    def edit(self, item):
        return reply(action="edit", hypothesis="h", evidence=["e"], edits=[item])

    def test_a_breaking_edit_is_sent_back_and_its_correction_applied(self):
        script = Script(self.edit(self.BROKEN), self.edit(self.FIXED), STOP)
        result, _ = self.run_with(script, config=NEW)
        self.assertIn("Reason: syntax_check", script.user(1))
        self.assertIn("unmatched #if", script.user(1))
        text = (self.worktree / CONFIG).read_text()
        self.assertIn("#define HAVE_MM_PREFETCH 0\n#endif", text)
        self.assertEqual(sum(a.applied for a in result.attempts), 1)

    def test_an_unchanged_resend_is_applied(self):
        script = Script(self.edit(self.BROKEN), self.edit(self.BROKEN), STOP)
        result, _ = self.run_with(script, config=NEW)
        self.assertIn("Reason: syntax_check", script.user(1))
        self.assertIn("#if defined(__x86_64__)", (self.worktree / CONFIG).read_text())
        self.assertEqual(sum(a.applied for a in result.attempts), 1)

    def test_the_guard_can_be_switched_off(self):
        script = Script(self.edit(self.BROKEN), STOP)
        self.run_with(script, config=AgentConfig(use_syntax_guard=False))
        self.assertNotIn("syntax_check", script.user(1))
        self.assertIn("#if defined(__x86_64__)", (self.worktree / CONFIG).read_text())

    def test_a_well_formed_edit_is_not_held(self):
        script = Script(self.edit(self.FIXED), STOP)
        result, _ = self.run_with(script, config=NEW)
        self.assertNotIn("syntax_check", script.user(1))
        self.assertEqual(result.attempts[0].applied, 1)


class ArchNames(unittest.TestCase):
    def test_debian_names(self):
        from src import archnames
        self.assertEqual([archnames.debian(a) for a in ("aarch64", "x86_64", "riscv64", "arm64", "?")],
                         ["arm64", "amd64", "riscv64", "arm64", ""])
        line = archnames.describe("aarch64")
        self.assertIn("Debian name arm64", line)
        self.assertIn("aarch64-linux-gnu", line)
        self.assertIn("UNSIGNED", line)

    def test_symbols_advice_uses_the_debian_name(self):
        from src import patterns
        import tempfile
        log = ["dpkg-gensymbols: error: some symbols or patterns disappeared in the symbols file",
               "dpkg-gensymbols: warning: debian/libx1/DEBIAN/symbols doesn't match completely debian/libx1.symbols",
               "- _Z1fv@Base 1.0"]
        with tempfile.TemporaryDirectory() as tmp:
            block = patterns.debian_symbols(log, Path(tmp), "aarch64")
        self.assertIn("(arch=!arm64)", block)
        self.assertNotIn("(arch=!aarch64)", block)


if __name__ == "__main__":
    unittest.main()
