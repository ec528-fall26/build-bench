"""Part D: the local build callback, runnable on macOS.

dpkg-source and run.sh are replaced by FakeValidator, which imitates their file
effects and exit codes. Part A is replaced by a small stand-in so this suite
never imports an agent's `src` package (the harness suite already imports the
baseline's); tests/full-agent/test_build_feedback_part_a.py runs the real one.

Run from the repository root:  python3 -m unittest discover -s harness/tests -t .
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.build_feedback import (
    EXCERPT_TAIL, LocalBuildFeedback, load_full_agent, prepare_worktree, write_excerpt,
)
from harness.run_case import CommandFailed


FIXTURES = Path(__file__).resolve().parent / "fixtures"
DSC = "r-cran-digest_0.6.32-1.dsc"
ERROR = "crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: No such file or directory"


@dataclass
class Diagnostic:
    message: str
    stage: str
    log_path: str | None
    line: int | None
    resolved: list = field(default_factory=list)
    source_line: str | None = None
    log_offset: int = -1


@dataclass
class BuildResult:
    succeeded: bool
    stage_reached: str
    diagnostics: list
    duration_seconds: float


def fake_diagnose(log, worktree):
    """Every line containing "error" is a build-stage diagnostic, with its offset."""
    found, offset = [], 0
    for raw in Path(log).read_bytes().splitlines(keepends=True):
        text = raw.decode().strip()
        if "error" in text and text not in [d.message for d in found]:
            found.append(Diagnostic(text, "build", None, None, log_offset=offset))
        offset += len(raw)
    return found


AGENT = SimpleNamespace(diagnose=fake_diagnose, BuildResult=BuildResult, Diagnostic=Diagnostic)


class FakeValidator:
    """Imitates dpkg-source -b and run.sh. The build passes when the candidate
    turns HAVE_MM_PREFETCH off, as the verified rcran repair does."""

    def __init__(self, repack_fails=False, no_result=False, real_build=True,
                 timed_out=False, log=None):
        self.repack_fails, self.no_result = repack_fails, no_result
        self.real_build, self.timed_out = real_build, timed_out
        self.log = log if log is not None else "noise\n" * 50 + ERROR + "\n"
        self.calls = []

    def __call__(self, cmd, cwd, log, timeout):
        cmd = [str(c) for c in cmd]
        self.calls.append((cmd, Path(cwd)))
        if cmd[:2] == ["dpkg-source", "-x"]:
            (Path(cwd) / cmd[3] / "src").mkdir(parents=True)
        elif cmd[:2] == ["dpkg-source", "--auto-commit"]:
            if self.repack_fails:
                Path(log).write_text("dpkg-source: error: patch debian/patches/x is malformed\n")
                raise CommandFailed("dpkg-source exited 25")
            patches = Path(cwd) / "debian" / "patches"
            patches.mkdir(parents=True, exist_ok=True)
            (patches / "debian-changes").write_text("auto-commit patch")
            (Path(cwd).parent / DSC).write_text("rebuilt dsc")
        elif cmd[0] == "bash":
            self._build(cmd)

    def _build(self, cmd):
        out = Path(cmd[cmd.index("--output") + 1])
        out.mkdir()
        if self.no_result:
            raise CommandFailed("bash exited 125")
        case = Path(cmd[cmd.index("--input") + 1])
        config = case.parent / "work/input/src/crc32c/crc32c_config.h"
        passes = config.is_file() and "#define HAVE_MM_PREFETCH 0" in config.read_text()
        result = json.loads((FIXTURES / ("fix-1" if passes else "baseline-1")
                             / "build-result.json").read_text())
        result["timed_out"] = self.timed_out
        (out / "build-result.json").write_text(json.dumps(result))
        (out / "build-diagnostics.json").write_text(json.dumps(
            {"execution": {"reached_real_build": self.real_build}}))
        (out / "build.log").write_text("" if passes else self.log)
        if not passes:
            raise CommandFailed("bash exited 1")


class LocalBuildFeedbackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.bundle = root / "bundle"
        case = self.bundle / "case"
        (case / "input").mkdir(parents=True)
        (case / "config").mkdir()
        (self.bundle / "run.sh").write_text("")
        shutil.copy(FIXTURES / "rcran-manifest.json", case / "manifest.json")
        (case / "input" / DSC).write_text("original dsc")
        self.worktree = root / "work"
        source = self.worktree / "input/src/crc32c"
        source.mkdir(parents=True)
        shutil.copy(FIXTURES / "crc32c_config.h", source)
        self.builds = root / "builds"

    def feedback(self, validator, **kwargs):
        return LocalBuildFeedback(self.bundle, self.builds, AGENT, runner=validator, **kwargs)

    def apply_known_fix(self):
        config = self.worktree / "input/src/crc32c/crc32c_config.h"
        config.write_text(config.read_text().replace("#define HAVE_MM_PREFETCH 1",
                                                     "#define HAVE_MM_PREFETCH 0"))

    def test_known_fix_succeeds(self):
        self.apply_known_fix()
        feedback = self.feedback(FakeValidator())
        result = feedback.request_build(self.worktree)
        self.assertEqual((result.succeeded, result.stage_reached, result.diagnostics),
                         (True, "packaging", []))
        record = feedback.records[0]
        self.assertEqual((record["kind"], record["result"]["status"]), ("succeeded", "succeeded"))
        self.assertEqual(json.loads((self.builds / "build-01/feedback.json").read_text()), record)

    def test_wrong_candidate_fails_with_the_new_error(self):
        result = self.feedback(FakeValidator()).request_build(self.worktree)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.stage_reached, "build")
        self.assertEqual([d.message for d in result.diagnostics], [ERROR])

    def test_the_agents_worktree_is_never_changed(self):
        before = sorted(p.relative_to(self.worktree) for p in self.worktree.rglob("*"))
        validator = FakeValidator()
        self.feedback(validator).request_build(self.worktree)
        self.assertEqual(sorted(p.relative_to(self.worktree) for p in self.worktree.rglob("*")),
                         before)  # --auto-commit's patch went into the copy
        repack_cwd = next(cwd for cmd, cwd in validator.calls if cmd[0] == "dpkg-source")
        self.assertEqual(repack_cwd, (self.builds / "build-01/work/input").resolve())
        self.assertTrue((self.builds / "build-01/case/input" / DSC).is_file())
        self.assertTrue((self.builds / "build-01/case/config").is_dir())

    def test_rejected_repack_is_a_failed_candidate_not_an_error(self):
        validator = FakeValidator(repack_fails=True)
        feedback = self.feedback(validator)
        result = feedback.request_build(self.worktree)
        self.assertEqual((result.succeeded, result.stage_reached), (False, "unknown"))
        self.assertIn("malformed", result.diagnostics[0].message)
        self.assertEqual(feedback.records[0]["kind"], "repack_failed")
        self.assertFalse(any(cmd[0] == "bash" for cmd, _ in validator.calls))

    def test_build_that_never_compiles_ranks_lowest(self):
        feedback = self.feedback(FakeValidator(real_build=False))
        result = feedback.request_build(self.worktree)
        self.assertEqual((result.succeeded, result.stage_reached), (False, "unknown"))
        self.assertEqual(feedback.records[0]["kind"], "no_real_build")

    def test_timeout_is_a_failed_candidate(self):
        feedback = self.feedback(FakeValidator(timed_out=True))
        self.assertFalse(feedback.request_build(self.worktree).succeeded)
        self.assertEqual(feedback.records[0]["kind"], "timed_out")

    def test_failure_without_recognised_errors_still_explains_itself(self):
        result = self.feedback(FakeValidator(log="noise\n")).request_build(self.worktree)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertIn("expected binary artifact", result.diagnostics[0].message)

    def test_missing_validator_result_raises_and_is_recorded(self):
        feedback = self.feedback(FakeValidator(no_result=True))
        with self.assertRaisesRegex(RuntimeError, "validator produced no result"):
            feedback.request_build(self.worktree)
        self.assertEqual(feedback.records[0]["kind"], "validator_error")

    def test_each_build_gets_its_own_folder(self):
        feedback = self.feedback(FakeValidator())
        feedback.request_build(self.worktree)
        self.apply_known_fix()
        self.assertTrue(feedback.request_build(self.worktree).succeeded)
        self.assertEqual([r["build"] for r in feedback.records], [1, 2])
        self.assertTrue((self.builds / "build-01/result/build.log").read_text())

    def test_builds_inside_the_worktree_are_refused(self):
        feedback = LocalBuildFeedback(self.bundle, self.worktree / "builds", AGENT,
                                      runner=FakeValidator())
        with self.assertRaises(ValueError):
            feedback.request_build(self.worktree)

    def test_excerpt_mode_diagnoses_only_the_excerpt(self):
        log = "".join(f"line {n}\n" for n in range(500)) + ERROR + "\n" \
            + "".join(f"after {n}\n" for n in range(500))
        feedback = self.feedback(FakeValidator(log=log), excerpt=True)
        result = feedback.request_build(self.worktree)
        excerpt = (self.builds / "build-01/result/build.excerpt.log").read_text()
        self.assertIn(ERROR, excerpt)
        self.assertIn("after 499", excerpt)
        self.assertNotIn("line 100\n", excerpt)
        self.assertLess(len(excerpt.splitlines()), 50)
        self.assertEqual(result.diagnostics[0].message, ERROR)
        self.assertEqual(result.diagnostics[0].log_offset, excerpt.index(ERROR))
        self.assertTrue(feedback.records[0]["excerpt"])


class Excerpt(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def excerpt(self, text):
        log, dest = self.root / "build.log", self.root / "excerpt.log"
        log.write_text(text)
        write_excerpt(log, fake_diagnose(log, self.root), dest)
        return dest.read_text()

    def test_short_log_is_kept_whole(self):
        text = "a\nb error\nc"
        self.assertEqual(self.excerpt(text), text + "\n")

    def test_gaps_are_marked(self):
        text = "".join(f"{n}\n" for n in range(100)) + "x error\n" + "".join(
            f"t{n}\n" for n in range(100))
        lines = self.excerpt(text).splitlines()
        self.assertEqual(lines.count("[... lines omitted ...]"), 2)
        self.assertEqual(lines[-EXCERPT_TAIL:], [f"t{n}" for n in range(80, 100)])

    def test_empty_log(self):
        self.assertEqual(self.excerpt(""), "")


class Helpers(unittest.TestCase):
    def test_prepare_unpacks_the_case(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "bundle"
            (bundle / "case").mkdir(parents=True)
            shutil.copy(FIXTURES / "rcran-manifest.json", bundle / "case/manifest.json")
            validator = FakeValidator()
            dest = prepare_worktree(bundle, Path(temp) / "wt", runner=validator)
            self.assertTrue((dest / "input/src").is_dir())
            self.assertEqual(validator.calls[0][0][:2], ["dpkg-source", "-x"])

    def test_refuses_to_mix_two_agents_src_packages(self):
        other = SimpleNamespace(__file__="/elsewhere/agents/one-shot/src/__init__.py")
        with patch.dict(sys.modules, {"src": other}):
            with self.assertRaisesRegex(RuntimeError, "already imported"):
                load_full_agent()


if __name__ == "__main__":
    unittest.main()
