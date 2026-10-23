import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).resolve().parents[2] / 'scripts/demo2_prepare_recorded.py'
spec = importlib.util.spec_from_file_location('recorded_profile', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class RecordedProfileTests(unittest.TestCase):
    def test_modified_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'source.py').write_bytes(b'original')
            expected = {'source.py': hashlib.sha256(b'original').hexdigest()}
            module.verify_sources(root, expected)
            (root/'source.py').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                module.verify_sources(root, expected)
    def test_missing_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                module.verify_sources(Path(tmp), {'absent.py': 'unused'})

if __name__ == '__main__':
    unittest.main()
