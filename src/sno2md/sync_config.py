"""Configuration shared by the CLI and desktop application."""
import math
import hashlib
import json
import os
from pathlib import Path

from .locations import obsidian_registry, require_separate
from .model import ConversionOptions
from .worker import worker_count
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
    worker_count(config.get('workers', 'auto'))
    timeout = config.get('wait_timeout', 300.0)
    if not math.isfinite(timeout) or not 0.1 <= timeout <= 3600:
        raise ValueError('wait_timeout must be between 0.1 and 3600 seconds')
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
    state = read_json(path) if path.exists() else {'notes': {}}
    config_path = Path(root)/'config.json'
    if not config_path.exists():
        return state
    pair = pair_key(read_json(config_path))
    if state.get('pair', pair) != pair:
        archive = Path(root)/'pairs'/f'{pair}.json'
        state = read_json(archive) if archive.exists() else {'notes': {}}
        if state.get('pair', pair) != pair:
            raise Conflict('saved synchronization history belongs to another backup/Vault')
    state['pair'] = pair
    return state


def pair_key(config):
    """Separate ownership histories when relative source names coincide."""
    paths = [os.path.normcase(str(Path(config[key]).expanduser().resolve()))
             for key in ('backup', 'vault')]
    return hashlib.sha256(json.dumps(paths).encode('utf-8')).hexdigest()


def change_paths(root, config, backup, vault):
    if pair_key(dict(config, backup=backup, vault=vault)) == pair_key(config):
        return dict(config)
    checked = new_config(root, backup, vault, width_scale=config['width_scale'])
    return dict(config, **{key: checked[key] for key in ('backup', 'vault', 'backup_kind')})


def new_config(root, backup, vault, *, width_scale=0.5, mode='auto', poll=5.0,
               settle=3.0, registry=None, workers='auto', wait_timeout=300.0):
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
                                 'poll_seconds': poll, 'settle_seconds': settle,
                                 'workers': worker_count(workers), 'wait_timeout': wait_timeout})


def create_config(root, config):
    """Caller holds store_lock. Keep interrupted initialization recoverable."""
    root = Path(root)
    if any((root/name).exists() for name in ('config.json', 'state.json', 'journal.json')):
        raise Conflict('already configured; existing state is retained')
    validate_config(root, config)
    save_json(root/'config.json', config)
    # load_state supplies an empty state until the first synchronization saves it.


def update_config(root, config):
    """Caller holds store_lock. Commit the selected pair with one config replace."""
    root = Path(root)
    previous = load_config(root)
    validate_config(root, config)
    if pair_key(config) != pair_key(previous):
        config = change_paths(root, config, config['backup'], config['vault'])
        from .sync_storage import recover
        state = load_state(root)
        if (root/'journal.json').exists():
            recover(root, previous, state)
        # Bind legacy state before committing the new config. An interruption
        # before/after config replacement selects the matching history.
        save_json(root/'state.json', state)
        archives = root/'pairs'
        archives.mkdir(exist_ok=True)
        save_json(archives/f'{pair_key(previous)}.json', state)
    save_json(root/'config.json', config)
