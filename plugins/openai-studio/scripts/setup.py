"""One-time offline runtime setup using bundled, hash-locked wheels."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN/'server'))
from storage import data_directory, initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, help='Dedicated absolute data directory; normally leave unset.')
    args = parser.parse_args()
    if args.data_dir is not None:
        os.environ['OPENAI_STUDIO_DATA_DIR'] = str(args.data_dir)
    arch = platform.machine()
    if sys.platform != 'darwin' or sys.version_info[:2] != (3, 11) or arch != 'arm64':
        raise ValueError('This release requires native CPython 3.11 on an Apple Silicon Mac.')
    if int(platform.mac_ver()[0].split('.')[0]) < 13:
        raise ValueError('Studio Local requires macOS 13 or later.')
    manifest_path = PLUGIN/'release-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for relative, expected in manifest['files'].items():
        path = PLUGIN/relative
        if path.is_symlink() or not path.resolve().is_relative_to(PLUGIN) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('A package file failed verification. Use an unmodified release.')
    data = data_directory()
    initialize(data)
    environment = data/f'runtime/1.1.0-{arch}'
    marker = environment/'.studio-runtime.json'
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if environment.is_symlink() or environment.parent.is_symlink():
        raise ValueError('Runtime directories must not be symlinks.')
    if environment.exists():
        if marker.is_file() and json.loads(marker.read_text()).get('manifest_sha256') == manifest_hash:
            print('This runtime is already set up. Run bin/studio setup-key in your own Terminal.')
            return
        raise ValueError('An incomplete or different runtime exists. Move that runtime directory aside and run setup again; keep state, inputs and outputs.')
    environment.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    venv.EnvBuilder(with_pip=True, symlinks=True).create(environment)
    python = environment/'bin/python3'
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'PIP_CONFIG_FILE': '/dev/null',
           'PYTHONDONTWRITEBYTECODE': '1'}
    if os.environ.get('TMPDIR'):
        env['TMPDIR'] = os.environ['TMPDIR']
    subprocess.run([str(python), '-I', '-m', 'pip', '--disable-pip-version-check', '--no-cache-dir',
        'install', '--no-index', '--no-deps', '--only-binary=:all:', '--require-hashes', '--no-compile',
        '--find-links', str(PLUGIN/'wheelhouse'/arch), '-r', str(PLUGIN/f'requirements-{arch}.lock')],
        env=env, check=True, shell=False)
    subprocess.run([str(python), '-I', '-m', 'pip', '--disable-pip-version-check', 'check'], env=env, check=True, shell=False)
    marker.write_text(json.dumps({'version': '1.1.0', 'architecture': arch, 'manifest_sha256': manifest_hash})+'\n')
    marker.chmod(0o600)
    print('Offline runtime ready. In your own Terminal, run:')
    import shlex
    print('/bin/sh '+shlex.quote(str(PLUGIN/'bin/studio'))+' setup-key')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError):
        raise SystemExit('Offline setup failed. Use an unmodified release, native Python 3.11, and a dedicated writable data directory. No online fallback was used.')
