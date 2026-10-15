"""Per-case views of the 10-case local bundle (harness/case_views.py).

Run from the repository root:  python3 -m unittest discover -s harness/tests -t .
"""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from harness.case_views import make_views


class CaseViews(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bundle = self.root / "bundle dir"  # a space, to check quoting
        for case_id in ("case-a", "case-b"):
            case = self.bundle / "cases" / case_id
            (case / "input").mkdir(parents=True)
            (case / "manifest.json").write_text(json.dumps({"case_id": case_id}))
        (self.bundle / "cases" / "not-a-case").mkdir()
        # The real run.sh selects a case with --case; this one records its call.
        (self.bundle / "run.sh").write_text('echo "$PWD|$*" >> calls.txt\n')
        self.views = self.root / "views"

    def test_one_view_per_case(self):
        made = make_views(self.bundle, self.views)
        self.assertEqual([v.name for v in made], ["case-a", "case-b"])
        manifest = json.loads((self.views / "case-a/case/manifest.json").read_text())
        self.assertEqual(manifest["case_id"], "case-a")
        self.assertTrue((self.views / "case-a/case/input").is_dir())

    def test_run_sh_selects_the_case_and_runs_from_the_bundle(self):
        make_views(self.bundle, self.views)
        subprocess.run(["bash", str(self.views / "case-b/run.sh"), "--input", "/x y",
                        "--output", "/out"], cwd=self.root, check=True)
        call = (self.bundle / "calls.txt").read_text().strip()
        self.assertEqual(call, f"{self.bundle.resolve()}|--case case-b --input /x y --output /out")

    def test_refreshing_is_safe_and_bundle_is_untouched(self):
        before = sorted(p.relative_to(self.bundle) for p in self.bundle.rglob("*"))
        make_views(self.bundle, self.views)
        make_views(self.bundle, self.views)
        self.assertEqual(sorted(p.relative_to(self.bundle) for p in self.bundle.rglob("*")),
                         before)

    def test_rejects_a_single_case_bundle_and_views_inside_the_bundle(self):
        with self.assertRaises(ValueError):
            make_views(self.bundle / "cases" / "case-a", self.views)
        with self.assertRaises(ValueError):
            make_views(self.bundle, self.bundle / "views")


if __name__ == "__main__":
    unittest.main()
