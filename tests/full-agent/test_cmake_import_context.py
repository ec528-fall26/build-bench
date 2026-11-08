import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
    "buildbench-starter-kit-0.1.0-rc.5/agents/full-agent"))
from src.evidence import diagnose

class CMakeImportedContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'input').mkdir()
        self.log = self.root / 'build.log'
        self.log.write_text('CMake Error in src/CMakeLists.txt:\n'
            '  Imported target "vendor_core" includes non-existent path\n\n'
            '    "/missing/modules"\n\n'
            '  in its INTERFACE_INCLUDE_DIRECTORIES.\n')
    def source(self, path, text):
        p = self.root / 'input' / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p
    def run_parser(self, mode='1'):
        with patch.dict(os.environ, {'BB_EXPERIMENT_CMAKE_CONTEXT': mode}):
            return diagnose(self.log, self.root)
    def test_default_off_preserves_original_diagnostic(self):
        self.source('CMakeLists.txt', 'target_link_libraries(app vendor_core companion)')
        with patch.dict(os.environ, {}, clear=True):
            default = diagnose(self.log, self.root)
        self.assertEqual(default, self.run_parser('0'))
        self.assertEqual(default[0].compiler_details, '')
    def test_source_context_includes_neighbors_and_line_provenance(self):
        self.source('src/CMakeLists.txt', 'target_link_libraries(app\n vendor_core\n companion_${precision}\n)\n')
        result = self.run_parser()[0]
        self.assertIn('input/src/CMakeLists.txt:3:', result.compiler_details)
        self.assertIn('companion_${precision}', result.compiler_details)
        self.assertIn('does not establish', result.compiler_details)
        self.assertIn('/missing/modules', result.message)
    def test_excludes_backups_symlinks_comments_and_substring_matches(self):
        self.source('.pc/backup/CMakeLists.txt', 'vendor_core BACKUP_SENTINEL')
        outside = self.root / 'outside.cmake'
        outside.write_text('vendor_core SYMLINK_SENTINEL')
        (self.root/'input/link.cmake').symlink_to(outside)
        self.source('CMakeLists.txt', '# vendor_core COMMENT_SENTINEL\nvendor_core_extra SUBSTRING_SENTINEL')
        details = self.run_parser()[0].compiler_details
        for word in ('BACKUP_SENTINEL','SYMLINK_SENTINEL','COMMENT_SENTINEL','SUBSTRING_SENTINEL'):
            self.assertNotIn(word, details)
        self.assertIn('does not prove absence', details)
    def test_no_imported_target_leaves_other_errors_unchanged(self):
        self.log.write_text('src/a.c:2: error: missing type\n')
        self.assertEqual(self.run_parser('0'), self.run_parser('1'))
    def test_large_source_is_bounded_and_reports_incomplete(self):
        self.source('CMakeLists.txt', ('vendor_core ' + 'x'*500 + '\n')*1000)
        details = self.run_parser()[0].compiler_details
        self.assertLessEqual(len(details), 8000)
        self.assertIn('Source search incomplete', details)
    def test_target_matching_is_literal(self):
        self.log.write_text(self.log.read_text().replace('vendor_core','vendor::core.v1'))
        self.source('CMakeLists.txt','vendor::coreXv1 WRONG_MATCH')
        self.assertNotIn('WRONG_MATCH', self.run_parser()[0].compiler_details)

if __name__ == '__main__':
    unittest.main()
