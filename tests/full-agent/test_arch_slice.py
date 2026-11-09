"""The architecture-dependence slice (arch_slice.py) and the static
post-edit verdicts (verify.py), and their place in the loop and the opening
message. No model, network, compiler or build.

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

from src import arch_model, arch_slice, verify  # noqa: E402
from src.contracts import AgentConfig, Diagnostic  # noqa: E402
from src.loop import run_loop  # noqa: E402
from src.model_adapter import ModelAdapter  # noqa: E402
from src.contracts import Budget  # noqa: E402

ARM = arch_model.lookup("arm64")
AMD = arch_model.lookup("amd64")

PREFETCH = ('#include "crc32c/crc32c_config.h"\n\n#if HAVE_MM_PREFETCH\n\n#if defined(_MSC_VER)\n'
            '#include <intrin.h>\n#else\n#include <xmmintrin.h>\n#endif\n\n#endif\n')
RULES = ("#!/usr/bin/make -f\nifeq ($(DEB_HOST_ARCH),arm64)\nexport DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n"
         "endif\n%:\n\tdh $@\n")


def diagnostic(message, path=None, line=None, resolved=(), quote=None, details=""):
    return Diagnostic(message, "build", path, line, list(resolved), quote, 0, details)


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


class Slice(Worktree):
    def test_finds_constructs_and_evaluates_them_for_the_target(self):
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH)
        self.write("input/src/crc32c/crc32c_config.h", "#define HAVE_MM_PREFETCH 1\n")
        self.write("input/src/simd.c", "#if defined(__x86_64__)\n#include <emmintrin.h>\nint a = _mm_add_epi32(x, y);\n#endif\n")
        self.write("input/src/lib.rs", "fn f(p: *const i8) {}\n#[cfg(target_arch = \"x86_64\")]\nfn g() {}\n")
        self.write("input/debian/rules", RULES)
        self.write("input/debian/control", "Package: p\nArchitecture: amd64 i386\n")
        self.write("input/debian/libp.symbols.amd64", "libp.so.1 libp #MINVER#\n")
        self.write("input/.pc/a/src/simd.c", "#include <emmintrin.h>\n")
        kinds = {(d.path, d.kind): d for d in arch_slice.scan(self.root, ARM)}
        include = kinds[("input/src/crc32c/crc32c_prefetch.h", "x86-intrinsics-include")]
        self.assertIs(include.reached, True)
        self.assertIn("HAVE_MM_PREFETCH is defined at input/src/crc32c/crc32c_config.h:1", include.note)
        self.assertIs(kinds[("input/src/simd.c", "x86-intrinsics-include")].reached, False)   # guarded
        self.assertIs(kinds[("input/src/simd.c", "cpp-arch-guard")].reached, True)
        self.assertEqual(kinds[("input/src/lib.rs", "rust-cstring-pointer")].note[:1], "1")
        self.assertIs(kinds[("input/src/lib.rs", "rust-cfg")].reached, False)
        self.assertIs(kinds[("input/debian/rules", "make-arch-conditional")].reached, True)
        self.assertEqual(kinds[("input/debian/rules", "exported-build-flag")].text,
                         "DEB_CFLAGS_MAINT_APPEND = '-mno-outline-atomics'")
        self.assertIs(kinds[("input/debian/control", "control-architecture")].reached, False)
        self.assertIn("NOT in this list", kinds[("input/debian/control", "control-architecture")].note)
        self.assertIs(kinds[("input/debian/libp.symbols.amd64", "per-arch-symbols-file")].reached, False)
        self.assertNotIn(("input/.pc/a/src/simd.c", "x86-intrinsics-include"), kinds)

    def test_render_puts_the_nearest_constructs_under_each_error(self):
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH)
        self.write("input/src/crc32c/crc32c_config.h", "#define HAVE_MM_PREFETCH 1\n")
        self.write("input/src/other.c", "#include <xmmintrin.h>\n")
        self.write("input/src/arm_only.h", "__asm__(\"nop\");\n")   # a header: its inclusion may be guarded
        self.write("input/src/ia64/cursor.c", "asm volatile(\"nop\");\n")   # selected by the build system
        deps = arch_slice.scan(self.root, ARM)
        text = arch_slice.render(deps, [diagnostic("fatal error: xmmintrin.h: No such file or directory",
                                                   "crc32c/crc32c_prefetch.h", 8, ["input/src/crc32c/crc32c_prefetch.h"])], ARM)
        self.assertIn("Near error 1 (input/src/crc32c/crc32c_prefetch.h):", text)
        self.assertIn("crc32c_prefetch.h:8 [x86-intrinsics-include] reached on arm64: yes", text)
        self.assertIn("Reached on this architecture with no guard", text)
        self.assertIn("input/src/other.c:1", text)
        self.assertNotIn("arm_only.h", text)
        self.assertNotIn("ia64/cursor.c", text)
        self.assertEqual(arch_slice.render([], [], ARM), "")

    def test_package_level_constructs_for_an_error_without_a_file(self):
        self.write("input/debian/rules", RULES + "LIBDIR := /usr/lib/$(DEB_HOST_MULTIARCH)\n")
        self.write("input/debian/control", "Package: a\nArchitecture: any\n\nPackage: b\nArchitecture: amd64 arm64\n\n"
                   "Package: c\nArchitecture: amd64 arm64\n\nPackage: d\nArchitecture: amd64\n")
        deps = arch_slice.scan(self.root, ARM)
        text = arch_slice.render(deps, [diagnostic("collect2: error: ld returned 1 exit status")], ARM)
        self.assertIn("Near error 1 (the package):", text)
        self.assertIn("[exported-build-flag]", text)
        self.assertIn("[arch-variable-use]", text)
        self.assertIn("control:11 [control-architecture] reached on arm64: no", text)   # d: target missing
        self.assertNotIn("control:4", text)        # lists that include the target are not shown
        self.assertEqual(text.count("Architecture: amd64 arm64"), 0)
        lines = text.split("\n")
        self.assertLess(lines.index(next(l for l in lines if "exported-build-flag" in l)),
                        lines.index(next(l for l in lines if "arch-variable-use" in l)))


class Summary(Worktree):
    def test_the_rest_of_the_slice_is_summarised_not_counted(self):
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH)
        self.write("input/src/crc32c/crc32c_config.h", "#define HAVE_MM_PREFETCH 0\n")
        self.write("input/src/blake3/blake3_impl.h", "#if defined(__x86_64__)\n#include <immintrin.h>\n#endif\n")
        deps = arch_slice.scan(self.root, ARM)
        text = arch_slice.render(deps, [diagnostic("fatal error: xmmintrin.h: No such file or directory",
                                                   "crc32c/crc32c_prefetch.h", 8, ["input/src/crc32c/crc32c_prefetch.h"],
                                                   "#include <xmmintrin.h>")], ARM)
        self.assertRegex(text, r"of the 2 not shown, 1 are guards.*1 are architecture-specific includes, calls or asm guarded and not reached on arm64")
        self.assertIn("no need to search the rest", text)


class Verdicts(Worktree):
    INCLUDE = diagnostic("fatal error: xmmintrin.h: No such file or directory", "crc32c/crc32c_prefetch.h", 8,
                         ["input/src/crc32c/crc32c_prefetch.h"], "#include <xmmintrin.h>")

    def test_include_still_reached_names_the_macro_and_its_definition(self):
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH)
        self.write("input/src/crc32c/crc32c_config.h", "#define HAVE_MM_PREFETCH 1\n")
        [v] = verify.predict(self.root, [self.INCLUDE], ARM, changed={"input/src/crc32c/crc32c_prefetch.h"})
        self.assertIs(v.ok, False)
        self.assertIn("still compiled on arm64", v.text)
        self.assertIn("HAVE_MM_PREFETCH is defined at input/src/crc32c/crc32c_config.h:1", v.text)

    def test_include_no_longer_reached_after_the_macro_is_turned_off(self):
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH)
        self.write("input/src/crc32c/crc32c_config.h", "#if defined(__x86_64__)\n#define HAVE_MM_PREFETCH 1\n#else\n#define HAVE_MM_PREFETCH 0\n#endif\n")
        [v] = verify.predict(self.root, [self.INCLUDE], ARM, changed={"input/src/crc32c/crc32c_config.h"})
        self.assertIs(v.ok, True)
        self.assertIn("no longer compiled on arm64", v.text)
        self.assertIn("reply stop", verify.render([v], ARM))
        [v] = verify.predict(self.root, [self.INCLUDE], AMD, changed={"input/src/crc32c/crc32c_config.h"})
        self.assertIs(v.ok, False)   # on amd64 it is still (rightly) compiled

    def test_include_guarded_by_an_architecture_macro_directly(self):
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH.replace("#if HAVE_MM_PREFETCH", "#if HAVE_MM_PREFETCH && defined(__x86_64__)"))
        self.write("input/src/crc32c/crc32c_config.h", "#define HAVE_MM_PREFETCH 1\n")
        [v] = verify.predict(self.root, [self.INCLUDE], ARM, changed={"input/src/crc32c/crc32c_prefetch.h"})
        self.assertIs(v.ok, True)

    def test_moved_or_removed_failing_line(self):
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH.replace("#include <xmmintrin.h>\n", ""))
        [v] = verify.predict(self.root, [self.INCLUDE], ARM, changed={"input/src/crc32c/crc32c_prefetch.h"})
        self.assertIsNone(v.ok)
        self.assertIn("no longer in the file", v.text)

    def test_rust_signedness_counts_what_remains(self):
        detail = "   |  ^^^ expected `*const u8`, found `*const i8`\n"
        self.write("input/src/a.rs", "fn f(p: *const std::ffi::c_char) {}\n")
        self.write("input/src/b.rs", "fn g(p: *const i8, q: *mut i8) {}\n")
        [v] = verify.predict(self.root, [diagnostic("error[E0308]: mismatched types", details=detail)], ARM, changed={"input/src/a.rs"})
        self.assertIs(v.ok, False)
        self.assertIn("2 `i8` C-string pointer type(s) remain: input/src/b.rs (2)", v.text)
        self.write("input/src/b.rs", "fn g(p: *const std::ffi::c_char) {}\n")
        [v] = verify.predict(self.root, [diagnostic("error[E0308]: mismatched types", details=detail)], ARM, changed={"input/src/b.rs"})
        self.assertIs(v.ok, True)

    def test_rules_flag_reaches_the_build_or_not(self):
        self.write("input/debian/rules", RULES)
        verdicts = verify.predict(self.root, [diagnostic("collect2: error: ld returned 1 exit status")], ARM,
                                  changed={"input/debian/rules"}, originals={"input/debian/rules": "%:\n\tdh $@\n"},
                                  log_lines=["hidden symbol `__aarch64_cas8_acq_rel' in libgcc.a is referenced by DSO"])
        texts = [v.text for v in verdicts if v.ok]
        self.assertTrue(any("exported with value '-mno-outline-atomics' on arm64" in t for t in texts))
        self.assertTrue(any("will no longer be emitted" in t for t in texts))
        self.write("input/debian/rules", RULES.replace("arm64", "amd64").replace("export DEB_CFLAGS", "DEB_CFLAGS"))
        verdicts = verify.predict(self.root, [], ARM, changed={"input/debian/rules"})
        self.assertTrue(any(v.ok is False and "false on arm64" in v.text for v in verdicts))

    def test_symbols_and_quilt_patch(self):
        self.write("input/debian/libp.symbols.amd64", "libp.so.1 libp #MINVER#\n _ZN3bppA@Base 1.0\n (arch=!arm64)_ZN3bppB@Base 1.0\n")
        log = ["-#MISSING: 1.0# _ZN3bppA@Base 1.0", "-#MISSING: 1.0# _ZN3bppB@Base 1.0"]
        [v] = verify.predict(self.root, [], AMD, changed={"input/debian/libp.symbols.amd64"}, log_lines=log)
        self.assertIs(v.ok, False)
        self.assertIn("1 of the 2 symbols", v.text)
        [v] = verify.predict(self.root, [], AMD, changed={"input/debian/patches/fix.patch"})
        self.assertIn("debian/patches was edited by hand", v.text)


def turn(edits, hypothesis="repair"):
    return SimpleNamespace(action="edit", edits=edits, hypothesis=hypothesis, evidence=[], reason="", tool_requests=[])


def edit(old, new, path):
    return SimpleNamespace(path=path, old_text=old, new_text=new, replace_all=False)


STOP = SimpleNamespace(action="stop", reason="done", edits=[], tool_requests=[])
TOOL = SimpleNamespace(action="tool", reason="look", edits=[],
                       tool_requests=[SimpleNamespace(tool="list_dir", args={"path": "input"})])


class InTheLoop(Worktree):
    def setUp(self):
        super().setUp()
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH)
        self.write("input/src/crc32c/crc32c_config.h", "#define HAVE_MM_PREFETCH 1\n")
        self.context = SimpleNamespace(worktree=self.root, case_id="launchpad-mantic-amd64-arm64-r-cran-digest-7a42",
                                       task_metadata={"target_arch": "arm64"}, log_tail="")
        self.requests = []

    def run_script(self, proposals, **kw):
        proposals = iter(proposals)

        def next_turn(request):
            self.requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}
        return run_loop(self.context, next_turn=next_turn, config=AgentConfig(use_build_feedback=False),
                        initial_diagnostics=[Verdicts.INCLUDE], **kw)

    def test_prediction_follows_an_unbuilt_batch(self):
        self.run_script([turn([edit("#define HAVE_MM_PREFETCH 1", "#define HAVE_MM_PREFETCH 0",
                                    "input/src/crc32c/crc32c_config.h")]), STOP])
        self.assertIn("[fixed] input/src/crc32c/crc32c_prefetch.h: the include of xmmintrin.h at line 8 is no longer "
                      "compiled on arm64", self.requests[1].prediction)
        self.assertIn("reply stop", self.requests[1].prediction)

    def test_prediction_says_not_fixed_for_an_irrelevant_edit(self):
        self.run_script([turn([edit("#if defined(_MSC_VER)", "#if defined(_MSC_VER) /* x */",
                                    "input/src/crc32c/crc32c_prefetch.h")]), STOP, STOP])   # the first stop is bounced once
        self.assertIn("[NOT fixed]", self.requests[1].prediction)
        self.assertIn("HAVE_MM_PREFETCH is defined at input/src/crc32c/crc32c_config.h:1", self.requests[1].prediction)

    def test_tool_turns_during_a_correction_are_capped(self):
        # main's no-build control bounces a stop with no change once: two STOPs.
        self.run_script([turn([edit("nowhere at all in this file", "x", "input/src/crc32c/crc32c_config.h")]),
                         TOOL, TOOL, TOOL, turn([]), STOP, STOP])
        feedback = self.requests[4].edit_validation_feedback
        self.assertIn("You have used 2 tool turns on this correction", feedback)
        self.assertEqual(len(self.requests[3].tool_results), 2)
        self.assertEqual(len(self.requests[4].tool_results), 2)   # the third was not run


class OpeningMessage(Worktree):
    def test_the_architecture_block_sits_after_the_errors(self):
        self.write("input/src/crc32c/crc32c_prefetch.h", PREFETCH)
        self.write("input/src/crc32c/crc32c_config.h", "#define HAVE_MM_PREFETCH 1\n")
        context = SimpleNamespace(worktree=self.root, case_id="launchpad-mantic-amd64-arm64-r-cran-digest-7a42",
                                  task_metadata={"target_arch": "arm64"}, log_tail="")
        adapter = ModelAdapter(lambda messages: None, context, config=AgentConfig(use_build_feedback=False),
                               budget=Budget(), diagnostics=[Verdicts.INCLUDE], log_path=None)
        try:
            block = adapter.architecture
        finally:
            adapter.close()
        self.assertIn("What differs between amd64 (where the package built) and arm64", block)
        self.assertIn("char signedness", block)
        self.assertIn("crc32c_prefetch.h:8 [x86-intrinsics-include] reached on arm64: yes", block)
        self.assertIn("HAVE_MM_PREFETCH is defined at input/src/crc32c/crc32c_config.h:1", block)


if __name__ == "__main__":
    unittest.main()


class CMakeVerdicts(Worktree):
    """ectrans: the verdict after an edit says whether every imported target of
    the package is repaired after find_package, and whether the directory was
    replaced rather than cleared."""

    LOG = ['CMake Error in src/trans/CMakeLists.txt:',
           '  Imported target "fiat" includes non-existent path',
           '    "/usr/lib/aarch64-linux-gnu/fortran/aarch64-linux-gnu-gfortran-13/fiat"',
           '  in its INTERFACE_INCLUDE_DIRECTORIES.  Possible reasons include:']
    TOP = "project( ectrans Fortran )\n\necbuild_find_package( NAME fiat REQUIRED )\n\nadd_subdirectory( src )\n"
    TRANS = ("foreach( prec dp sp )\n  ecbuild_add_library( TARGET trans_${prec} PUBLIC_LIBS fiat parkind_${prec} )\n"
             "endforeach()\n")

    def setUp(self):
        super().setUp()
        self.write("input/CMakeLists.txt", self.TOP)
        self.write("input/src/trans/CMakeLists.txt", self.TRANS)

    def verdicts(self, changed):
        from src import patterns  # noqa: F401
        return verify.predict(self.root, [], ARM, changed=changed, log_lines=self.LOG)

    def test_clearing_one_target_is_not_the_repair(self):
        self.write("input/src/trans/CMakeLists.txt",
                   'if(TARGET fiat)\n  set_target_properties(fiat PROPERTIES INTERFACE_INCLUDE_DIRECTORIES "")\nendif()\n' + self.TRANS)
        vs = self.verdicts({"input/src/trans/CMakeLists.txt"})
        by = {v.text.split("`")[1]: v for v in vs}
        self.assertIs(by["fiat"].ok, None)
        self.assertIn("cleared", by["fiat"].text)
        self.assertIs(by["parkind_dp"].ok, False)
        self.assertIn("same package", by["parkind_dp"].text)
        self.assertIn("address it in the next edit", verify.render(vs, ARM))

    def test_the_loop_over_every_target_after_find_package_is_fixed(self):
        from src import patterns
        snippet = patterns.cmake_repair_snippet(["fiat", "parkind_dp", "parkind_sp"])
        self.write("input/CMakeLists.txt", self.TOP.replace("REQUIRED )\n", "REQUIRED )\n" + snippet + "\n"))
        vs = self.verdicts({"input/CMakeLists.txt"})
        self.assertEqual([v.ok for v in vs], [True, True, True])
        self.assertIn("reply stop", verify.render(vs, ARM))

    def test_a_repair_placed_before_find_package_does_nothing(self):
        from src import patterns
        snippet = patterns.cmake_repair_snippet(["fiat", "parkind_dp", "parkind_sp"])
        self.write("input/CMakeLists.txt", snippet + "\n" + self.TOP)
        vs = self.verdicts({"input/CMakeLists.txt"})
        self.assertTrue(all(v.ok is False for v in vs))
        self.assertIn("before input/CMakeLists.txt:", [v for v in vs if "fiat" in v.text][0].text)

    def test_no_cmake_edit_no_cmake_verdict(self):
        self.assertEqual(self.verdicts({"input/debian/rules"}), [])


class SphinxVerdicts(Worktree):
    LOG = ["error: invalid command 'build_sphinx'"]
    RULES = ("#!/usr/bin/make -f\n%:\n\tdh $@ --with python3,sphinxdoc --buildsystem=pybuild\n\n"
             "override_dh_auto_build-indep:\n\tpython3 setup.py build_sphinx\n")

    def setUp(self):
        super().setUp()
        self.write("input/setup.cfg", "[build_sphinx]\nsource-dir = docs/\nbuild-dir = docs/_build\n")
        self.write("input/debian/python-intbitset-doc.install", "./docs/_build/html/ usr/share/doc/python-intbitset-doc/\n")

    def verdicts(self, rules):
        self.write("input/debian/rules", rules)
        return verify.predict(self.root, [], AMD, changed={"input/debian/rules"}, log_lines=self.LOG)

    def test_renaming_the_override_loses_the_docs(self):
        vs = self.verdicts(self.RULES.replace("override_dh_auto_build-indep:", "override_dh_auto_build-indep-disabled:"))
        self.assertTrue(all(v.ok is False for v in vs))
        self.assertTrue(any("missing files" in v.text for v in vs))

    def test_the_direct_sphinx_call_is_fixed(self):
        [v] = self.verdicts(self.RULES.replace("python3 setup.py build_sphinx", "python3 -m sphinx -b html docs/ docs/_build/html"))
        self.assertIs(v.ok, True)
        self.assertIn("python-intbitset-doc.install:1 installs", v.text)

    def test_leaving_the_command_is_not_fixed(self):
        vs = self.verdicts(self.RULES + "\n# touched\n")
        self.assertTrue(any(v.ok is False and "Sphinx 7 removed" in v.text for v in vs))


class SymbolsMacroInTheLoop(Worktree):
    LOG = ["dpkg-gensymbols: error: some symbols or patterns disappeared in the symbols file: see diff output below",
           "-#MISSING: 1.0# _ZN3bppA@Base 1.0", "- _ZN3bppB@Base 1.0", "dh_makeshlibs: error: failing due to earlier errors"]

    def test_the_macro_edit_is_expanded_applied_and_judged_fixed(self):
        from src import patterns
        path = "input/debian/libp.symbols.amd64"
        self.write(path, "libp.so.1 libp #MINVER#\n _ZN3bppA@Base 1.0\n _ZN3bppB@Base 1.0\n _ZN3bppC@Base 1.0\n")
        log = self.write("build.log", "\n".join(self.LOG) + "\n")
        context = SimpleNamespace(worktree=self.root, case_id="launchpad-jammy-arm64-amd64-libbpp-core-13069fd09280",
                                  task_metadata={"target_arch": "amd64"}, log_tail="")
        requests = []
        proposals = iter([turn([edit(patterns.SYMBOLS_MACRO, "optional", path)]), STOP])

        def next_turn(request):
            requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}
        run_loop(context, next_turn=next_turn, config=AgentConfig(use_build_feedback=False),
                 initial_diagnostics=[diagnostic("dh_makeshlibs: error: failing due to earlier errors")], log_path=log)
        text = (self.root / path).read_text()
        self.assertIn(" (optional)_ZN3bppA@Base 1.0\n (optional)_ZN3bppB@Base 1.0\n _ZN3bppC@Base 1.0\n", text)
        self.assertIn("[fixed] input/debian/libp.symbols.amd64: every reported-missing symbol tagged (2); nothing else changed.",
                      requests[1].prediction)

    def test_a_tag_after_the_version_is_not_fixed(self):
        path = "input/debian/libp.symbols.amd64"
        self.write(path, "libp.so.1 libp #MINVER#\n _ZN3bppA@Base 1.0 (optional)\n _ZN3bppB@Base (optional) 1.0\n")
        vs = verify.predict(self.root, [], AMD, changed={path}, log_lines=self.LOG)
        self.assertTrue(any(v.ok is False and "is not a valid version" in v.text for v in vs))
