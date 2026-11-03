"""Recognised cross-architecture patterns (patterns.py): each test is one of
the live failures of 3 November and checks that the block names the evidence
and the repair the playbook describes. No model, network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import patterns, prompts  # noqa: E402
from src.contracts import AgentConfig, Diagnostic  # noqa: E402

RUST_DETAIL = """error[E0308]: mismatched types
  --> src/client.rs:32:72
   |
32 |         let local_abspath: &std::path::Path = std::ffi::CStr::from_ptr(local_abspath)
   |                                               ------------------------ ^^^^^^^^^^^^^ expected `*const u8`, found `*const i8`
"""


def diagnostic(message, path=None, line=None, resolved=(), details=""):
    return Diagnostic(message, "build", path, line, list(resolved), None, 0, details)


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


class RustSignedness(Worktree):
    def test_names_c_char_and_the_files_with_i8(self):
        self.write("input/src/client.rs", "fn f(p: *const i8) {}\nlet x: *mut i8;\n")
        self.write("input/src/delta.rs", "let v: Vec<*const i8>;\n")
        self.write("input/src/ok.rs", "let s: *const std::ffi::c_char;\n")
        self.write("input/.pc/x/src/client.rs", "*const i8 *const i8\n")
        block = patterns.recognise([diagnostic("error[E0308]: mismatched types", details=RUST_DETAIL)],
                                   None, self.root, "arm64")
        self.assertIn("std::ffi::c_char", block)
        self.assertIn("replace_all: true", block)
        self.assertIn("Do NOT cast call sites to `*const u8` or `*const i8`", block)
        self.assertIn("input/src/client.rs (2), input/src/delta.rs (1)", block)
        self.assertNotIn(".pc", block)
        self.assertNotIn("ok.rs", block)

    def test_the_other_direction_on_x86(self):
        detail = RUST_DETAIL.replace("expected `*const u8`, found `*const i8`",
                                     "expected `*const i8`, found `*const u8`")
        self.write("input/src/a.rs", "fn f(p: *const u8) {}\n")
        block = patterns.recognise([diagnostic("e", details=detail)], None, self.root, "amd64")
        self.assertIn("where the crate wrote `*const u8`", block)
        self.assertIn("input/src/a.rs (1)", block)

    def test_both_directions_appear_and_the_target_decides(self):
        # rust-subversion: an argument mismatch reads "expected u8, found i8",
        # a return-value mismatch the other way round; the crate wrote i8.
        detail = RUST_DETAIL + RUST_DETAIL.replace("expected `*const u8`, found `*const i8`",
                                                   "expected `*const i8`, found `*const u8`")
        block = patterns.recognise([diagnostic("e", details=detail)], None, self.root, "arm64")
        self.assertIn("where the crate wrote `*const i8` (2 such errors)", block)
        block = patterns.recognise([diagnostic("e", details=detail)], None, self.root, "amd64")
        self.assertIn("where the crate wrote `*const u8`", block)

    def test_no_rust_error_no_block(self):
        self.assertEqual(patterns.recognise([diagnostic("undefined reference to `foo'")],
                                            None, self.root), "")


class IntrinsicHeader(Worktree):
    def test_finds_the_guard_and_the_macro_definition(self):
        self.write("input/src/crc32c/crc32c_prefetch.h",
                   "#include \"crc32c/crc32c_config.h\"\n\n#if HAVE_MM_PREFETCH\n\n#if defined(_MSC_VER)\n"
                   "#include <intrin.h>\n#else\n#include <xmmintrin.h>\n#endif\n\n#endif\n")
        self.write("input/src/crc32c/crc32c_config.h", "// x\n#define HAVE_MM_PREFETCH 1\n")
        block = patterns.recognise([diagnostic(
            "fatal error: xmmintrin.h: No such file or directory", "crc32c/crc32c_prefetch.h", 8,
            ["input/src/crc32c/crc32c_prefetch.h"])], None, self.root, "arm64")
        self.assertIn("line 3: #if HAVE_MM_PREFETCH", block)
        self.assertIn("HAVE_MM_PREFETCH is defined here: input/src/crc32c/crc32c_config.h:2: "
                      "#define HAVE_MM_PREFETCH 1", block)
        self.assertIn("make that definition false", block)

    def test_without_a_guard_the_advice_is_the_architecture_macro(self):
        self.write("input/a.c", "#include <emmintrin.h>\n")
        block = patterns.recognise([diagnostic(
            "fatal error: emmintrin.h: No such file or directory", "a.c", 1, ["input/a.c"])],
            None, self.root, "arm64")
        self.assertIn("defined(__x86_64__) || defined(__i386__)", block)

    def test_other_missing_headers_are_not_this_pattern(self):
        self.assertEqual(patterns.recognise([diagnostic(
            "fatal error: foo.h: No such file or directory", "a.c", 1, ["input/a.c"])],
            None, self.root), "")


class OutlineAtomics(Worktree):
    def test_names_the_symbol_and_the_rules_lines(self):
        log = self.write("build.log", "x\n/usr/bin/ld: .libs/Lperf-simple: hidden symbol "
                         "`__aarch64_cas8_acq_rel' in /usr/lib/gcc/aarch64-linux-gnu/14/libgcc.a(cas_8_4.o)"
                         " is referenced by DSO\ncollect2: error: ld returned 1 exit status\n")
        block = patterns.recognise([diagnostic("collect2: error: ld returned 1 exit status")],
                                   log, self.root, "arm64")
        self.assertIn("`__aarch64_cas8_acq_rel`", block)
        self.assertIn("Failing links: .libs/Lperf-simple.", block)
        self.assertIn("export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics", block)
        self.assertIn("do not split it", block)

    def test_undefined_reference_form(self):
        log = self.write("build.log", "foo.o: undefined reference to `__aarch64_ldadd4_acq_rel'\n")
        self.assertIn("__aarch64_ldadd4_acq_rel", patterns.recognise([], log, self.root, "arm64"))


class SymbolsFile(Worktree):
    def test_lists_the_missing_symbols_and_the_file(self):
        log = self.write("build.log", "\n".join([
            "dpkg-gensymbols: warning: some new symbols appeared in the symbols file: see diff output below",
            "dpkg-gensymbols: error: some symbols or patterns disappeared in the symbols file: see diff output below",
            "dpkg-gensymbols: warning: debian/libbpp-core4/DEBIAN/symbols doesn't match completely debian/libbpp-core4.symbols.amd64",
            "--- debian/libbpp-core4.symbols.amd64 (libbpp-core4_2.4.1-1_amd64)",
            "+++ dpkg-gensymbolsSjfd3 2026-11-03", "@@ -1,3 +1,3 @@",
            " (c++)\"bpp::A::a()@Base\" 2.4.1", "-#MISSING: 2.4.1# (c++)\"bpp::B::b()@Base\" 2.4.1",
            "+ (c++)\"bpp::C::c()@Base\" 2.4.1-1", "dh_makeshlibs: error: failing due to earlier errors", ""]))
        block = patterns.recognise([diagnostic("dh_makeshlibs: error: failing due to earlier errors")],
                                   log, self.root, "amd64")
        self.assertIn("1 listed symbols are missing from the build and 1 new ones appeared", block)
        self.assertIn("debian/libbpp-core4.symbols.amd64", block)
        self.assertIn("-#MISSING: 2.4.1# (c++)\"bpp::B::b()@Base\" 2.4.1", block)
        self.assertIn("(arch=!amd64)", block)


class Rendering(Worktree):
    def test_opening_message_shows_the_block_after_the_errors(self):
        context = SimpleNamespace(case_id="c", worktree=self.root, task_metadata={"target_arch": "arm64"},
                                  log_tail="")
        message = prompts.opening_message(context, AgentConfig(use_build_feedback=False),
                                          diagnostics=[diagnostic("error[E0308]: mismatched types")],
                                          pattern="=== Recognised cross-architecture pattern ===\nX\n")
        self.assertLess(message.index("=== End of errors ==="), message.index("Recognised"))

    def test_a_failing_recogniser_never_breaks_the_run(self):
        self.assertEqual(patterns.recognise([SimpleNamespace(message=None)], None, self.root), "")


if __name__ == "__main__":
    unittest.main()
