"""Part E: the Development Validation bundle, checked against the organizers' layout.

The layout rules asserted here are the ones on the Development Validation page
(inspected 13 October 2026): a root manifest.json with only schema_version,
suite_id, optional client and cases; cases/<case_id>/worktree/input/ per case;
no extra top-level folder; no case manifest, config, dependencies, logs or
repair.diff. The run folders are built for the test, not recorded results.

Run from the repository root:  python3 -m unittest discover -s tests/experiments
"""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments import validation_bundle as vb  # noqa: E402
from harness.record import append_jsonl  # noqa: E402


class Bundles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runs_dir = self.root / "full-history-on"

    def run_folder(self, run_id, case_id, repaired, *, package=True, runs_dir=None):
        """A run as harness.run_case leaves it: a row, and the rebuilt package it built."""
        runs_dir = runs_dir or self.runs_dir
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True)
        append_jsonl(runs_dir / "runs.jsonl", {
            "run_id": run_id, "case_id": case_id, "repaired": repaired, "fix_type": "source",
            "agent_version": "0.1.0+aaaaaaaaaaaa", "model": "m",
            "result": {"status": "succeeded" if repaired else "failed"}})
        (run_dir / "repair.diff").write_text("diff")
        (run_dir / "harness.log").write_text("log")
        if package:
            folder = run_dir / "case" / "input"
            folder.mkdir(parents=True)
            archives = {"pkg_1.0.orig.tar.gz": b"upstream " + run_id.encode(),
                        "pkg_1.0-1.debian.tar.xz": b"debian " + run_id.encode()}
            lines = ["Format: 3.0 (quilt)", "Source: pkg", "Checksums-Sha1:"]
            lines += [f" {'0' * 40} {len(data)} {name}" for name, data in archives.items()]
            lines.append("Checksums-Sha256:")
            for name, data in archives.items():
                (folder / name).write_bytes(data)
                lines.append(f" {hashlib.sha256(data).hexdigest()} {len(data)} {name}")
            lines += ["Files:"] + [f" {'0' * 32} {len(d)} {n}" for n, d in archives.items()]
            (folder / "pkg_1.0-1.dsc").write_text("\n".join(lines) + "\n")
            # Things a run folder also holds, which must never be uploaded.
            (run_dir / "case" / "manifest.json").write_text("{}")
            (run_dir / "case" / "config").mkdir()
            (run_dir / "case" / "config" / "buildconfig").write_text("x")
        return run_dir

    def build(self, run_ids, **kwargs):
        output = self.root / "upload" / "development-validation.zip"
        sheet = vb.build_bundle(vb.find_runs([self.runs_dir]), run_ids, output, **kwargs)
        return output, sheet


class Layout(Bundles):
    def test_the_zip_is_exactly_the_documented_layout(self):
        self.run_folder("on--case-a--r1", "case-a", True)
        self.run_folder("on--case-b--r2", "case-b", False)
        output, _ = self.build(["on--case-a--r1", "on--case-b--r2"])
        with zipfile.ZipFile(output) as bundle:
            names = sorted(bundle.namelist())
            manifest = json.loads(bundle.read("manifest.json"))
            dsc = bundle.read("cases/case-a/worktree/input/pkg_1.0-1.dsc").decode()
            orig = bundle.read("cases/case-b/worktree/input/pkg_1.0.orig.tar.gz")
        self.assertEqual(names, sorted(
            ["manifest.json"] + [f"cases/{case}/worktree/input/{name}" for case in ("case-a", "case-b")
                                 for name in ("pkg_1.0-1.dsc", "pkg_1.0.orig.tar.gz",
                                              "pkg_1.0-1.debian.tar.xz")]))
        self.assertEqual(manifest, {
            "schema_version": "0.1", "suite_id": "development-recommended-v1",
            "cases": [{"case_id": "case-a", "worktree": "cases/case-a/worktree"},
                      {"case_id": "case-b", "worktree": "cases/case-b/worktree"}]})
        self.assertIn("Checksums-Sha256:", dsc)
        self.assertEqual(orig, b"upstream on--case-b--r2")  # that run's package, not another's

    def test_optional_manifest_fields(self):
        self.run_folder("r1", "case-a", True)
        output, sheet = self.build(["r1"], suite_id="another-suite", client="ec528")
        with zipfile.ZipFile(output) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
        self.assertEqual(list(manifest), ["schema_version", "suite_id", "client", "cases"])
        self.assertEqual((manifest["suite_id"], sheet["suite_id"]), ("another-suite",) * 2)

    def test_the_check_sheet_holds_local_verdicts_and_empty_official_ones(self):
        self.run_folder("r1", "case-a", True)
        self.run_folder("r2", "case-b", False)
        output, sheet = self.build(["r1", "r2"])
        self.assertEqual(sheet["bundle_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual([(c["case_id"], c["run_id"], c["local_repaired"], c["local_status"])
                          for c in sheet["checks"]],
                         [("case-a", "r1", True, "succeeded"), ("case-b", "r2", False, "failed")])
        for check in sheet["checks"]:
            self.assertIsNone(check["official_repaired"])
            self.assertIsNone(check["official_status"])


class Refused(Bundles):
    def refused(self, run_ids):
        with self.assertRaises(vb.BundleError) as caught:
            self.build(run_ids)
        return str(caught.exception)

    def test_two_runs_of_one_case(self):
        self.run_folder("r1", "case-a", True)
        self.run_folder("r2", "case-a", False)
        self.assertIn("one worktree per case", self.refused(["r1", "r2"]))

    def test_a_run_that_never_built(self):
        self.run_folder("r1", "case-a", False, package=False)
        self.assertIn("nothing to validate", self.refused(["r1"]))

    def test_unknown_run_and_empty_selection(self):
        self.run_folder("r1", "case-a", True)
        self.assertIn("not recorded", self.refused(["r9"]))
        self.assertIn("no run selected", self.refused([]))

    def test_a_package_that_does_not_match_its_dsc(self):
        folder = self.run_folder("r1", "case-a", True) / "case" / "input"
        (folder / "pkg_1.0.orig.tar.gz").write_bytes(b"upstream r?")  # same size, other bytes
        self.assertIn("does not match the checksum", self.refused(["r1"]))
        (folder / "pkg_1.0.orig.tar.gz").unlink()
        self.assertIn("is not a file", self.refused(["r1"]))

    def test_a_dsc_without_checksums_or_a_second_dsc(self):
        folder = self.run_folder("r1", "case-a", True) / "case" / "input"
        (folder / "other_1.0-1.dsc").write_text("Format: 1.0\n")
        self.assertIn("expected one .dsc", self.refused(["r1"]))
        (folder / "other_1.0-1.dsc").unlink()
        (folder / "pkg_1.0-1.dsc").write_text("Format: 3.0 (quilt)\nFiles:\n abc 1 x\n")
        self.assertIn("lists no Checksums-Sha256", self.refused(["r1"]))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_symlinked_archives(self):
        folder = self.run_folder("r1", "case-a", True) / "case" / "input"
        target = self.root / "elsewhere.tar.gz"
        target.write_bytes((folder / "pkg_1.0.orig.tar.gz").read_bytes())
        (folder / "pkg_1.0.orig.tar.gz").unlink()
        (folder / "pkg_1.0.orig.tar.gz").symlink_to(target)
        self.assertIn("is not a file", self.refused(["r1"]))

    def test_the_same_run_id_in_two_folders(self):
        self.run_folder("r1", "case-a", True)
        other = self.root / "full-history-off"
        self.run_folder("r1", "case-b", True, runs_dir=other)
        with self.assertRaises(vb.BundleError):
            vb.find_runs([self.runs_dir, other])
        with self.assertRaises(vb.BundleError):
            vb.find_runs([self.root / "nowhere"])


class Sampling(Bundles):
    def setUp(self):
        super().setUp()
        for n in range(1, 5):
            self.run_folder(f"good-{n}", f"case-{n}", True)
            self.run_folder(f"bad-{n}", f"case-{n}", False)
        self.run_folder("unbuilt", "case-9", False, package=False)
        self.runs = vb.find_runs([self.runs_dir])

    def test_one_run_per_case_with_both_verdicts(self):
        chosen = vb.sample(self.runs, 4)
        self.assertEqual(len(chosen), 4)
        self.assertEqual(len({self.runs[r][0]["case_id"] for r in chosen}), 4)
        self.assertEqual(sorted(self.runs[r][0]["repaired"] for r in chosen),
                         [False, False, True, True])
        self.assertNotIn("unbuilt", chosen)

    def test_reproducible_and_bounded_by_what_exists(self):
        self.assertEqual(vb.sample(self.runs, 3), vb.sample(self.runs, 3))
        self.assertNotEqual({tuple(vb.sample(self.runs, 4, seed)) for seed in range(8)},
                            {tuple(vb.sample(self.runs, 4))})
        self.assertEqual(len(vb.sample(self.runs, 50)), 4)  # four cases have a package


class CommandLine(Bundles):
    def main(self, *argv):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            try:
                code = vb.main([str(a) for a in argv])
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    def test_build_then_compare(self):
        self.run_folder("r1", "case-a", True)
        self.run_folder("r2", "case-b", False)
        self.run_folder("r3", "case-c", True)
        output = self.root / "check.zip"
        code, out, _ = self.main(self.runs_dir, "--sample", 3, "--output", output)
        self.assertEqual(code, 0)
        self.assertIn("3 case(s), suite development-recommended-v1", out)
        sheet_path = self.root / "check.checks.json"
        sheet = json.loads(sheet_path.read_text())
        self.assertEqual(sheet["sample"], {"requested": 3, "seed": vb.SEED})

        code, out, _ = self.main("--compare", sheet_path)
        self.assertIn("0 of 0 checked local verdicts agree with Development Validation; "
                      "3 not yet checked.", out)
        by_run = {c["run_id"]: c for c in sheet["checks"]}
        by_run["r1"].update(official_repaired=True, official_status="succeeded")
        by_run["r2"].update(official_repaired=True, official_status="succeeded")
        sheet_path.write_text(json.dumps(sheet))
        code, out, _ = self.main("--compare", sheet_path)
        self.assertIn("1 of 2 checked local verdicts agree with Development Validation; "
                      "1 not yet checked.", out)
        self.assertIn("DISAGREES r2: local not repaired, official repaired (succeeded)", out)

    def test_errors_are_reported_without_a_traceback(self):
        self.run_folder("r1", "case-a", False, package=False)
        code, _, err = self.main(self.runs_dir, "--run-id", "r1", "--output", self.root / "x.zip")
        self.assertEqual(code, 1)
        self.assertIn("error: r1: no rebuilt source package", err)
        self.assertFalse((self.root / "x.zip").exists())
        self.assertEqual(self.main(self.runs_dir)[0], 2)
        self.assertEqual(self.main("--sample", 2)[0], 2)


if __name__ == "__main__":
    unittest.main()
