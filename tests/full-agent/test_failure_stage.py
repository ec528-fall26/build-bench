"""Part A: failure-stage classification; no model, network or build.

The main check uses real data: the committed Demo 2 audit
(docs/evidence/demo2/paired-retest/audit.json) keeps selected lines and the
tail of every baseline build log, with a human classification of each case.
The classifier must agree with that review on all eleven cases. For the setup
cases that agreement mostly reflects the validator's own stage event, which the
review also used; the log-only path is tested separately below.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
from src import failure
from src.failure import STAGES, FailureStage, classify_failure
from src import main as agent_main

AUDIT = ROOT / "docs/evidence/demo2/paired-retest/audit.json"
HELLO = Path(__file__).resolve().parent / "fixtures" / "hello-rpm"
HELLO_REPAIRED_LOG = ROOT / "tests/one-shot/part2-log-tail/fixtures/build.log"
HELLO_FAILED_LOG = ROOT / "tests/one-shot/part2-log-tail/fixtures/initial-build.log"

# The human review's categories, and the stages that agree with each.
AGREES = {
    "environment_blocked": {"setup"},
    "already_building": {"none"},
    "source_repair_evaluated": {"configure", "compile", "link", "test", "install", "packaging"},
}


def baseline_excerpts():
    """(case_id, human classification, excerpt lines) for each case's baseline log."""
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    seen = {}
    for run in audit["runs"]:
        for excerpt in run["log_excerpts"]:
            if excerpt["source"].endswith("/baseline/build.log") and run["case_id"] not in seen:
                lines = {item["line"]: item["text"]
                         for key in ("selected_lines", "tail") for item in excerpt.get(key, [])}
                seen[run["case_id"]] = (run["classification"], [lines[n] for n in sorted(lines)])
    return [(case, label, lines) for case, (label, lines) in sorted(seen.items())]


class Classify(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.log = Path(temp.name) / "build.log"

    def classify(self, text):
        self.log.write_text(text, encoding="utf-8")
        result = classify_failure(self.log)
        self.assertIn(result.stage, STAGES)
        return result


class Demo2Audit(Classify):
    def test_agrees_with_the_human_review_on_all_eleven_cases(self):
        cases = baseline_excerpts()
        self.assertEqual(len(cases), 11)
        for case, label, lines in cases:
            with self.subTest(case=case):
                result = self.classify("\n".join(lines) + "\n")
                self.assertIn(result.stage, AGREES[label], result)
                self.assertEqual(result.source, "validator")
                if label == "environment_blocked":
                    self.assertIs(result.repairable_in_package, False)
                elif label == "source_repair_evaluated":
                    self.assertIs(result.repairable_in_package, True)

    def test_specific_stages_from_real_excerpts(self):
        # libmceliece's excerpt has no ld line (it is in the full log), so only the
        # build step is left: compile, labelled as inferred.
        expected = {"r-cran-digest": "compile", "ectrans": "configure", "libunwind": "link",
                    "rust-subversion": "compile", "libmceliece": "compile"}
        for case, _, lines in baseline_excerpts():
            name = next((n for n in expected if n in case), None)
            if name:
                with self.subTest(case=name):
                    self.assertEqual(self.classify("\n".join(lines) + "\n").stage, expected[name])

    def test_setup_evidence_is_reported_not_explained(self):
        case, _, lines = next(c for c in baseline_excerpts() if "libbpp-core" in c[0])
        result = self.classify("\n".join(lines) + "\n")
        joined = " ".join(result.evidence)
        self.assertIn("build_setup", joined)
        self.assertIn("Segmentation fault", joined)
        self.assertIn("times", joined)
        self.assertNotIn("QEMU", joined)        # observed, not diagnosed


class HelloLogs(Classify):
    def test_real_rpm_build_failure_is_the_build_section(self):
        # rpmbuild prints "RPM build errors:" after a failure in any section.
        result = classify_failure(HELLO_FAILED_LOG)
        self.assertEqual((result.stage, result.build_step), ("compile", "%build"))
        self.assertIs(result.repairable_in_package, True)

    def test_hello_setup_failure_without_any_error_keyword(self):
        # chroot: can't execute '/.build/build' — no "error" anywhere on the line.
        result = classify_failure(HELLO / "input/initial-build.log")
        self.assertEqual((result.stage, result.validator_stage), ("setup", "build_setup"))
        self.assertIs(result.repairable_in_package, False)

    def test_repaired_hello_build_succeeded(self):
        result = classify_failure(HELLO_REPAIRED_LOG)
        self.assertEqual((result.stage, result.source), ("none", "validator"))
        self.assertIsNone(result.repairable_in_package)


class FromLogOnly(Classify):
    """Logs without the validator's stage events, e.g. an excerpt."""

    def test_unresolvable_build_dependency(self):
        result = self.classify("[  3s] unresolvable: nothing provides libfoo-dev needed by bar\n")
        self.assertEqual((result.stage, result.source), ("dependencies", "log"))
        self.assertIs(result.repairable_in_package, True)

    def test_setup_before_the_package_build(self):
        result = self.classify("Segmentation fault (core dumped)\n"
                               "chroot: failed to run command '/bin/sh': No such file or directory\n")
        self.assertEqual(result.stage, "setup")

    def test_earliest_specific_error_wins_and_names_the_step(self):
        result = self.classify(
            "dpkg-buildpackage: info: source package x\n   dh_auto_build\n"
            "src/a.c:3:1: error: unknown type name 'foo'\n"
            "/usr/bin/ld: cannot find -lbar\n"
            "make: *** [debian/rules:7: binary] Error 2\n")
        self.assertEqual((result.stage, result.build_step), ("compile", "dh_auto_build"))
        self.assertIn("unknown type name", result.evidence[0])

    def test_rust_compiling_during_dh_auto_test_is_compile(self):
        result = self.classify("   dh_auto_test\nerror[E0308]: mismatched types\n"
                               "error: could not compile `x` (lib) due to 1 previous error\n"
                               "dh_auto_test: error: cargo build returned exit code 101\n")
        self.assertEqual((result.stage, result.build_step), ("compile", "dh_auto_test"))

    def test_fortran_error_is_compile(self):
        result = self.classify("Fatal Error: Cannot open module file 'parkind1.mod' for reading "
                               "at (1): No such file or directory\n")
        self.assertEqual(result.stage, "compile")

    def test_linker(self):
        for line in ("/bin/ld: BFD (GNU Binutils) 2.41 assertion fail ../../bfd/elfnn-aarch64.c:9754",
                     "foo.c:(.text+0x1c): undefined reference to `bar'",
                     "collect2: error: ld returned 1 exit status"):
            with self.subTest(line=line):
                self.assertEqual(self.classify(line + "\n").stage, "link")

    def test_ctest_success_line_is_not_a_test_failure(self):
        result = self.classify("   dh_auto_test\n100% tests passed, 0 tests failed out of 12\n"
                               "dh_missing: error: usr/lib/x.so exists in debian/tmp but is not "
                               "installed to anywhere\n")
        self.assertEqual(result.stage, "packaging")
        self.assertEqual(self.classify("   dh_auto_test\n75% tests passed, 3 tests failed out of 12\n").stage,
                         "test")

    def test_a_segfault_during_the_package_build_is_not_setup(self):
        result = self.classify("   dh_auto_test\nSegmentation fault (core dumped)\n"
                               "dh_auto_test: error: make -j2 check returned exit code 2\n")
        self.assertEqual(result.stage, "test")
        self.assertIs(result.repairable_in_package, True)

    def test_test_and_packaging_failures(self):
        self.assertEqual(self.classify("   dh_auto_test\nFAIL: test_parse\n").stage, "test")
        self.assertEqual(self.classify(
            "dh_missing: error: usr/lib/libx.so exists in debian/tmp but is not installed\n").stage,
            "packaging")
        self.assertEqual(self.classify(
            "error: Installed (but unpackaged) file(s) found:\n").stage, "packaging")

    def test_optional_cmake_package_is_not_a_configure_failure(self):
        result = self.classify("-- Could NOT find Doxygen (missing: DOXYGEN_EXECUTABLE)\n"
                               "   dh_auto_build\nx.c:1:1: error: bad\n")
        self.assertEqual(result.stage, "compile")

    def test_step_only_inference_is_labelled(self):
        result = self.classify("+ Executing(%build): /bin/sh -e /var/tmp/rpm-tmp.1\n"
                               "intentional failure\nerror: Bad exit status from /var/tmp/rpm-tmp.1 (%build)\n")
        self.assertEqual((result.stage, result.build_step), ("compile", "%build"))
        self.assertIn("inferred from the build step", result.evidence[-1])

    def test_nothing_recognizable_is_unknown(self):
        result = self.classify("hello\nworld\n")
        self.assertEqual((result.stage, result.source), ("unknown", "none"))
        self.assertIsNone(result.repairable_in_package)

    def test_missing_log_does_not_raise(self):
        result = classify_failure(self.log.parent / "absent.log")
        self.assertEqual(result.stage, "unknown")

    def test_evidence_is_bounded(self):
        result = self.classify("".join(f"x{i}.c:1:1: error: {'y' * 1000}\n" for i in range(50))
                               + "".join(f"/bin/ld: problem {i}\n" for i in range(50)))
        self.assertLessEqual(len(result.evidence), 8)
        self.assertTrue(all(len(line) <= 300 for line in result.evidence))

    def test_other_validator_stages(self):
        for stage, expected in (("backend_preflight", "setup"), ("manifest_validation", "setup"),
                                ("dependency_preflight", "dependencies")):
            with self.subTest(stage=stage):
                result = self.classify('[build-case] [event] {"event":"stage","stage":"%s",'
                                       '"state":"failed"}\n' % stage)
                self.assertEqual((result.stage, result.source), (expected, "validator"))

    def test_large_logs_are_read_from_the_tail(self):
        head = "Segmentation fault (core dumped)\n" * 50
        tail = "   dh_auto_build\nsrc/a.c:1:1: error: real\n"
        with patch.object(failure, "TAIL_BYTES", len(tail) + 10):
            result = self.classify(head + tail)
        self.assertEqual(result.stage, "compile")
        self.assertIn("only the last", result.evidence[0])

    def test_malformed_events_are_ignored(self):
        result = self.classify("[build-case] [event] {" + '"a":' + "[" * 100_000 + "}\n"
                               "src/a.c:1:1: error: real\n")
        self.assertEqual(result.stage, "compile")

    def test_validator_event_overrides_log_guess(self):
        result = self.classify(
            "Segmentation fault (core dumped)\nsrc/a.c:1:1: error: bad\n"
            '[build-case] [event] {"event":"stage","stage":"package_build","state":"failed"}\n')
        self.assertEqual((result.stage, result.validator_stage), ("compile", "package_build"))


class Wiring(unittest.TestCase):
    def test_a_classifier_failure_is_recorded_not_raised(self):
        with patch.object(agent_main, "classify_failure", side_effect=RuntimeError("boom")):
            stage = agent_main._failure_stage(Path("x.log"))
        self.assertEqual(stage, FailureStage(evidence=["classification failed: RuntimeError"]))

    def test_main_records_the_failure_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shutil.copytree(HELLO / "input", workspace / "input")
            shutil.copytree(HELLO / "work", workspace / "work")
            with patch.dict(os.environ, {"BB_WORKSPACE": str(workspace), "BB_MODEL_API_KEY": ""}), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                for name in [k for k in os.environ if k.startswith("BB_AGENT_")]:
                    os.environ.pop(name)
                self.assertEqual(agent_main.main(), 0)
            result = json.loads((workspace / "output/agent-result.json").read_text())
            self.assertEqual(result["failure_stage"]["stage"], "setup")
            self.assertIs(result["failure_stage"]["repairable_in_package"], False)


if __name__ == "__main__":
    unittest.main()


QEMU_FIX = ROOT / "docs/evidence/qemu-fix-20261102"


class QemuFixLogs(unittest.TestCase):
    """Real full logs from 2 November: the five x86_64-target cases after the
    emulator update, and python-intbitset before it. Stages agree with
    five-case-results.md, which was written by hand from the same logs."""

    def classify(self, relative):
        return classify_failure(QEMU_FIX / relative / "build.log")

    def test_before_the_update_it_is_setup(self):
        result = self.classify("before-qemu8")
        self.assertEqual((result.stage, result.validator_stage), ("setup", "build_infrastructure"))
        self.assertIs(result.repairable_in_package, False)

    def test_after_the_update_each_case_reaches_its_package_stage(self):
        expected = {
            "x86-original-qemu10-20261102T022142Z/launchpad-noble-arm64-amd64-libbpp-qt-32eb71858f9f":
                ("packaging", "dpkg-gensymbols: error"),
            "x86-remaining-qemu10-20261102T030236Z/launchpad-jammy-arm64-amd64-libbpp-core-13069fd09280":
                ("packaging", "dpkg-gensymbols: error"),
            "x86-original-qemu10-20261102T022142Z/launchpad-resolute-arm64-amd64-pybigwig-56ac842288ce":
                ("link", "recompile with -fPIC"),
            "x86-remaining-qemu10-20261102T030236Z/launchpad-plucky-arm64-amd64-libevtx-1ee53325da7e":
                ("test", "FAIL: test_tools.sh"),
            # A documentation build ("invalid command 'build_sphinx'") inside
            # dh_auto_build: no specific message, so only the step is reported.
            "after-qemu10": ("compile", "inferred from the build step dh_auto_build"),
        }
        for relative, (stage, evidence) in expected.items():
            with self.subTest(case=relative.rsplit("/", 1)[-1]):
                result = self.classify(relative)
                self.assertEqual((result.stage, result.validator_stage), (stage, "package_build"))
                self.assertIs(result.repairable_in_package, True)
                self.assertIn(evidence, " ".join(result.evidence))
