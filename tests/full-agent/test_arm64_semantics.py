"""arm64-x86-semantics: tests fail on arm64 only and nothing fails to compile
(tasmanian, xsimd, mlmmj in the public cases). The recipe adds three compiler
flags for arm64; the controller applies it as a starting point and the model
goes on as before. No model, network or build.

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

from copy import deepcopy
import itertools
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))

from src import loop  # noqa: E402
from src.contracts import AgentConfig, Budget, Diagnostic  # noqa: E402
from src.loop import run_loop  # noqa: E402
from src.recipes import arm64_semantics, registry  # noqa: E402
from src.types import Edit  # noqa: E402

RULES = """#!/usr/bin/make -f
export DEB_BUILD_MAINT_OPTIONS = hardening=+all

%:
\tdh $@
"""
LOG = [
    "dpkg-buildpackage: info: host architecture arm64",
    "gcc -DHAVE_CONFIG_H -I. -g -O2 -fstack-protector-strong -c -o gethdrline.o gethdrline.c",
    "   dh_auto_test -a",
    "tests/mlmmj-receive:basics  ->  broken: Test case body timed out  [300.013s]",
    "172/196 passed (24 failed)",
    "make[1]: *** [Makefile:620: check-recursive] Error 1",
    'dh_auto_test: error: make -j8 check "TESTSUITEFLAGS=-j8 --verbose" VERBOSE=1 returned exit code 2',
    "make: *** [debian/rules:14: binary-arch] Error 25",
]


class Worktree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write(RULES)

    def write(self, text):
        path = self.root / "input/debian/rules"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def rules(self):
        return (self.root / "input/debian/rules").read_text()


class Recipe(Worktree):
    def test_a_test_step_that_fails_on_arm64(self):
        match = arm64_semantics.recognise(LOG, self.root, "arm64")
        self.assertIn("dh_auto_test: error", match.evidence[0])
        edits, note = registry.run(arm64_semantics.ID, self.root, LOG, "arm64", {})
        text = self.rules().replace(edits[0].old_text, edits[0].new_text)
        self.assertIn("ifeq ($(DEB_HOST_ARCH),arm64)\n"
                      "export DEB_CFLAGS_MAINT_APPEND += -fsigned-char -ffp-contract=off -fno-strict-aliasing\n"
                      "export DEB_CXXFLAGS_MAINT_APPEND += -fsigned-char -ffp-contract=off -fno-strict-aliasing\n"
                      "endif\n", text)
        self.assertLess(text.index("hardening=+all"), text.index("-fsigned-char"))
        self.assertIn("endif\n\n%:\n\tdh $@\n", text)             # just before the first rule

    def test_an_assignment_for_another_architecture_does_not_stop_it(self):
        # xsimd: the first live run refused here and the case went without the flags
        self.write("#!/usr/bin/make -f\nexport LC_ALL=C.UTF-8\n\nBUILD_OPTIONS += -DX=ON\n\n"
                   "ifeq ($(DEB_HOST_ARCH),armhf)\n  export DEB_CXXFLAGS_MAINT_APPEND = -mfloat-abi=hard\nendif\n\n"
                   "include /usr/share/dpkg/default.mk\n\n%:\n\tdh $@ --with sphinxdoc\n\n"
                   "override_dh_auto_configure:\n\tdh_auto_configure -- $(BUILD_OPTIONS)\n")
        edits, _ = registry.run(arm64_semantics.ID, self.root, LOG, "arm64", {})
        text = self.rules().replace(edits[0].old_text, edits[0].new_text)
        self.assertLess(text.index("-mfloat-abi=hard"), text.index("-fsigned-char"))
        self.assertLess(text.index("include /usr/share/dpkg/default.mk"), text.index("-fsigned-char"))
        self.assertIn("endif\n\n%:\n\tdh $@ --with sphinxdoc\n", text)

    def test_not_on_amd64_and_not_for_a_compile_or_link_error(self):
        amd = ["dpkg-buildpackage: info: host architecture amd64"] + LOG[1:]
        self.assertIsNone(arm64_semantics.recognise(amd, self.root, "amd64"))
        broken = LOG[:3] + ["test_a.c:12:5: error: unknown type name 'u64'"] + LOG[3:]
        self.assertIsNone(arm64_semantics.recognise(broken, self.root, "arm64"))
        build = [line for line in LOG if "dh_auto_test" not in line]
        self.assertIsNone(arm64_semantics.recognise(build, self.root, "arm64"))

    def test_not_when_nothing_c_is_compiled(self):
        go = [LOG[0], "go test -vet=off -v -p 4 ./...", "--- FAIL: TestX (0.01s)", LOG[-2], LOG[-1]]
        self.assertIsNone(arm64_semantics.recognise(go, self.root, "arm64"))

    def test_not_twice_and_refused_when_a_later_assignment_drops_it(self):
        edits, _ = registry.run(arm64_semantics.ID, self.root, LOG, "arm64", {})
        self.write(self.rules().replace(edits[0].old_text, edits[0].new_text))
        self.assertIsNone(arm64_semantics.recognise(LOG, self.root, "arm64"))
        self.write(RULES.replace("%:", "export DEB_CFLAGS_MAINT_APPEND = -Wall\n\n%:"))
        match = arm64_semantics.recognise(LOG, self.root, "arm64")
        try:
            edits = arm64_semantics.expand(self.root, match, {})
        except registry.Refusal:
            return
        text = self.rules().replace(edits[0].old_text, edits[0].new_text)
        self.assertLess(text.index("= -Wall"), text.index("-fsigned-char"))
        self.write(RULES + "\nexport DEB_CFLAGS_MAINT_APPEND = -Wall\n")       # after the rules: would drop it
        with self.assertRaises(registry.Refusal):
            arm64_semantics.expand(self.root, arm64_semantics.recognise(LOG, self.root, "arm64"), {})


class Baseline(Worktree):
    """The controller adds it after the model has finished, when no other
    recipe matches. The model never sees it and keeps all its attempts."""

    STOP = SimpleNamespace(action="stop", reason="nothing else to change", edits=[], tool_requests=[])

    def run_script(self, proposals, attempts=3):
        self.requests = []
        log = self.root / "build.log"
        log.write_text("\n".join(LOG) + "\n")
        proposals = itertools.chain(proposals, itertools.repeat(self.STOP))

        def next_turn(request):
            self.requests.append(deepcopy(request))
            return next(proposals), {"input_tokens": 1, "output_tokens": 1}
        context = SimpleNamespace(worktree=self.root, case_id="launchpad-questing-amd64-arm64-mlmmj-1",
                                  task_metadata={"target_arch": "arm64"}, log_tail="")
        diagnostic = Diagnostic(LOG[-2], "test", None, None, [], None, 0, "")
        return run_loop(context, next_turn=next_turn, config=AgentConfig(use_build_feedback=False),
                        initial_diagnostics=[diagnostic], log_path=log, max_turns=12,
                        budget=Budget(max_attempts=attempts))

    @staticmethod
    def edit(n):
        old = "\tdh $@\n" if n == 0 else f"--with a{n - 1}\n"
        new = "\tdh $@ --with a0\n" if n == 0 else f"--with a{n}\n"
        return SimpleNamespace(action="edit", hypothesis=f"try {n}", evidence=[], reason="", tool_requests=[],
                               edits=[Edit("input/debian/rules", old, new)])

    def test_the_model_is_asked_first_and_sees_nothing_of_it(self):
        # libgovirt: with the flags applied first the model stopped without the repair it found in rc.5
        result = self.run_script([self.STOP, self.STOP])
        first = self.requests[0]
        self.assertFalse(first.controller_note and "arm64-x86-semantics" in first.controller_note)
        self.assertEqual(first.kept_attempts, [])
        self.assertNotIn("-ffp-contract", str(first.own_edits))
        self.assertIn("-ffp-contract=off", self.rules())            # there at the end
        self.assertEqual(result.termination_reason, "model_stop")
        self.assertIn("baseline `arm64-x86-semantics` added", result.message)
        self.assertEqual(len(result.attempts), 1)
        self.assertTrue(result.attempts[0].hypothesis.startswith("controller:"))

    def test_it_goes_on_top_of_the_models_own_edits_and_costs_no_attempt(self):
        result = self.run_script([self.edit(0), self.edit(1), self.edit(2), self.edit(3)], attempts=3)
        self.assertEqual(result.termination_reason, "attempt_budget")
        self.assertEqual([a.hypothesis.split(":")[0] for a in result.attempts],
                         ["try 0", "try 1", "try 2", "controller"])
        text = self.rules()
        self.assertIn("--with a2", text)
        self.assertIn("-fsigned-char", text)

    def test_not_applied_when_switched_off(self):
        with patch.object(loop, "BASELINE_IDS", frozenset()):
            self.run_script([self.STOP])
        self.assertNotIn("-ffp-contract=off", self.rules())
        with patch.object(loop, "AUTO_APPLY_RECIPES", False):
            self.run_script([self.STOP])
        self.assertNotIn("-ffp-contract=off", self.rules())

if __name__ == "__main__":
    unittest.main()
