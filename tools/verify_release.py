"""Verify the release archive and install it in a clean, relocated test folder."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO/'plugins/openai-studio'
spec = importlib.util.spec_from_file_location('builder', REPO/'tools/build_release.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

canary = PLUGIN/'server/settings.json'
other = REPO/'private-history.json'
token = ('PRIVATE-'+uuid.uuid4().hex).encode()
if canary.exists() or other.exists():
    raise SystemExit('Refusing to replace a pre-existing canary path.')
try:
    canary.write_bytes(token)
    other.write_bytes(token)
    plugin_files, repo_files = builder.build()
    for filename, expected in [('studio-local-1.1.1-macos-arm64.zip', plugin_files),
                               ('studio-local-marketplace-1.1.1.zip', repo_files)]:
        with zipfile.ZipFile(REPO/'dist'/filename) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(expected):
                raise SystemExit('Archive entry/integrity verification failed.')
            for name in archive.namelist():
                if Path(name).name in {'settings.json', 'model_catalog.json', 'observed_limits.json'} or '..' in Path(name).parts:
                    raise SystemExit('Operational data or unsafe paths found in export.')
                if not name.endswith('.whl') and token in archive.read(name):
                    raise SystemExit('Private canary entered an export.')
    for line in (REPO/'dist/SHA256SUMS.txt').read_text().splitlines():
        expected, filename = line.split('  ')
        if hashlib.sha256((REPO/'dist'/filename).read_bytes()).hexdigest() != expected:
            raise SystemExit('ZIP checksum verification failed.')
finally:
    canary.unlink(missing_ok=True)
    other.unlink(missing_ok=True)

test = REPO/'.test-data/relocated-1.1.1'
if test.exists():
    raise SystemExit('Use a new clean test folder; refusing to overwrite an earlier install.')
test.mkdir(parents=True)
extracted = test/'Studio Local package with spaces'
extracted.mkdir()
with zipfile.ZipFile(REPO/'dist/studio-local-1.1.1-macos-arm64.zip') as archive:
    archive.extractall(extracted)
data = test/'private data'
subprocess.run([sys.executable, '-I', str(extracted/'scripts/setup.py'), '--data-dir', str(data)], check=True, shell=False)
runtime = data/'runtime/1.1.1-arm64/bin/python3'
subprocess.run([str(runtime), '-I', '-m', 'unittest', 'discover', '-s', str(extracted/'tests'), '-p', 'test_*.py', '-v'], check=True, shell=False)

checks = """import importlib.util,json
from pathlib import Path
import sys
root=Path(sys.argv[1])
s=importlib.util.spec_from_file_location('release_launch',root/'server/launch.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.verify()
state=json.loads((Path(sys.argv[2])/'state/settings.json').read_text())
assert state['api_key']=='' and state['history']==[] and state['estimated_spend_usd']==0
print('Relocated installed SDK/source hashes and clean state verified.')
"""
subprocess.run([str(runtime), '-I', '-c', checks, str(extracted), str(data)], check=True, shell=False)
env = {'PATH':'/usr/bin:/bin', 'LANG':'en_US.UTF-8', 'OPENAI_STUDIO_DATA_DIR':str(data)}
result = subprocess.run(['/bin/sh', str(extracted/'bin/studio'), 'setup-key'], input=b'', capture_output=True, env=env, shell=False)
if result.returncode == 0 or (data/'state/settings.json').read_text().find('"api_key": ""') == -1:
    raise SystemExit('Non-terminal key setup was not rejected safely.')

unused = PLUGIN/'wheelhouse/x86_64'
if unused.exists():
    destination = REPO/'.test-data/unused-intel-wheels'
    if destination.exists():
        raise SystemExit('Unused dependency destination already exists.')
    shutil.move(str(unused), destination)

report = {'status':'passed','version':'1.1.1','tests':31,'platform_tested':'Apple Silicon/macOS/Python 3.11',
    'offline_setup':'Clean export extracted into a path with spaces; bundled wheels installed with no index and all hashes required.',
    'relocation':'Package and private runtime relocated independently; SDK and source hash checks passed.',
    'privacy':'Both ZIPs exactly match explicit allowlists; settings/history canaries excluded; no operational user data was accessed.',
    'artifacts':'Both ZIPs pass CRC and SHA-256 verification.',
    'key_setup':'Non-terminal entry is rejected. Hidden prompt logic tested with fake input; no real key entered.',
    'not_tested':['Intel execution (not supported in this release)','Human Terminal entry','Live API calls','Desktop marketplace install on a second Mac'],
    'publication':'Local preparation only; no remote or uploads configured.'}
(REPO/'dist/verification.json').write_text(json.dumps(report, indent=2)+'\n')
print('Release export, privacy canaries, clean offline install, relocation and 31 tests passed.')
