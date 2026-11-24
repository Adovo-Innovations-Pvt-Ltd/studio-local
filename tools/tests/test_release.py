"""Offline checks for the release export boundary."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_builder', Path(__file__).resolve().parents[1]/'build_release.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseTests(unittest.TestCase):
    def test_public_document_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'README.md').write_text('Public setup instructions.\n')
            builder.validate_public_file(root, 'README.md')

    def test_repository_documents_receive_secret_and_personal_path_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for contents in ['sk-'+'offline-test-value-'*3, '/Users'+'/example/private', '/home'+'/example/private']:
                with self.subTest(contents=contents):
                    (root/'README.md').write_text(contents)
                    with patch.object(builder, 'REPO', root), patch.object(builder, 'REPO_FILES', ['README.md']), \
                         patch.object(builder, 'package_files', side_effect=AssertionError('Package work started before repository validation')):
                        with self.assertRaisesRegex(ValueError, 'Personal path or credential-like'):
                            builder.build()

    def test_exports_reject_symlinks_and_paths_outside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent/'public'
            root.mkdir()
            (parent/'outside.md').write_text('Must not be exported.\n')
            (root/'linked.md').symlink_to(parent/'outside.md')
            for name in ['linked.md', '../outside.md']:
                with self.subTest(path=name), self.assertRaisesRegex(ValueError, 'unsafe public file'):
                    builder.validate_public_file(root, name)


if __name__ == '__main__':
    unittest.main()
