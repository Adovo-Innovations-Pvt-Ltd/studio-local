"""Read a key from the user's terminal, never from chat or command arguments."""
import getpass
from pathlib import Path
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'server'))
from storage import data_directory, save_key


def main():
    if sys.argv[1:] == ['--remove']:
        save_key(data_directory(), '')
        print('Saved API key removed. History and outputs preserved.')
        return
    if sys.argv[1:]:
        raise ValueError('Use setup-key without any key in the command arguments.')
    if not sys.stdin.isatty():
        raise ValueError('Run setup-key directly in Terminal. Piped input and chat key entry are not accepted.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        value = getpass.getpass('OpenAI API key (hidden): ')
    if not value.strip():
        print('No key entered; existing key unchanged.')
        return
    save_key(data_directory(), value)
    print('API key saved locally with owner-only permissions. No request sent to OpenAI.')


if __name__ == '__main__':
    try:
        main()
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise SystemExit('Key setup cancelled; no unmasked input accepted.')
    except (OSError, ValueError):
        raise SystemExit('Key setup failed. Use a dedicated writable data directory and a valid sk- key; never paste it into chat.')
