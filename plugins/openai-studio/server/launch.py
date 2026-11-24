"""Verify the public package and run it with bounded filesystem/network access."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN/'server'))
from storage import data_directory


def verify():
    audit = json.loads((PLUGIN/'release-manifest.json').read_text())
    for relative, expected in audit['files'].items():
        path = PLUGIN/relative
        if path.is_symlink() or not path.resolve().is_relative_to(PLUGIN) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Package files changed after release verification.')
    runtime = Path(sys.prefix)
    for relative, expected in audit['dependency_files'].items():
        path = runtime/'lib/python3.11/site-packages'/relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('A reviewed runtime dependency changed; reinstall the offline runtime.')


def profile(data, scratch):
    reads = [PLUGIN, Path(sys.prefix), Path(sys.base_prefix), scratch, data/'state', data/'inputs', data/'outputs',
             Path('/System'), Path('/Library'), Path('/usr'), Path('/bin'), Path('/sbin'), Path('/dev'), Path('/private/etc')]
    writes = [data/'state', data/'inputs', data/'outputs', scratch]
    clauses = lambda paths: ' '.join('(subpath '+json.dumps(str(p.resolve()))+')' for p in paths)
    return '\n'.join(['(version 1)', '(deny default)',
        '(allow sysctl-read mach-lookup ipc-posix-shm)',
        '(allow signal process-info* (target same-sandbox))', '(allow process-fork)',
        '(allow process-exec (subpath '+json.dumps(str(Path(sys.base_prefix).resolve()))+') (literal '+json.dumps(str(Path(sys.executable).resolve()))+'))',
        '(allow file-read-metadata file-test-existence)', '(allow file-read* (literal "/"))',
        '(allow file-read* file-map-executable '+clauses(reads)+')',
        '(allow file-write* '+clauses(writes)+')',
        '(allow file-read* (literal "/private/var/run/resolv.conf") (literal "/private/var/run/mDNSResponder"))',
        '(allow network-outbound (remote unix-socket (path "/private/var/run/mDNSResponder")))',
        '(allow network-outbound (remote tcp "*:443"))'])


def main():
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        raise ValueError('Studio Local requires macOS sandbox-exec; no unrestricted fallback.')
    verify()
    data = data_directory()
    for folder in [data, data/'state', data/'inputs', data/'outputs']:
        if folder.is_symlink():
            raise ValueError('Data folders must not be symlinks.')
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='studio-local-') as directory:
        scratch = Path(directory).resolve()
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'TMPDIR': str(scratch),
               'OPENAI_STUDIO_DATA_DIR': str(data), 'PYTHONDONTWRITEBYTECODE': '1'}
        return subprocess.run(['/usr/bin/sandbox-exec', '-p', profile(data, scratch),
            sys.executable, '-I', str(PLUGIN/'server/entry.py')], env=env, cwd=PLUGIN, shell=False).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        raise SystemExit('Studio Local could not verify or sandbox the package. Run the documented offline setup; no unrestricted fallback was used.')
