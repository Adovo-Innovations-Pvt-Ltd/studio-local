"""Verify the release archive and install it in a clean, relocated test folder."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
    for filename, expected in [(f'studio-local-{builder.VERSION}-macos-arm64.zip', plugin_files),
                               (f'studio-local-marketplace-{builder.VERSION}.zip', repo_files)]:
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

scratch = REPO/'.test-data'
scratch.mkdir(exist_ok=True, mode=0o700)
with tempfile.TemporaryDirectory(prefix='verify-', dir=scratch) as directory:
    test = Path(directory)
    extracted = test/'Studio Local package with spaces'
    extracted.mkdir()
    with zipfile.ZipFile(REPO/f'dist/studio-local-{builder.VERSION}-macos-arm64.zip') as archive:
        archive.extractall(extracted)
    data = test/'private data'
    subprocess.run([sys.executable, '-I', str(extracted/'scripts/setup.py'), '--data-dir', str(data)], check=True, shell=False)
    runtime = data/f'runtime/{builder.VERSION}-arm64/bin/python3'
    run_tests = """import json,sys,unittest
suite=unittest.TestSuite(unittest.TestLoader().discover(path, pattern='test_*.py') for path in sys.argv[1:])
result=unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print(json.dumps({'tests':result.testsRun}))
"""
    result = subprocess.run([str(runtime), '-I', '-c', run_tests, str(extracted/'tests'), str(REPO/'tools/tests')],
                            capture_output=True, text=True, check=False, shell=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    result.check_returncode()
    test_count = json.loads(result.stdout.splitlines()[-1])['tests']

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
    state = json.loads((data/'state/settings.json').read_text())
    if result.returncode == 0 or b'Key setup failed.' not in result.stderr or state['api_key'] != '':
        raise SystemExit('Non-terminal key setup was not rejected safely through the installed launcher.')

report = {'status':'passed','version':builder.VERSION,'tests':test_count,'platform_tested':'Apple Silicon/macOS/Python 3.11',
    'offline_setup':'Clean export extracted into a path with spaces; bundled wheels installed with no index and all hashes required.',
    'relocation':'Package and private runtime relocated independently; SDK and source hash checks passed.',
    'privacy':'Both ZIPs exactly match explicit allowlists; settings/history canaries excluded; no operational user data was accessed.',
    'artifacts':'Both ZIPs pass CRC and SHA-256 verification.',
    'key_setup':'Non-terminal entry is rejected. Hidden prompt logic tested with fake input; no real key entered.',
    'not_tested':['Intel execution (not supported in this release)','Human Terminal entry','Live API calls','Desktop marketplace install on a second Mac'],
    'publication':'This verification command performs no publication or upload.'}
(REPO/'dist/verification.json').write_text(json.dumps(report, indent=2)+'\n')
print(f'Release export, privacy canaries, clean offline install, relocation and {test_count} tests passed.')
