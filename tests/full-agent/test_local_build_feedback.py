"""Part D with the real Part A and Part C: harness/build_feedback.py end to end.

dpkg-source and run.sh are still faked (harness/tests/test_build_feedback.py's
FakeValidator), so this runs anywhere; the real validator is exercised on the
ARM64 host. The log uses the validator's real "[   93s] " line prefix.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"))
sys.path.insert(0, str(ROOT))

from harness.build_feedback import LocalBuildFeedback, load_full_agent  # noqa: E402
from harness.tests.test_build_feedback import DSC, FIXTURES, ORIG, FakeValidator  # noqa: E402
from src.contracts import BuildFeedback, ModelTurn  # noqa: E402
from src.loop import run_loop  # noqa: E402
from src.types import CaseContext, Edit  # noqa: E402


LOG = ("[   80s]    dh_auto_install\n"
       + "".join(f"[   9{n}s] gcc -c file{n}.c\n" for n in range(5))
       + "[   93s] crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: "
         "No such file or directory\n"
       + "[   93s]    18 | #include <xmmintrin.h>\n"
       + "[   93s] make[2]: *** [crc32c_portable.o] Error 1\n")
CONFIG = "input/src/crc32c/crc32c_config.h"


class LocalBuildWithRealParts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.bundle = root / "bundle"
        (self.bundle / "case/input").mkdir(parents=True)
        (self.bundle / "run.sh").write_text("")
        shutil.copy(FIXTURES / "rcran-manifest.json", self.bundle / "case/manifest.json")
        (self.bundle / "case/input" / DSC).write_text("original dsc")
        self.worktree = root / "work"
        source = self.worktree / "input/src/crc32c"
        source.mkdir(parents=True)
        shutil.copy(FIXTURES / "crc32c_config.h", source)
        (source / "crc32c_prefetch.h").write_text("#include <xmmintrin.h>\n")
        (self.worktree / ORIG).write_text("upstream")
        self.feedback = LocalBuildFeedback(self.bundle, root / "builds", load_full_agent(),
                                           runner=FakeValidator(log=LOG))

    def test_satisfies_the_agents_contract(self):
        self.assertIsInstance(self.feedback, BuildFeedback)

    def test_new_error_is_parsed_and_resolved(self):
        result = self.feedback.request_build(self.worktree)
        first = result.diagnostics[0]
        self.assertFalse(result.succeeded)
        self.assertEqual((result.stage_reached, first.stage), ("install", "install"))
        self.assertEqual(first.resolved, ["input/src/crc32c/crc32c_prefetch.h"])
        self.assertEqual(first.source_line, "#include <xmmintrin.h>")

    def test_loop_revises_after_a_failed_build(self):
        wrong = Edit(CONFIG, "#define HAVE_MM_PREFETCH 1", "#define HAVE_MM_PREFETCH 1  // tried")
        fix = Edit(CONFIG, "HAVE_MM_PREFETCH 1", "HAVE_MM_PREFETCH 0")
        turns, requests = [wrong, fix], []

        def next_turn(request):
            requests.append(request)
            edit = turns.pop(0)
            return (ModelTurn("edit", [], f"try {len(requests)}", [], [edit], ""),
                    {"input_tokens": 10, "output_tokens": 5})

        context = CaseContext("rcran", self.worktree, LOG, {"target_arch": "aarch64"})
        result = run_loop(context, next_turn=next_turn, build_feedback=self.feedback)

        self.assertEqual(result.termination_reason, "build_succeeded")
        self.assertEqual([a.build.succeeded for a in result.attempts], [False, True])
        # The second turn saw the first build's parsed error.
        seen = requests[1].current_build or requests[1].history[-1].build
        self.assertEqual(seen.diagnostics[0].resolved, ["input/src/crc32c/crc32c_prefetch.h"])
        self.assertIn("#define HAVE_MM_PREFETCH 0", (self.worktree / CONFIG).read_text())
        self.assertEqual([r["kind"] for r in self.feedback.records],
                         ["build_failed", "succeeded"])


if __name__ == "__main__":
    unittest.main()
