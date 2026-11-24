"""Maintainer-only: fetch pinned public PyPI wheels into the release sandbox."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO/'plugins/openai-studio'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Dependency redirects are disabled.')


def response(url):
    import certifi
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'pypi.org', 'files.pythonhosted.org'} or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise ValueError('Only fixed public PyPI hosts are permitted.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                         urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    return opener.open(url, timeout=30)


def download_pin(pin):
    from pip._vendor.packaging.tags import cpython_tags, compatible_tags, mac_platforms
    from pip._vendor.packaging.utils import parse_wheel_filename
    if not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+', pin):
        raise ValueError('Expected an exact public package pin.')
    name, version = pin.split('==')
    with response(f'https://pypi.org/pypi/{name}/{version}/json') as handle:
        metadata = json.loads(handle.read(10 * 1024 * 1024))
    records = []
    for arch in ['arm64']:
        platforms = list(mac_platforms(version=(13, 0), arch=arch))
        ordered = list(cpython_tags(python_version=(3, 11), abis=['cp311'], platforms=platforms))
        ordered += list(compatible_tags(python_version=(3, 11), interpreter='cp311', platforms=platforms))
        rank = {tag: i for i, tag in reversed(list(enumerate(ordered)))}
        candidates = []
        for item in metadata['urls']:
            filename = item['filename']
            if item['packagetype'] != 'bdist_wheel' or item.get('yanked') or not re.fullmatch(r'[A-Za-z0-9_.+-]+[.]whl', filename):
                continue
            tags = parse_wheel_filename(filename)[3]
            scores = [rank[tag] for tag in tags if tag in rank]
            if scores:
                candidates.append((min(scores), filename, item))
        if not candidates:
            available = [item['filename'] for item in metadata['urls'] if 'macosx' in item['filename']]
            raise ValueError(f'No compatible macOS 13 / CPython 3.11 wheel: {pin} {arch}; available: {available}')
        item = min(candidates)[2]
        expected = item['digests']['sha256']
        if not re.fullmatch(r'[0-9a-f]{64}', expected) or item['size'] > 100 * 1024 * 1024:
            raise ValueError('Invalid dependency size/hash metadata.')
        target = PLUGIN/'wheelhouse'/arch/item['filename']
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.download-')
            try:
                count, digest = 0, hashlib.sha256()
                with os.fdopen(fd, 'wb') as output, response(item['url']) as handle:
                    while chunk := handle.read(1024 * 1024):
                        count += len(chunk)
                        if count > item['size']:
                            raise ValueError('Wheel exceeds declared size.')
                        digest.update(chunk)
                        output.write(chunk)
                if count != item['size'] or digest.hexdigest() != expected:
                    raise ValueError('Wheel hash or size verification failed.')
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        records.append({'package': name, 'version': version, 'architecture': arch,
                        'filename': item['filename'], 'sha256': expected, 'source': item['url'],
                        'license_expression': metadata['info'].get('license_expression')})
    return records


def fetch_all():
    pins = [line.strip() for line in (PLUGIN/'requirements.lock').read_text().splitlines() if line.strip()]
    with ThreadPoolExecutor(max_workers=6) as pool:
        records = [record for batch in pool.map(download_pin, pins) for record in batch]
    for arch in ['arm64']:
        selected = [r for r in records if r['architecture'] == arch]
        lock = '\n'.join(f"{r['package']}=={r['version']} --hash=sha256:{r['sha256']}" for r in selected)+'\n'
        (PLUGIN/f'requirements-{arch}.lock').write_text(lock)
    (PLUGIN/'audit/dependencies.json').write_text(json.dumps(records, indent=2)+'\n')
    print(f'Fetched and SHA-256 verified {len(records)} public wheels for Apple Silicon; no plugin state or key was accessible.')


if __name__ == '__main__':
    if sys.argv[1:] == ['--child']:
        fetch_all()
    elif not sys.argv[1:]:
        with tempfile.TemporaryDirectory(prefix='studio-wheels-') as directory:
            scratch = Path(directory).resolve()
            reads = [REPO, Path(sys.prefix), Path(sys.base_prefix), scratch,
                     Path('/System'), Path('/Library'), Path('/usr'), Path('/bin'), Path('/dev'), Path('/private/etc')]
            paths = lambda roots: ' '.join('(subpath '+json.dumps(str(p.resolve()))+')' for p in roots)
            writes = [PLUGIN/'wheelhouse', scratch]
            literals = ' '.join('(literal '+json.dumps(str(p))+')' for p in [PLUGIN/'requirements-arm64.lock', PLUGIN/'requirements-x86_64.lock', PLUGIN/'audit/dependencies.json'])
            policy = '\n'.join(['(version 1)', '(deny default)',
                '(allow process-exec process-fork sysctl-read mach-lookup ipc-posix-shm)',
                '(allow signal process-info* (target same-sandbox))', '(allow file-read-metadata file-test-existence)',
                '(allow file-read* (literal "/"))', '(allow file-read* file-map-executable '+paths(reads)+')',
                '(allow file-write* '+paths(writes)+' '+literals+')',
                '(allow file-read* (literal "/private/var/run/resolv.conf") (literal "/private/var/run/mDNSResponder"))',
                '(allow network-outbound (remote unix-socket (path "/private/var/run/mDNSResponder")))',
                '(allow network-outbound (remote tcp "*:443"))'])
            env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'TMPDIR': str(scratch), 'PYTHONDONTWRITEBYTECODE': '1'}
            raise SystemExit(subprocess.run(['/usr/bin/sandbox-exec', '-p', policy, sys.executable, '-I', str(Path(__file__).resolve()), '--child'],
                                            cwd=REPO, env=env, shell=False).returncode)
    else:
        raise SystemExit('Run without arguments to download the pinned public wheels in confinement.')
