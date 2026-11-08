import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
    'buildbench-starter-kit-0.1.0-rc.5/agents/full-agent'))
from src.evidence import diagnose

class PackagingSourceContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root/'input').mkdir()
        self.log = self.root/'build.log'
        self.log.write_text("error: invalid command 'old_doc_builder'\n")
    def put(self, name, text):
        p = self.root/'input'/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p
    def parse(self, mode):
        with patch.dict(os.environ, {'BB_EXPERIMENT_PACKAGING_CONTEXT': mode,
                                    'BB_EXPERIMENT_CMAKE_CONTEXT': '0'}):
            return diagnose(self.log, self.root)
    def test_exposes_original_output_and_producer(self):
        self.put('debian/sample-doc.install', './manual/generated/web usr/share/doc/sample\n')
        self.put('setup.cfg', '[build_docs]\nsource-dir = manual\n')
        self.put('manual/Makefile', 'OUT = generated\nweb:\n\tdoc-tool -o $(OUT)/web\n')
        text = self.parse('1')[0].compiler_details
        self.assertIn('manual/generated/web', text)
        self.assertIn('input/manual/Makefile:3:', text)
        self.assertIn('doc-tool', text)
        self.assertIn('does not prove', text)
    def test_off_preserves_parser(self):
        self.put('debian/sample.install', 'data usr/share/sample\n')
        self.assertEqual(self.parse('0')[0].compiler_details, '')
    def test_no_manifest_no_change(self):
        self.assertEqual(self.parse('0'), self.parse('1'))
    def test_ignores_symlink_and_quilt_backup(self):
        self.put('.pc/debian/sample.install', 'SECRET')
        outside = self.root/'outside'
        outside.write_text('SECRET')
        (self.root/'input/debian').mkdir()
        (self.root/'input/debian/sample.install').symlink_to(outside)
        self.assertEqual(self.parse('0'), self.parse('1'))
    def test_bounds_large_manifest(self):
        self.put('debian/sample.install', 'x'*1000+'\n'+'entry\n'*10000)
        text = self.parse('1')[0].compiler_details
        self.assertLessEqual(len(text), 8000)
        self.assertIn('incomplete', text)
    def test_does_not_follow_parent_paths(self):
        self.put('debian/sample.install', '../outside/output usr/share/sample\n')
        (self.root/'Makefile').write_text('LEAK_SENTINEL')
        self.assertNotIn('LEAK_SENTINEL', self.parse('1')[0].compiler_details)
