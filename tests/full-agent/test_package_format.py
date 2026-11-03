"""Part A: Debian / RPM / unknown package format; no model, network or build."""
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
from src.case import detect_package_format, load_case
from src import main as agent_main

HELLO = Path(__file__).resolve().parent / "fixtures" / "hello-rpm"
RCRAN_MANIFEST = ROOT / "docs/results/part-c-rcran-694ehfba/case-manifest.json"


class Workspace(unittest.TestCase):
    """A scratch worktree; files are created relative to work/repo/."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tree = self.root / "work" / "repo"
        (self.tree / "input").mkdir(parents=True)
        self.task = self.root / "input" / "task.json"
        self.task.parent.mkdir()
        self.task.write_text(json.dumps({"case_id": "c"}))

    def put(self, relative, text="x\n"):
        path = self.tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def detect(self, task=None, manifest=None):
        return detect_package_format(task or {}, manifest or {}, self.tree)


class HelloRegression(unittest.TestCase):
    """The RPM `hello` case must never be reported as Debian."""

    def test_hello_is_rpm_from_its_manifest(self):
        case = load_case(HELLO / "input/task.json", worktree=HELLO / "work/repo",
                         log_path=HELLO / "input/initial-build.log")
        self.assertEqual((case.package_format.name, case.package_format.basis), ("rpm", "metadata"))
        joined = " ".join(case.package_format.evidence)
        self.assertIn("'rpm'", joined)
        self.assertIn("buildbench-hello.spec", joined)
        # The worktree manifest also fills fields task.json lacks; task values still win.
        self.assertEqual(case.case_id, "hello")
        self.assertEqual(case.target_arch, "x86_64")

    def test_hello_is_rpm_from_its_files_without_a_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "hello"
            shutil.copytree(HELLO, copy)
            (copy / "work/repo/manifest.json").unlink()
            case = load_case(copy / "input/task.json", worktree=copy / "work/repo")
            self.assertEqual((case.package_format.name, case.package_format.basis), ("rpm", "files"))
            self.assertIn("input/buildbench-hello.spec", case.package_format.evidence[0])

    def test_fixture_is_unchanged_by_loading(self):
        files = sorted(p for p in HELLO.rglob("*") if p.is_file())
        before = {p: p.read_bytes() for p in files}
        load_case(HELLO / "input/task.json", worktree=HELLO / "work/repo")
        self.assertEqual(before, {p: p.read_bytes() for p in files})

    def test_main_records_the_format_in_agent_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shutil.copytree(HELLO / "input", workspace / "input")
            shutil.copytree(HELLO / "work", workspace / "work")
            with patch.dict(os.environ, {"BB_WORKSPACE": str(workspace), "BB_MODEL_API_KEY": ""}), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                for name in [k for k in os.environ if k.startswith("BB_AGENT_")]:
                    os.environ.pop(name)
                code = agent_main.main()
            result = json.loads((workspace / "output/agent-result.json").read_text())
            self.assertEqual(code, 0)
            self.assertEqual(result["package_format"]["name"], "rpm")
            self.assertEqual(result["package_format"]["basis"], "metadata")


class Metadata(Workspace):
    def test_real_debian_manifest(self):
        case = load_case(self.task, manifest_path=RCRAN_MANIFEST, worktree=self.tree)
        fmt = case.package_format
        self.assertEqual((fmt.name, fmt.basis), ("debian", "metadata"))
        self.assertEqual(len(fmt.evidence), 4)   # package_type, .dsc recipe, backend, artifacts

    def test_task_json_alone_is_enough(self):
        fmt = self.detect(task={"package_type": "deb"})
        self.assertEqual((fmt.name, fmt.basis), ("debian", "metadata"))

    def test_metadata_wins_over_files(self):
        self.put("input/foo.spec")
        fmt = self.detect(manifest={"package_type": "deb"})
        self.assertEqual((fmt.name, fmt.basis), ("debian", "metadata"))

    def test_disagreeing_metadata_is_unknown_with_both_reasons(self):
        fmt = self.detect(task={"package_type": "rpm"},
                          manifest={"build": {"recipe": "input/foo_1.0-1.dsc"}})
        self.assertEqual((fmt.name, fmt.basis), ("unknown", "metadata"))
        joined = " ".join(fmt.evidence)
        self.assertIn("'rpm'", joined)
        self.assertIn("foo_1.0-1.dsc", joined)
        self.assertIn("disagrees", joined)

    def test_unrecognized_type_is_noted_and_files_decide(self):
        self.put("input/debian/control")
        fmt = self.detect(manifest={"package_type": "apk"})
        self.assertEqual((fmt.name, fmt.basis), ("debian", "files"))
        self.assertIn("not a recognized format", fmt.evidence[0])

    def test_unreadable_worktree_manifest_is_ignored_not_fatal(self):
        self.put("manifest.json", "{not json")
        self.put("input/debian/control")
        case = load_case(self.task, worktree=self.tree)
        self.assertEqual(case.package_format.name, "debian")
        self.assertIn("manifest.json ignored", case.package_format.evidence[0])
        self.assertEqual(case.manifest_metadata, {})

    def test_explicit_bad_manifest_still_raises(self):
        bad = self.root / "bad.json"
        bad.write_text("[]")
        with self.assertRaises(ValueError):
            load_case(self.task, manifest_path=bad, worktree=self.tree)


class Files(Workspace):
    def test_dsc_means_debian(self):
        self.put("input/foo_1.0-1.dsc")
        self.assertEqual(self.detect().name, "debian")

    def test_debian_control_means_debian(self):
        self.put("input/debian/control")
        fmt = self.detect()
        self.assertEqual((fmt.name, fmt.basis), ("debian", "files"))

    def test_upstream_spec_beside_a_dsc_is_not_rpm(self):
        # Shaped like libbpp-core: a Debian package whose upstream ships bpp-core.spec.
        self.put("input/libbpp-core_2.4.1-6.dsc")
        self.put("input/debian/control")
        self.put("input/bpp-core.spec")
        fmt = self.detect()
        self.assertEqual(fmt.name, "debian")
        self.assertIn("upstream source", fmt.evidence[-1])

    def test_spec_and_debian_dir_without_dsc_stay_unknown(self):
        self.put("input/debian/control")
        self.put("input/bpp-core.spec")
        fmt = self.detect()
        self.assertEqual((fmt.name, fmt.basis), ("unknown", "files"))
        joined = " ".join(fmt.evidence)
        self.assertIn("debian/control", joined)
        self.assertIn("bpp-core.spec", joined)

    def test_nested_spec_is_ignored(self):
        # Shaped like dnscap: rpm/dnscap.spec inside a Debian package's upstream tree.
        self.put("input/debian/control")
        self.put("input/rpm/dnscap.spec")
        self.assertEqual(self.detect().name, "debian")

    def test_spec_alone_means_rpm(self):
        self.put("input/foo.spec")
        self.assertEqual(self.detect().name, "rpm")

    def test_nothing_recognizable_is_unknown(self):
        self.put("input/README")
        fmt = self.detect()
        self.assertEqual((fmt.name, fmt.basis), ("unknown", "none"))

    def test_missing_input_is_unknown_not_an_error(self):
        shutil.rmtree(self.tree / "input")
        fmt = self.detect()
        self.assertEqual(fmt.name, "unknown")
        self.assertIn("missing or unreadable", fmt.evidence[0])

    def test_many_spec_files_are_summarized(self):
        for i in range(6):
            self.put(f"input/s{i}.spec")
        fmt = self.detect()
        self.assertEqual(fmt.name, "rpm")
        self.assertIn("and 3 more", fmt.evidence[0])


if __name__ == "__main__":
    unittest.main()
