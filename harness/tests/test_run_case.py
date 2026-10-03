"""Orchestration checks for run_case, runnable on macOS.

Parts 1, 2 and 4 run for real. dpkg-source and run.sh are replaced by FakeHost,
which imitates their file effects and exit codes; the real versions are
exercised on the ARM64 host.

Run from the repository root:  python3 -m unittest discover -s harness/tests -t .
"""

from contextlib import redirect_stderr, redirect_stdout
from functools import partial
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import harness.run_case as run_case_module
from harness.run_case import AGENT_DIR, CommandFailed, agent_version, main, run_case, write_diff


FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPLAY = Path(__file__).resolve().parents[1] / "replays" / "rcran-known-fix.json"
DSC = "r-cran-digest_0.6.32-1.dsc"


class FakeHost:
    """Imitates dpkg-source and run.sh well enough to drive run_case."""

    def __init__(self, baseline_passes=False, fail_on=None, build_hangs=False):
        self.baseline_passes = baseline_passes
        self.fail_on = fail_on
        self.build_hangs = build_hangs
        self.calls = []

    def __call__(self, cmd, cwd, log, timeout):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if self.fail_on and self.fail_on in cmd:
            raise CommandFailed(f"{cmd[0]} exited 2")
        if cmd[:2] == ["dpkg-source", "-x"]:
            src = Path(cwd) / cmd[3] / "src" / "crc32c"
            src.mkdir(parents=True)
            shutil.copy(FIXTURES / "crc32c_config.h", src)
            (Path(cwd) / "r-cran-digest_0.6.32.orig.tar.gz").write_text("orig")
        elif cmd[:2] == ["dpkg-source", "--auto-commit"]:
            # Real --auto-commit records the edit as a patch inside the tree.
            patches = Path(cwd) / "debian" / "patches"
            patches.mkdir(parents=True, exist_ok=True)
            (patches / "debian-changes-0.6.32-1").write_text("auto-commit patch")
            (Path(cwd).parent / DSC).write_text("rebuilt dsc")
            (Path(cwd).parent / "r-cran-digest_0.6.32-1.debian.tar.xz").write_text("rebuilt debian")
        elif cmd[0] == "bash":
            self._build(cmd)

    def _build(self, cmd):
        repair = "--input" in cmd
        if repair and self.build_hangs:
            raise CommandFailed("bash timed out after 4200s")
        out = Path(cmd[cmd.index("--output") + 1])
        out.mkdir()
        if repair:
            run_dir = Path(cmd[cmd.index("--input") + 1]).parent
            config = (run_dir / "work/input/src/crc32c/crc32c_config.h").read_text()
            passes = "#define HAVE_MM_PREFETCH 0" in config
        else:
            passes = self.baseline_passes
        fixture = "fix-1" if passes else "baseline-1"
        shutil.copy(FIXTURES / fixture / "build-result.json", out)
        (out / "build.log").write_text(
            "noise\n" * 50
            + "crc32c/crc32c_prefetch.h:18:10: fatal error: xmmintrin.h: No such file or directory\n"
        )
        if not passes:
            raise CommandFailed("bash exited 1")  # run.sh exits nonzero on a failed build


class RunCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.bundle = root / "bundle"
        case = self.bundle / "case"
        (case / "input").mkdir(parents=True)
        (case / "config").mkdir()
        (case / "dependencies" / "ubuntu-mantic-arm64").mkdir(parents=True)
        (self.bundle / "run.sh").write_text("")
        shutil.copy(FIXTURES / "rcran-manifest.json", case / "manifest.json")
        (case / "input" / DSC).write_text("original signed dsc")
        (case / "config" / "dependency-lock.json").write_text("{}")
        (case / "dependencies" / "ubuntu-mantic-arm64" / "libc6.deb").write_text("deb")
        self.runs = root / "runs"

    def run_with(self, host, run_id="r1", replay=REPLAY, **kwargs):
        return run_case(self.bundle, run_id, self.runs, replay, runner=host, **kwargs)

    def replay_file(self, **changes):
        data = json.loads(REPLAY.read_text())
        data.update(changes)
        path = Path(self.temp.name) / "replay.json"
        path.write_text(json.dumps(data))
        return path

    def test_known_fix_is_repaired_end_to_end(self):
        row = self.run_with(FakeHost())
        self.assertIsNone(row["served_model"])  # replays have no server
        self.assertTrue(row["repaired"])
        self.assertEqual(row["termination_reason"], "build_succeeded")
        self.assertEqual((row["edits_applied"], row["edits_proposed"]), (1, 1))
        self.assertEqual(row["baseline"]["status"], "failed")
        self.assertEqual(row["result"]["status"], "succeeded")
        self.assertEqual(row["edit_warnings"], [])

    def test_assembled_case_swaps_only_input(self):
        self.run_with(FakeHost())
        built = self.runs / "r1" / "case"
        self.assertEqual(sorted(p.name for p in (built / "input").iterdir()), [
            "r-cran-digest_0.6.32-1.debian.tar.xz", DSC, "r-cran-digest_0.6.32.orig.tar.gz"])
        self.assertEqual((built / "input" / DSC).read_text(), "rebuilt dsc")
        self.assertEqual((self.bundle / "case" / "input" / DSC).read_text(), "original signed dsc")
        self.assertTrue(os.path.samefile(
            built / "dependencies/ubuntu-mantic-arm64/libc6.deb",
            self.bundle / "case/dependencies/ubuntu-mantic-arm64/libc6.deb"))
        self.assertTrue((built / "manifest.json").is_file())

    def test_repack_uses_auto_commit(self):
        host = FakeHost()
        self.run_with(host)
        self.assertIn(["dpkg-source", "--auto-commit", "-b", "."], host.calls)

    def test_every_run_appends_one_row(self):
        self.run_with(FakeHost(), run_id="a")
        self.run_with(FakeHost(), run_id="b", replay=self.replay_file(case_id="other-case"))
        rows = [json.loads(line) for line in (self.runs / "runs.jsonl").read_text().splitlines()]
        self.assertEqual([(r["run_id"], r["repaired"]) for r in rows], [("a", True), ("b", False)])
        self.assertTrue((self.runs / "a" / "record.json").is_file())

    def test_forbidden_edit_is_rejected_and_recorded(self):
        edits = json.loads(REPLAY.read_text())["edits"] + [
            {"path": "config/dependency-lock.json", "old_text": "{}", "new_text": "[]"}]
        row = self.run_with(FakeHost(), replay=self.replay_file(edits=edits))
        self.assertEqual((row["edits_applied"], row["edits_proposed"]), (1, 2))
        self.assertEqual(len(row["edit_warnings"]), 1)
        self.assertIn("edit_rejected", row["edit_warnings"][0])
        self.assertTrue(row["repaired"])
        self.assertEqual((self.bundle / "case/config/dependency-lock.json").read_text(), "{}")

    def test_no_applied_edit_skips_the_build(self):
        host = FakeHost()
        row = self.run_with(host, replay=self.replay_file(case_id="other-case"))
        self.assertEqual(row["termination_reason"], "no_edit_applied")
        self.assertFalse(any("--auto-commit" in c for c in host.calls))
        self.assertFalse(any("--input" in c for c in host.calls))

    def test_case_that_already_builds_is_not_used(self):
        host = FakeHost(baseline_passes=True)
        row = self.run_with(host)
        self.assertEqual(row["termination_reason"], "case_not_failing")
        self.assertFalse(any(c[0] == "dpkg-source" for c in host.calls))

    def test_supplied_log_skips_the_baseline(self):
        log = Path(self.temp.name) / "build.log"
        log.write_text("fatal error: xmmintrin.h\n")
        host = FakeHost()
        row = self.run_with(host, log=log)
        self.assertIsNone(row["baseline"])
        self.assertEqual(sum(c[0] == "bash" for c in host.calls), 1)
        self.assertTrue(row["repaired"])

    def test_unpack_failure_is_recorded(self):
        row = self.run_with(FakeHost(fail_on="-x"))
        self.assertEqual(row["termination_reason"], "unpack_failed")
        self.assertFalse(row["repaired"])
        self.assertEqual(len((self.runs / "runs.jsonl").read_text().splitlines()), 1)

    def test_build_timeout_is_reported_not_swallowed(self):
        row = self.run_with(FakeHost(build_hangs=True))
        self.assertEqual(row["termination_reason"], "build_error")
        self.assertIn("timed out", row["error"])

    def test_run_id_cannot_be_reused(self):
        self.run_with(FakeHost())
        with self.assertRaises(FileExistsError):
            self.run_with(FakeHost())

    def test_repair_diff_shows_only_the_agents_change(self):
        row = self.run_with(FakeHost())
        diff = Path(row["diff_path"]).read_text()
        self.assertIn("--- a/input/src/crc32c/crc32c_config.h", diff)
        # Same hunk dpkg-source wrote on the ARM64 host: the original define stays
        # as context and the architecture guard is inserted around it.
        self.assertIn("@@ -12,7 +12,11 @@", diff)
        self.assertIn("+#if defined(__x86_64__)", diff)
        self.assertIn(" #define HAVE_MM_PREFETCH 1\n+#else\n+#define HAVE_MM_PREFETCH 0\n+#endif", diff)
        self.assertNotIn("debian/patches", diff)  # taken before --auto-commit
        self.assertFalse((self.runs / "r1" / "pristine").exists())

    def test_run_without_a_change_has_no_diff(self):
        row = self.run_with(FakeHost(), replay=self.replay_file(case_id="other-case"))
        self.assertIsNone(row["diff_path"])
        self.assertFalse((self.runs / "r1" / "repair.diff").exists())
        self.assertFalse((self.runs / "r1" / "pristine").exists())

    def test_agent_version_is_recorded_and_stable(self):
        first = self.run_with(FakeHost(), run_id="a")["agent_version"]
        second = self.run_with(FakeHost(), run_id="b")["agent_version"]
        self.assertRegex(first, r"^0\.1\.0\+[0-9a-f]{12}$")
        self.assertEqual(first, second)

    def cli(self, host, run_id):
        args = ["--bundle", str(self.bundle), "--replay", str(REPLAY),
                "--run-id", run_id, "--runs-dir", str(self.runs)]
        with patch.object(run_case_module, "run_case", partial(run_case, runner=host)), \
                redirect_stdout(io.StringIO()) as out:
            code = main(args)
        return code, out.getvalue()

    def test_cli_exits_zero_on_a_completed_run(self):
        code, out = self.cli(FakeHost(), "ok")
        self.assertEqual(code, 0)
        self.assertIn("REPAIRED", out)

    def test_cli_exits_nonzero_on_infrastructure_failure(self):
        code, out = self.cli(FakeHost(fail_on="-x"), "broken")
        self.assertEqual(code, 1)
        self.assertIn("unpack_failed", out)

    def test_real_part3_client_end_to_end(self):
        # Part 3's real client; only the HTTP endpoint and the build are faked.
        reply = {"model": "openai.gpt-oss-120b-1:0",
                 "choices": [{"message": {"content": "Here is the fix:\n" + REPLAY.read_text()},
                              "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 2000, "completion_tokens": 400}}
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return io.BytesIO(json.dumps(reply).encode())

        client = run_case_module.load_agent(AGENT_DIR).ModelClient("test-key", opener=opener)
        row = run_case(self.bundle, "live", self.runs, model=client, runner=FakeHost())
        self.assertTrue(row["repaired"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(row["model"], "openai-compatible:openai.gpt-oss-120b-1:0")
        self.assertEqual(row["served_model"], "openai.gpt-oss-120b-1:0")
        self.assertEqual(row["usage"], {"input_tokens": 2000, "output_tokens": 400})
        prompt = json.loads(requests[0].data)["messages"][1]["content"]
        log_section = prompt.split("=== End of build log ===")[0]
        self.assertIn("fatal error: xmmintrin.h", log_section)  # the baseline's failure log

    def test_cli_live_passes_part3_client_to_run_case(self):
        calls = []
        fake_run_case = lambda *args, **kwargs: calls.append(kwargs) or {
            "run_id": "x", "repaired": False, "termination_reason": "no_edit_applied",
            "edits_applied": 0, "edits_proposed": 0, "wall_seconds": 0.0, "error": None}
        args = ["--bundle", str(self.bundle), "--live", "--run-id", "x", "--runs-dir", str(self.runs)]
        with patch.dict(os.environ, {"BB_MODEL_API_KEY": "test-key"}), \
                patch.object(run_case_module, "run_case", fake_run_case), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        self.assertIsNone(calls[0]["replay"])
        self.assertEqual(calls[0]["model"].name, "openai-compatible:openai.gpt-oss-120b-1:0")

    def test_cli_live_without_a_key_is_refused(self):
        args = ["--bundle", str(self.bundle), "--live", "--run-id", "x", "--runs-dir", str(self.runs)]
        env = {k: v for k, v in os.environ.items() if k != "BB_MODEL_API_KEY"}
        with patch.dict(os.environ, env, clear=True), redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(SystemExit) as stop:
            main(args)
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("BB_MODEL_API_KEY", err.getvalue())
        self.assertFalse((self.runs / "x").exists())


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_agent_version_changes_when_the_code_changes(self):
        copy = self.root / "agent"
        shutil.copytree(AGENT_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
        before = agent_version(copy)
        with (copy / "src" / "types.py").open("a") as f:
            f.write("# changed\n")
        self.assertNotEqual(before, agent_version(copy))
        self.assertEqual(before.split("+")[0], agent_version(copy).split("+")[0])

    def test_agent_version_ignores_hidden_files(self):
        # A Mac tar copy without COPYFILE_DISABLE left ._edit_applier.py on the
        # ARM64 host and changed the fingerprint of otherwise identical code.
        copy = self.root / "agent"
        shutil.copytree(AGENT_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
        before = agent_version(copy)
        (copy / "src" / "._edit_applier.py").write_bytes(b"\x00\x05\x16\x07 AppleDouble")
        (copy / "src" / ".hidden").mkdir()
        (copy / "src" / ".hidden" / "x.py").write_text("x = 1\n")
        self.assertEqual(agent_version(copy), before)

    def tree(self, name, files):
        root = self.root / name
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        return root

    def test_identical_trees_write_nothing(self):
        a = self.tree("a", {"x.c": "int x;\n"})
        b = self.tree("b", {"x.c": "int x;\n"})
        self.assertFalse(write_diff(a, b, self.root / "d.diff"))
        self.assertFalse((self.root / "d.diff").exists())

    def test_missing_final_newline_is_marked(self):
        a = self.tree("a", {"x.c": "a\n"})
        b = self.tree("b", {"x.c": "a\nb"})
        self.assertTrue(write_diff(a, b, self.root / "d.diff"))
        self.assertIn("+b\n\\ No newline at end of file\n", (self.root / "d.diff").read_text())

    def test_added_file_diffs_against_dev_null(self):
        a = self.tree("a", {"x.c": "a\n"})
        b = self.tree("b", {"x.c": "a\n", "new.h": "#pragma once\n"})
        write_diff(a, b, self.root / "d.diff")
        diff = (self.root / "d.diff").read_text()
        self.assertIn("--- /dev/null\n+++ b/input/new.h", diff)


if __name__ == "__main__":
    unittest.main()
