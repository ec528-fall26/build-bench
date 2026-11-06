"""harness/run_public.py: public cases -> agent without builds -> one Worktree Bundle.

dpkg-source is faked and the model is scripted, so this runs anywhere; the real
tools run on the ARM64 host. The bundle is checked by the real
experiments/validation_bundle.py (layout and .dsc checksums).

Run from the repository root:  python3 -m unittest discover -s tests/full-agent
"""

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
sys.path.insert(0, str(ROOT))

from harness import run_public  # noqa: E402
from harness.run_case import AGENTS, CommandFailed, load_agent  # noqa: E402

CASE = "launchpad-noble-amd64-arm64-wlrctl-5183d65fdbbc"
OTHER = "launchpad-plucky-arm64-amd64-libotr-7f09e7c32c80"
HEADER = "#define HAVE_FAST 1\n"
EDIT = ('{"action": "edit", "hypothesis": "x86-only option", "evidence": ["log"], "edits": '
        '[{"path": "input/src/config.h", "old_text": "#define HAVE_FAST 1\\n", '
        '"new_text": "#define HAVE_FAST 0\\n"}]}')
STOP = '{"action": "stop", "reason": "done"}'


def dsc_text(files: dict) -> str:
    lines = ["Format: 3.0 (quilt)", "Source: pkg", "Checksums-Sha256:"]
    for name, data in files.items():
        lines.append(f" {hashlib.sha256(data).hexdigest()} {len(data)} {name}")
    return "\n".join(lines) + "\n"


def write_package(folder: Path, debian: bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    # The signature is listed by the .dsc too (zvbi): a bundle without it is invalid.
    files = {"pkg_1.0.orig.tar.gz": b"orig", "pkg_1.0.orig.tar.gz.asc": b"sig",
             "pkg_1.0-1.debian.tar.xz": debian}
    for name, data in files.items():
        (folder / name).write_bytes(data)
    (folder / "pkg_1.0-1.dsc").write_text(dsc_text(files))


class FakeDpkg:
    def __init__(self, fail_on=None):
        self.fail_on, self.calls = fail_on, []

    def __call__(self, cmd, cwd, log, timeout):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if self.fail_on and self.fail_on in cmd:
            raise CommandFailed(f"{cmd[0]} exited 2")
        if cmd[:2] == ["dpkg-source", "-x"]:
            src = Path(cwd) / cmd[3] / "src"
            src.mkdir(parents=True)
            (src / "config.h").write_text(HEADER)
            (Path(cwd) / "pkg_1.0.orig.tar.gz").write_bytes(b"orig")
        elif cmd[:2] == ["dpkg-source", "--auto-commit"]:
            write_package(Path(cwd).parent, b"rebuilt debian")


class Scripted:
    name = "openai-compatible:scripted"
    served_model = "scripted"
    api_key = "k"

    def __init__(self, *replies):
        self.replies = list(replies)

    def complete(self, messages):
        return self.replies.pop(0), "stop", {"input_tokens": 10, "output_tokens": 2}


class Public(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cases_root = self.root / "buildbench-development-cases-v0.1"
        rows = []
        for case_id, direction in ((CASE, "x86_64-to-aarch64"), (OTHER, "aarch64-to-x86_64")):
            folder = self.cases_root / "cases" / direction / case_id
            write_package(folder / "source/input", b"original debian")
            (folder / "logs").mkdir(parents=True)
            (folder / "logs/target-failure.log").write_text(
                "src/fast.c:3:10: fatal error: immintrin.h: No such file or directory\n")
            (folder / "case.json").write_text(json.dumps({
                "case_id": case_id, "package": {"name": "pkg"},
                "platform": {"target_arch": direction.split("-to-")[1]},
                "paths": {"source_input": "source/input",
                          "target_failure_log": "logs/target-failure.log"}}))
            rel = f"cases/{direction}/{case_id}"
            rows.append(f"{case_id},{direction},pkg,1.0-1,ubuntu,noble,a,b,{rel}/source/input,"
                        f"{rel}/logs/target-failure.log,{rel}/case.json,x")
        (self.cases_root / "manifest.csv").write_text(
            "case_id,direction,package_name,version,distribution,series,source_arch,target_arch,"
            "source_input,target_failure_log,case_metadata,checksums\n" + "\n".join(rows) + "\n")
        self.cases = run_public.public_cases(self.cases_root)
        self.agent = load_agent(AGENTS / "full-agent")
        self.agent.client_completion = lambda model: model.complete
        self.out = self.root / "runs"

    def case(self, case_id):
        return next(c for c in self.cases if c["case_id"] == case_id)

    def test_selection(self):
        self.assertEqual([c["case_id"] for c in run_public.select(self.cases, small=True)],
                         [CASE, OTHER])  # both are on the website's small list
        self.assertEqual([c["case_id"] for c in run_public.select(self.cases, ids=["libotr-7f09e7c32c80"])],
                         [OTHER])
        self.assertEqual(len(run_public.select(self.cases, sample=1, seed=3)), 1)
        with self.assertRaises(SystemExit):
            run_public.select(self.cases, ids=["nope"])

    def test_the_small_list_has_the_websites_111_cases(self):
        names = run_public._hash_names(run_public.SMALL_CASES)
        self.assertEqual((len(names), len(set(names))), (111, 111))
        self.assertTrue(all(run_public._matches("x-" + n, names) for n in run_public.TUNED_CASES
                            if n not in ("libunwind-f18b55e03a91", "rust-subversion-b3a4b8c35bb2")))

    def test_an_edit_is_rebuilt_and_bundled_and_no_edit_bundles_the_original(self):
        dpkg = FakeDpkg()
        edited = run_public.run_one(self.case(CASE), self.out, "t", self.agent,
                                    Scripted(EDIT, STOP), runner=dpkg)
        unchanged = run_public.run_one(self.case(OTHER), self.out, "t", self.agent,
                                       Scripted(STOP), runner=dpkg)
        self.assertEqual((edited["termination_reason"], edited["bundled"], edited["edits_applied"]),
                         ("submitted", "rebuilt", 1))
        self.assertIn("+#define HAVE_FAST 0", (self.out / CASE / "repair.diff").read_text())
        self.assertFalse((self.out / CASE / "work").exists())
        self.assertEqual((unchanged["termination_reason"], unchanged["bundled"]),
                         ("no_edit_applied", "original"))
        for row in (edited, unchanged):
            run_public.append_jsonl(self.out / "runs.jsonl", row)

        output = self.root / "devval.zip"
        with redirect_stdout(io.StringIO()) as printed:
            code = run_public.cmd_bundle(SimpleNamespace(out=self.out, output=output, fill_from=None))
        self.assertEqual(code, 0, printed.getvalue())
        with zipfile.ZipFile(output) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            self.assertEqual(sorted(manifest), ["cases", "schema_version", "suite_id"])
            self.assertEqual(sorted(c["case_id"] for c in manifest["cases"]), [CASE, OTHER])
            self.assertEqual(bundle.read(f"cases/{CASE}/worktree/input/pkg_1.0-1.debian.tar.xz"),
                             b"rebuilt debian")
            self.assertEqual(bundle.read(f"cases/{OTHER}/worktree/input/pkg_1.0-1.debian.tar.xz"),
                             b"original debian")
        self.assertIn(OTHER, printed.getvalue())  # not all 111: the cases are listed

    def test_stage_selection_and_filling_from_a_baseline(self):
        stages = {CASE: "test", OTHER: "compile"}
        chosen = run_public.select(self.cases, stage="test",
                                   classify=lambda log: stages[log.parent.parent.name])
        self.assertEqual([c["case_id"] for c in chosen], [CASE])
        dpkg, base = FakeDpkg(), self.root / "baseline"
        for case_id, out in ((CASE, self.out), (CASE, base), (OTHER, base)):
            row = run_public.run_one(self.case(case_id), out, out.name, self.agent,
                                     Scripted(STOP), runner=dpkg)
            run_public.append_jsonl(out / "runs.jsonl", row)
        output = self.root / "mixed.zip"
        with redirect_stdout(io.StringIO()) as printed:
            run_public.cmd_bundle(SimpleNamespace(out=self.out, output=output, fill_from=base))
        self.assertIn("1 cases filled in", printed.getvalue())
        sheet = json.loads(output.with_suffix(".checks.json").read_text())
        self.assertEqual(sorted((c["case_id"], c["run_id"].split("-")[0]) for c in sheet["checks"]),
                         sorted([(OTHER, "baseline"), (CASE, "runs")]))

    def test_failures_still_bundle_the_original(self):
        row = run_public.run_one(self.case(CASE), self.out, "t", self.agent, Scripted(EDIT, STOP),
                                 runner=FakeDpkg(fail_on="--auto-commit"))
        self.assertEqual((row["termination_reason"], row["bundled"]), ("repack_failed", "original"))
        row = run_public.run_one(self.case(CASE), self.out, "t", self.agent, Scripted(),
                                 runner=FakeDpkg(fail_on="-x"))
        self.assertEqual((row["termination_reason"], row["bundled"]), ("unpack_failed", "original"))
        self.assertTrue((self.out / CASE / "case/input/pkg_1.0-1.dsc").is_file())


if __name__ == "__main__":
    unittest.main()
