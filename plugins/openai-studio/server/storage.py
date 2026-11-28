"""Private local state, outside the distributable package."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile

PACKAGE = Path(__file__).resolve().parent


def _is_within(path, root):
    # File identity also catches case aliases on case-insensitive volumes.
    return path.is_relative_to(root) or (root.exists() and any(
        parent.exists() and parent.samefile(root) for parent in (path, *path.parents)))


def data_directory():
    value = os.environ.get('OPENAI_STUDIO_DATA_DIR')
    path = Path(value) if value else Path.home()/'Library/Application Support/Studio Local'
    if not path.is_absolute():
        raise ValueError('Choose an absolute, dedicated Studio Local data directory.')
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('Studio Local data paths must not contain symlinks.')
    path = path.resolve()
    plugin = PACKAGE.parent.resolve()
    home = Path.home().resolve()
    is_home = path == home or (path.exists() and home.exists() and path.samefile(home))
    if path == Path('/') or is_home or _is_within(path, plugin) or _is_within(plugin, path):
        raise ValueError('Choose a dedicated data directory outside the plugin, not a home or filesystem root.')
    return path


def initialize(data):
    for folder in [data, data/'state', data/'inputs', data/'outputs']:
        if folder.is_symlink():
            raise ValueError('Studio Local data directories must not be symlinks.')
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if folder.stat().st_uid != os.getuid():
            raise ValueError('Studio Local data must belong to the current user.')
        if stat.S_IMODE(folder.stat().st_mode) != 0o700:
            folder.chmod(0o700)
    target = data/'state/settings.json'
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if target.is_symlink():
            raise ValueError('Studio settings must not be a symlink.')
    else:
        with os.fdopen(fd, 'wb') as handle:
            handle.write((PACKAGE/'defaults.json').read_bytes())


def save_key(data, value):
    value = value.strip()
    if value and (len(value) > 512 or not re.fullmatch(r'sk-[A-Za-z0-9_-]+', value)):
        raise ValueError('Use a valid OpenAI API key beginning with sk-.')
    initialize(data)
    state_dir = data/'state'
    fd = os.open(state_dir/'.settings.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = state_dir/'settings.json'
        if settings.is_symlink() or settings.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Repair local settings before changing the key.')
        state = json.loads(settings.read_text())
        if not isinstance(state, dict) or not isinstance(state.get('history'), list):
            raise ValueError('Repair local settings before changing the key.')
        state['api_key'] = value
        descriptor, temporary = tempfile.mkstemp(prefix='.settings-', dir=state_dir)
        try:
            with os.fdopen(descriptor, 'w') as handle:
                json.dump(state, handle, indent=2, allow_nan=False)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, settings)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        for name in ['model_catalog.json', 'observed_limits.json']:
            (state_dir/name).unlink(missing_ok=True)
