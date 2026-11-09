"""The architecture model (arch_model.py) and the evaluators that predict a
build's control flow under a target architecture (predict.py). No model,
network, compiler or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import arch_model, predict  # noqa: E402

ARM = arch_model.lookup("arm64")
AMD = arch_model.lookup("amd64")


class Model(unittest.TestCase):
    def test_lookup_by_any_name(self):
        for name in ("arm64", "aarch64", "aarch64-linux-gnu", "ARM64"):
            self.assertIs(arch_model.lookup(name), ARM)
        self.assertIs(arch_model.lookup("x86_64"), AMD)
        self.assertIsNone(arch_model.lookup("mips64el"))

    def test_c_char(self):
        self.assertEqual((AMD.c_char, ARM.c_char), ("i8", "u8"))

    def test_source_from_case_id_or_counterpart(self):
        self.assertEqual(arch_model.pair_from_case_id("launchpad-plucky-amd64-arm64-libunwind-f18b"), ("amd64", "arm64"))
        self.assertIs(arch_model.source_for(ARM, "launchpad-plucky-amd64-arm64-x-1"), AMD)
        self.assertIs(arch_model.source_for(AMD, "launchpad-jammy-arm64-amd64-libbpp-core-1"), ARM)
        self.assertIs(arch_model.source_for(ARM, "no-pair-here"), AMD)
        self.assertIs(arch_model.source_for(AMD), ARM)

    def test_differences_name_the_facts_that_matter(self):
        facts = [d.fact for d in arch_model.differences(AMD, ARM)]
        self.assertEqual(facts[:3], ["char signedness", "x86 intrinsics headers", "outline atomics"])
        self.assertIn("long double", facts)
        self.assertNotIn("endianness", facts)
        text = arch_model.render(AMD, ARM)
        self.assertIn("std::ffi::c_char", text)
        self.assertIn("__x86_64__", text)
        self.assertEqual(arch_model.render(None, ARM), "")


class Preprocessor(unittest.TestCase):
    RCRAN = ("#include \"crc32c/crc32c_config.h\"\n"      # 1
             "\n"                                           # 2
             "#if HAVE_MM_PREFETCH\n"                       # 3
             "\n"                                           # 4
             "#if defined(_MSC_VER)\n"                      # 5
             "#include <intrin.h>\n"                        # 6
             "#else  // !defined(_MSC_VER)\n"               # 7
             "#include <xmmintrin.h>\n"                     # 8
             "#endif  // defined(_MSC_VER)\n"               # 9
             "\n"                                           # 10
             "#endif  // HAVE_MM_PREFETCH\n")               # 11

    def test_rcran_include_is_reached_only_when_the_macro_is_1(self):
        self.assertIs(predict.cpp_reached(self.RCRAN, 8, ARM, {"HAVE_MM_PREFETCH": 1}), True)
        self.assertIs(predict.cpp_reached(self.RCRAN, 8, ARM, {"HAVE_MM_PREFETCH": 0}), False)
        self.assertIs(predict.cpp_reached(self.RCRAN, 8, ARM, {}), None)   # the macro is unknown
        self.assertIs(predict.cpp_reached(self.RCRAN, 6, ARM, {"HAVE_MM_PREFETCH": 1}), False)  # _MSC_VER

    def test_architecture_macros_select_the_branch(self):
        text = ("#if defined(__x86_64__) || defined(__i386__)\nx86\n"
                "#elif defined(__aarch64__)\narm\n#else\nother\n#endif\n")
        arm = predict.cpp_active_lines(text, ARM).active
        amd = predict.cpp_active_lines(text, AMD).active
        self.assertEqual((arm[1], arm[3], arm[5]), (False, True, False))
        self.assertEqual((amd[1], amd[3], amd[5]), (True, False, False))

    def test_expressions(self):
        for expression, expected in (
            ("__aarch64__ && !__x86_64__", True), ("__ARM_ARCH >= 8", True), ("__ARM_ARCH < 8", False),
            ("defined(__aarch64__) && __LP64__", True), ("FOO", None), ("!defined(FOO)", None),
            ("defined __x86_64__", False), ("(1 + 1) == 2", True), ("3 > 2 ? 0 : 1", False),
            ("__GNUC__ >= 4", None), ("defined(__GNUC__)", True), ("defined(__linux__) && !defined(_WIN32)", True),
        ):
            text = f"#if {expression}\nyes\n#endif\n"
            self.assertIs(predict.cpp_active_lines(text, ARM).active[1], expected, expression)

    def test_defines_in_the_file_are_followed_in_order(self):
        text = ("#define USE_SSE 1\n#if USE_SSE\nsse\n#endif\n#undef USE_SSE\n#if USE_SSE\nagain\n#endif\n"
                "#if defined(__x86_64__)\n#define HAVE_FAST 1\n#else\n#define HAVE_FAST 0\n#endif\n"
                "#if HAVE_FAST\nfast\n#endif\n")
        state = predict.cpp_active_lines(text, ARM)
        self.assertEqual((state.active[2], state.active[6], state.active[14]), (True, None, False))
        self.assertEqual(state.defines["HAVE_FAST"], 0)

    def test_continuation_lines_belong_to_their_directive(self):
        text = "#if defined(__x86_64__) || \\\n    defined(__i386__)\nx86\n#endif\n"
        self.assertEqual(predict.cpp_active_lines(text, ARM).active[:3], [True, True, False])

    def test_nested_false_block_hides_everything_inside(self):
        text = "#ifdef __x86_64__\n#ifdef UNKNOWN\na\n#else\nb\n#endif\n#endif\n"
        self.assertEqual(predict.cpp_active_lines(text, ARM).active[2:5], [False, False, False])

    def test_project_defines_evaluated_under_the_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "input/src").mkdir(parents=True)
            (root / "input/src/config.h").write_text(
                "#if defined(__x86_64__)\n#define HAVE_MM_PREFETCH 1\n#else\n#define HAVE_MM_PREFETCH 0\n#endif\n"
                "#define HAVE_BUILTIN_PREFETCH 1\n#define VERSION \"1.2\"\n")
            (root / "input/.pc/x").mkdir(parents=True)
            (root / "input/.pc/x/config.h").write_text("#define HAVE_MM_PREFETCH 1\n")
            found = predict.project_defines(root, {"HAVE_MM_PREFETCH", "HAVE_BUILTIN_PREFETCH", "VERSION", "MISSING"}, ARM)
            self.assertEqual(found, {"HAVE_MM_PREFETCH": 0, "HAVE_BUILTIN_PREFETCH": 1, "VERSION": None})
            self.assertEqual(predict.project_defines(root, {"HAVE_MM_PREFETCH"}, AMD), {"HAVE_MM_PREFETCH": 1})


class Make(unittest.TestCase):
    RULES = ("#!/usr/bin/make -f\n"                                        # 1
             "export DEB_BUILD_MAINT_OPTIONS = hardening=-all,+format\n"   # 2
             "ifneq (,$(filter $(DEB_HOST_ARCH), amd64 i386))\n"           # 3
             "  CXX_EXCEPTIONS=--enable-cxx-exceptions\n"                  # 4
             "endif\n"                                                     # 5
             "ifeq ($(DEB_HOST_ARCH),arm64)\n"                             # 6
             "export DEB_CFLAGS_MAINT_APPEND += -mno-outline-atomics\n"    # 7
             "DEB_CXXFLAGS_MAINT_APPEND += -mno-outline-atomics\n"         # 8
             "endif\n"                                                     # 9
             "%:\n\tdh $@\n")

    def test_libunwind_rules_under_both_architectures(self):
        arm = predict.make_active_lines(self.RULES, ARM)
        self.assertEqual(arm.flags, {"DEB_BUILD_MAINT_OPTIONS": "hardening=-all,+format",
                                     "DEB_CFLAGS_MAINT_APPEND": "-mno-outline-atomics"})
        self.assertNotIn("DEB_CXXFLAGS_MAINT_APPEND", arm.exported)   # assigned but not exported
        self.assertEqual((arm.active[3], arm.active[6]), (False, True))
        self.assertNotIn("CXX_EXCEPTIONS", arm.variables)
        amd = predict.make_active_lines(self.RULES, AMD)
        self.assertEqual(amd.variables["CXX_EXCEPTIONS"], "--enable-cxx-exceptions")
        self.assertNotIn("DEB_CFLAGS_MAINT_APPEND", amd.flags)

    def test_export_on_its_own_line_and_else_branches(self):
        text = ("DEB_CFLAGS_MAINT_APPEND = -O2\nexport DEB_CFLAGS_MAINT_APPEND\n"
                "ifeq ($(DEB_HOST_ARCH),amd64)\nX = a\nelse ifeq ($(DEB_HOST_ARCH),arm64)\nX = b\nelse\nX = c\nendif\n"
                "export X\n")
        self.assertEqual(predict.make_active_lines(text, ARM).exported, {"DEB_CFLAGS_MAINT_APPEND": "-O2", "X": "b"})
        self.assertEqual(predict.make_active_lines(text, AMD).exported["X"], "a")

    def test_unknown_constructs_are_unknown_not_guessed(self):
        text = "ifeq ($(shell uname -m),aarch64)\nY = 1\nendif\nifdef SOMETHING_UNSET\nZ = 1\nendif\n"
        state = predict.make_active_lines(text, ARM)
        self.assertIsNone(state.active[1])
        self.assertIsNone(state.variables.get("Y", None))
        self.assertIs(state.active[4], False)

    def test_multiarch_and_recipe_lines(self):
        text = "LIBDIR = /usr/lib/$(DEB_HOST_MULTIARCH)\n%:\n\tdh $@ --libdir=$(LIBDIR)\n"
        state = predict.make_active_lines(text, ARM)
        self.assertEqual(state.variables["LIBDIR"], "/usr/lib/aarch64-linux-gnu")
        self.assertEqual(state.active, [True, True, True, True])


if __name__ == "__main__":
    unittest.main()
