"""Configuration shared by the CLI and desktop application."""
import math
from pathlib import Path

from .locations import obsidian_registry, require_separate
from .model import ConversionOptions
from .sync_storage import Conflict, read_json, save_json


def duration(value):
    value = float(value)
    if not math.isfinite(value) or not 0.1 <= value <= 60:
        raise ValueError('duration must be between 0.1 and 60 seconds')
    return value


def validate_config(root, config):
    require_separate(config['backup'], config['vault'], root)
    ConversionOptions(width_scale=config['width_scale'])
    duration(config['poll_seconds'])
    duration(config['settle_seconds'])
    if config['mode'] not in ('auto', 'manual'):
        raise ValueError('invalid registration mode')
    if config['backup_kind'] not in ('file', 'directory'):
        raise ValueError('invalid backup kind')
    return config


def load_config(root):
    path = Path(root)/'config.json'
    if not path.is_file():
        raise ValueError('configure backup and Vault first')
    return validate_config(root, read_json(path))


def load_state(root):
    path = Path(root)/'state.json'
    if not path.exists() and (Path(root)/'journal.json').exists():
        raise Conflict('recovery journal exists but state.json is missing')
    return read_json(path) if path.exists() else {'notes': {}}


def new_config(root, backup, vault, *, width_scale=0.5, mode='auto', poll=5.0,
               settle=3.0, registry=None):
    backup, vault = Path(backup).expanduser().resolve(), Path(vault).expanduser().resolve()
    if not backup.exists() or not vault.is_dir():
        raise ValueError('backup and selected Vault must already exist')
    if backup.is_file() and backup.suffix.lower() != '.sdocx':
        raise ValueError('single-file backup must be .sdocx')
    settings = (Path(registry) if registry else obsidian_registry()).resolve().parent
    if vault.is_relative_to(settings) or settings.is_relative_to(vault):
        raise ValueError('select an actual Vault, not the Obsidian settings directory')
    return validate_config(root, {'backup': str(backup), 'backup_kind': 'file' if backup.is_file() else 'directory',
                                 'vault': str(vault), 'mode': mode, 'width_scale': width_scale,
                                 'poll_seconds': poll, 'settle_seconds': settle})


def create_config(root, config):
    """Caller holds store_lock. Keep interrupted initialization recoverable."""
    root = Path(root)
    if any((root/name).exists() for name in ('config.json', 'state.json', 'journal.json')):
        raise Conflict('already configured; existing state is retained')
    validate_config(root, config)
    save_json(root/'config.json', config)
    # load_state supplies an empty state until the first synchronization saves it.


def update_config(root, config):
    """Caller holds store_lock; a desktop session cannot change the managed pair."""
    previous = load_config(root)
    for key in ('backup', 'vault', 'backup_kind'):
        if config[key] != previous[key]:
            raise Conflict('changing the registered backup/Vault pair is not supported')
    save_json(Path(root)/'config.json', validate_config(root, config))
