"""Opt-in, read-only path diagnostics. Default reports contain no path names."""
import locale
import os
from pathlib import Path
import platform
import tempfile
import unicodedata

from . import __version__
from .sync import Synchronizer, discover, source_path, fingerprint
from .sync_config import load_config
from .catalog import read_catalog
from .decoder import read_note


def diagnose(root, config, keys=(), selected_paths=None, stop_event=None, tk_version=None):
    report = {'environment': {'os': platform.system(), 'os_version': platform.version(),
                              'python': platform.python_version(), 'app': __version__,
                              'locale': locale.getlocale(), 'tk': tk_version}, 'checks': [], 'raw': []}
    paths = []
    reloaded = load_config(root)
    for name in ('backup', 'vault'):
        paths.append((name, config[name], {'settings_reload_equal': config[name] == reloaded[name]}))
    for kind, value in (selected_paths or {}).items():
        name = 'vault' if kind == 'vault' else 'backup'
        paths.append(('dialog', value, {'settings_equal': value == config[name]}))
    for number, (role, value, extra) in enumerate(paths, 1):
        item = {'path_id': number, 'role': role, 'NFC': unicodedata.normalize('NFC', value) == value,
                'NFD': unicodedata.normalize('NFD', value) == value, **extra}
        report['checks'].append(item)
        report['raw'].append({'path_id': number, 'path': value})
        try:
            path = Path(value)
            item.update(exists=path.exists(), is_file=path.is_file(), is_dir=path.is_dir())
            if path.is_dir():
                with os.scandir(path) as entries: next(entries, None)
            elif path.is_file():
                with path.open('rb') as stream: stream.read(1)
            else:
                raise FileNotFoundError()
            item['access'] = True
        except OSError as error:
            item.update(access=False, failed_stage='path_access', error_type=type(error).__name__)
    try:
        found = discover(config['backup'])
        catalog = read_catalog(config['backup'])
    except Exception as error:
        report['checks'].append({'failed_stage': 'discovery_or_catalog', 'error_type': type(error).__name__})
        report['raw'].append({'error': str(error)})
        return report
    engine = Synchronizer(root, config, {'notes': {}}, emit=lambda _: None)
    for key in sorted(keys):
        if stop_event is not None and stop_event.is_set(): break
        path = source_path(config, key)
        item = {'path_id': len(report['raw'])+1, 'role': 'document',
                'NFC': unicodedata.normalize('NFC', str(path)) == str(path),
                'NFD': unicodedata.normalize('NFD', str(path)) == str(path),
                'discovered_key_equal': key in found,
                'source_path_equal': key in found and found[key] == path,
                'catalog_match': str(path.resolve()) in catalog}
        report['checks'].append(item)
        report['raw'].append({'path_id': item['path_id'], 'source': key, 'path': str(path)})
        stage = 'fingerprint'
        try:
            value = fingerprint(path)
            stage = 'snapshot'
            with tempfile.TemporaryDirectory(prefix='sno2md-path-check-') as temporary:
                snapshot = engine.snapshot(path, value, Path(temporary))
                item['snapshot_equal'] = fingerprint(snapshot) == value
                if stop_event is not None and stop_event.is_set():
                    item['stopped'] = True; break
                stage = 'decode'
                note = read_note(snapshot)
                item.update(decoded=True, pages=len(note.pages))
        except Exception as error:
            if stage == 'decode' and str(error) == 'empty, missing, duplicate, or unlisted note pages':
                stage = 'page_integrity'
            item.update(failed_stage=stage, error_type=type(error).__name__)
            report['raw'][-1]['error'] = str(error)
    return report
