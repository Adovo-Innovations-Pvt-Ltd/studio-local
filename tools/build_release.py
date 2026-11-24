"""Build exports from an explicit public-file allowlist, never from a directory copy."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO/'plugins/openai-studio'
PUBLIC_FILES = [
    'plugin.json', 'mcp.json', '.mcp.json', '.codex-plugin/plugin.json', 'bin/studio',
    'server/mcp_server.py', 'server/storage.py', 'server/launch.py', 'server/entry.py',
    'server/defaults.json', 'server/model_reference.json', 'scripts/setup.py', 'scripts/setup_key.py',
    'skills/openai-studio/SKILL.md', 'skills/setup/SKILL.md',
    'assets/icon.svg', 'assets/icon.png', 'assets/icon-256.png', 'assets/icon-64.png',
    'assets/mark.svg', 'assets/mark-dark.svg', 'assets/mark-mono.svg',
    'tests/test_studio.py', 'tests/test_portable.py', 'tests/default-settings.json',
    'README.md', 'DESIGN.md', 'PRIVACY.md', 'SECURITY.md', 'LICENSE', 'THIRD_PARTY_NOTICES.txt',
    'requirements.lock', 'requirements-arm64.lock', 'audit/dependencies.json',
    'audit/reviewed-sdk.json', 'audit/verification.md', 'audit/logo-gate.md']
REPO_FILES = ['README.md', 'LICENSE', 'PUBLISHING.md', '.gitignore', '.agents/plugins/marketplace.json',
              'tools/build_release.py', 'tools/download_wheels.py', 'tools/verify_release.py']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wheel_files():
    records = json.loads((PLUGIN/'audit/dependencies.json').read_text())
    result = []
    for record in records:
        filename = record['filename']
        if record['architecture'] != 'arm64' or not re.fullmatch(r'[A-Za-z0-9_.+-]+[.]whl', filename):
            raise ValueError('Unexpected dependency record.')
        relative = 'wheelhouse/arm64/'+filename
        path = PLUGIN/relative
        if path.is_symlink() or digest(path) != record['sha256']:
            raise ValueError('A public wheel failed its provenance hash.')
        result.append(relative)
    pins = {line.split('==')[0].lower().replace('_', '-') for line in (PLUGIN/'requirements.lock').read_text().splitlines() if line.strip()}
    names = {r['package'].lower().replace('_', '-') for r in records}
    if names != pins or len(names) != len(records):
        raise ValueError('The wheel set does not exactly match the pinned dependency list.')
    return sorted(result)


def notices(wheels):
    result = ['Third-party dependencies retain their original licenses. Original wheel archives and embedded license notices are included.\n']
    for relative in wheels:
        result.append('\n'+'='*72+'\n'+Path(relative).name+'\n'+'='*72+'\n')
        with zipfile.ZipFile(PLUGIN/relative) as archive:
            for name in sorted(archive.namelist()):
                lower = name.lower()
                if '.dist-info/' in lower and ('/licenses/' in lower or lower.rsplit('/', 1)[-1].startswith(('license', 'copying', 'notice', 'authors'))):
                    result.append('\n'+name+'\n'+archive.read(name).decode('utf-8', errors='replace')+'\n')
    (PLUGIN/'THIRD_PARTY_NOTICES.txt').write_text(''.join(result))


def package_files():
    wheels = wheel_files()
    notices(wheels)
    files = sorted(PUBLIC_FILES+wheels)
    for relative in files:
        path = PLUGIN/relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(PLUGIN):
            raise ValueError('Missing or unsafe public file: '+relative)
        if path.suffix in {'.py', '.json', '.md', '.txt', '.svg', '.lock'} or path.name in {'studio', 'LICENSE'}:
            contents = path.read_bytes()
            if b'/Users/jezwn' in contents or re.search(rb'sk-[A-Za-z0-9_-]{24,}', contents):
                raise ValueError('Personal path or credential-like text in a public source file: '+relative)
    defaults = json.loads((PLUGIN/'server/defaults.json').read_text())
    if defaults['api_key'] or defaults['history'] or defaults['estimated_spend_usd']:
        raise ValueError('Public defaults must have an empty key, history and spend.')
    manifest = {'name': 'openai-studio', 'display_name': 'Studio Local', 'version': '1.1.1',
        'platform': 'macOS 13+ / Apple Silicon / native CPython 3.11',
        'files': {name: digest(PLUGIN/name) for name in files},
        'dependency_files': json.loads((PLUGIN/'audit/reviewed-sdk.json').read_text())}
    (PLUGIN/'release-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return files+['release-manifest.json']


def write_zip(destination, root, files):
    with zipfile.ZipFile(destination, 'w') as archive:
        for relative in sorted(files):
            path = root/relative
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('Unsafe export path.')
            info = zipfile.ZipInfo(relative, date_time=(2026, 11, 24, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if relative.endswith('/bin/studio') or relative == 'bin/studio' else 0o100644) << 16
            info.compress_type = zipfile.ZIP_STORED if path.suffix in {'.whl', '.png'} else zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def build():
    plugin_files = package_files()
    (REPO/'LICENSE').write_bytes((PLUGIN/'LICENSE').read_bytes())
    repo_files = REPO_FILES+['plugins/openai-studio/'+p for p in plugin_files]
    destination = REPO/'dist'
    destination.mkdir(exist_ok=True)
    plugin_zip = destination/'studio-local-1.1.1-macos-arm64.zip'
    repo_zip = destination/'studio-local-marketplace-1.1.1.zip'
    write_zip(plugin_zip, PLUGIN, plugin_files)
    write_zip(repo_zip, REPO, repo_files)
    (destination/'SHA256SUMS.txt').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in [plugin_zip, repo_zip]))
    (destination/'export-files.json').write_text(json.dumps({'plugin': plugin_files, 'repository': repo_files}, indent=2)+'\n')
    print(f'Built {len(plugin_files)} public plugin entries and two ZIPs from the explicit allowlist.')
    return plugin_files, repo_files


if __name__ == '__main__':
    build()
