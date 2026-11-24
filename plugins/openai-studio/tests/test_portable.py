"""Offline checks for private setup, data isolation and portable package metadata."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import xml.etree.ElementTree as ET

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN/'server'))
import storage

spec = importlib.util.spec_from_file_location('key_setup', PLUGIN/'scripts/setup_key.py')
key_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(key_setup)


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name).resolve()/'private-data'

    def tearDown(self):
        self.temporary.cleanup()

    def test_fresh_state_has_no_credentials_history_or_spend(self):
        storage.initialize(self.data)
        state = json.loads((self.data/'state/settings.json').read_text())
        self.assertEqual(state['api_key'], '')
        self.assertEqual(state['history'], [])
        self.assertEqual(state['estimated_spend_usd'], 0)
        self.assertEqual(stat.S_IMODE((self.data/'state/settings.json').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o700)

    def test_repeat_setup_preserves_private_state(self):
        storage.save_key(self.data, 'sk-offline-key-only')
        path = self.data/'state/settings.json'
        state = json.loads(path.read_text())
        state.update(history=[{'id': 'private-job'}], estimated_spend_usd=3.25)
        path.write_text(json.dumps(state))
        (self.data/'outputs/keep.txt').write_text('private output')
        storage.initialize(self.data)
        storage.save_key(self.data, 'sk-new-offline-key')
        new = json.loads(path.read_text())
        self.assertEqual(new['history'], state['history'])
        self.assertEqual(new['estimated_spend_usd'], 3.25)
        self.assertEqual((self.data/'outputs/keep.txt').read_text(), 'private output')

    def test_masked_prompt_never_prints_key_and_clears_no_other_state(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'OPENAI_STUDIO_DATA_DIR': str(self.data)}), \
             patch.object(sys, 'argv', ['setup-key']), patch.object(sys.stdin, 'isatty', return_value=True), \
             patch.object(key_setup.getpass, 'getpass', return_value='sk-offline-prompt-only') as prompt, \
             contextlib.redirect_stdout(output):
            key_setup.main()
        prompt.assert_called_once_with('OpenAI API key (hidden): ')
        self.assertNotIn('sk-offline-prompt-only', output.getvalue())
        state = json.loads((self.data/'state/settings.json').read_text())
        self.assertEqual(state['api_key'], 'sk-offline-prompt-only')

    def test_prompt_refuses_pipes_and_unmasked_getpass_fallback(self):
        with patch.object(sys, 'argv', ['setup-key']), patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaises(ValueError):
                key_setup.main()
        def fallback(_):
            warnings.warn('No terminal echo control', key_setup.getpass.GetPassWarning)
        with patch.object(sys, 'argv', ['setup-key']), patch.object(sys.stdin, 'isatty', return_value=True), \
             patch.object(key_setup.getpass, 'getpass', side_effect=fallback):
            with self.assertRaises(key_setup.getpass.GetPassWarning):
                key_setup.main()
        self.assertFalse(self.data.exists())

    def test_key_in_argv_is_rejected(self):
        with patch.object(sys, 'argv', ['setup-key', 'sk-never-in-argv']):
            with self.assertRaises(ValueError):
                key_setup.main()

    def test_symlink_data_and_settings_are_rejected(self):
        link = self.data.parent/'linked-data'
        link.symlink_to(self.data)
        with patch.dict(os.environ, {'OPENAI_STUDIO_DATA_DIR': str(link)}):
            with self.assertRaises(ValueError):
                storage.data_directory()
        storage.initialize(self.data)
        settings = self.data/'state/settings.json'
        settings.unlink()
        target = self.data/'private.json'
        target.write_text('{}')
        settings.symlink_to(target)
        with self.assertRaises(ValueError):
            storage.save_key(self.data, 'sk-offline-key')
        self.assertEqual(target.read_text(), '{}')

    def test_dot_segments_cannot_select_home_or_root(self):
        home = self.data.parent/'home'
        home.mkdir()
        for value in [home/'unused/..', Path('/private/..')]:
            with self.subTest(path=value), patch.object(Path, 'home', return_value=home), \
                 patch.dict(os.environ, {'OPENAI_STUDIO_DATA_DIR': str(value)}):
                with self.assertRaises(ValueError):
                    storage.data_directory()
        self.assertFalse((home/'state').exists())
        with patch.object(Path, 'home', return_value=home), \
             patch.dict(os.environ, {'OPENAI_STUDIO_DATA_DIR': str(home/'private/child/..')}):
            self.assertEqual(storage.data_directory(), home/'private')

    def test_data_directory_cannot_overlap_plugin(self):
        plugin = self.data.parent/'plugin'
        with patch.object(storage, 'PACKAGE', plugin/'server'):
            for value in [plugin, plugin/'private-data', plugin.parent]:
                with self.subTest(path=value), patch.dict(os.environ, {'OPENAI_STUDIO_DATA_DIR': str(value)}):
                    with self.assertRaises(ValueError):
                        storage.data_directory()

    def test_case_aliases_cannot_select_protected_directories(self):
        home = self.data.parent/'home'
        plugin = self.data.parent/'plugin'
        home.mkdir()
        plugin.mkdir()
        if not home.with_name('HOME').exists():
            self.skipTest('Case-sensitive filesystem; no case aliases exist.')
        values = [home.with_name('HOME'), plugin.with_name('PLUGIN'),
                  plugin.with_name('PLUGIN')/'private-data', self.data.parent.with_name(self.data.parent.name.upper())]
        with patch.object(Path, 'home', return_value=home), patch.object(storage, 'PACKAGE', plugin/'server'):
            for value in values:
                with self.subTest(path=value), patch.dict(os.environ, {'OPENAI_STUDIO_DATA_DIR': str(value)}):
                    with self.assertRaises(ValueError):
                        storage.data_directory()

    def test_portable_metadata_and_original_icons(self):
        manifest = json.loads((PLUGIN/'plugin.json').read_text())
        release = json.loads((PLUGIN/'release-manifest.json').read_text())
        self.assertEqual(manifest['version'], release['version'])
        interface = manifest['extensions']['com.openai']['interface']
        self.assertEqual(interface['displayName'], 'Studio Local')
        self.assertLessEqual(len(interface['shortDescription']), 30)
        for field in ['composerIcon', 'composerIconDark']:
            root = ET.fromstring((PLUGIN/interface[field]).read_text())
            self.assertEqual(root.attrib['viewBox'], '0 0 1024 1024')
        mcp = json.loads((PLUGIN/'mcp.json').read_text())['mcpServers']['openai_studio']
        self.assertEqual(mcp['command'], '/bin/sh')
        self.assertEqual(mcp['args'][0], '${PLUGIN_ROOT}/bin/studio')


if __name__ == '__main__':
    unittest.main(verbosity=2)
