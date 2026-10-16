"""Automatic discovery, stable snapshots and serial synchronization using stdlib polling."""
import hashlib
from copy import deepcopy
from threading import Event
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from .api import export_note
from .locations import require_separate
from .model import ConversionOptions
from .sync_storage import Conflict, check_outputs, file_digest, publish_pair, recover, save_json


class InputChanged(OSError):
    pass


def note_folder(path):
    return (path/'note.note').is_file() and (path/'pageIdInfo.dat').is_file()


def fail_walk(error):
    raise error


def discover(root):
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f'backup unavailable: {root}')
    if root.is_file():
        if root.suffix.lower() != '.sdocx':
            raise ValueError('single-file input must be .sdocx')
        return {root.name: root}
    found = {}
    for directory, folders, files in os.walk(root, followlinks=False, onerror=fail_walk):
        path = Path(directory)
        folders[:] = sorted(name for name in folders if not (path/name).is_symlink())
        if note_folder(path):
            found[path.relative_to(root).as_posix()] = path
            folders[:] = []
        else:
            for name in sorted(files):
                file = path/name
                if file.suffix.lower() == '.sdocx' and not file.is_symlink():
                    found[file.relative_to(root).as_posix()] = file
    return dict(sorted(found.items()))


def source_path(config, key):
    root = Path(config['backup'])
    if config['backup_kind'] == 'file':
        if key != root.name:
            raise ValueError('invalid single-file registration')
        return root
    path = root/key
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('registered source escapes backup root')
    return path


def input_inventory(path):
    if path.is_symlink():
        raise ValueError(f'symlink source is unsupported: {path}')
    if path.is_file():
        files = [('', path)]
    elif path.is_dir():
        files = []
        for directory, folders, names in os.walk(path, followlinks=False, onerror=fail_walk):
            parent = Path(directory)
            for name in folders + names:
                if (parent/name).is_symlink():
                    raise ValueError(f'symlink in note: {parent/name}')
            files.extend(((parent/name).relative_to(path).as_posix(), parent/name) for name in names)
    else:
        raise FileNotFoundError(f'input missing: {path}')
    return sorted(files)


def fingerprint(path):
    inventory = input_inventory(path)
    before = [(name, file.stat().st_size, file.stat().st_mtime_ns) for name, file in inventory]
    digest = hashlib.sha256()
    for name, file in inventory:
        value = file_digest(file)
        if value is None:
            raise InputChanged(f'input disappeared: {file}')
        digest.update(name.encode('utf-8') + b'\0' + value.encode('ascii') + b'\n')
    after = [(name, file.stat().st_size, file.stat().st_mtime_ns) for name, file in input_inventory(path)]
    if before != after:
        raise InputChanged(f'input changed while reading: {path}')
    return digest.hexdigest()


def valid_name(name):
    if (not name or name.endswith((' ', '.')) or any(c in name for c in '/\\:*?"<>|')
            or any(ord(c) < 32 for c in name)):
        raise ValueError('invalid output filename stem')
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL', *(f'COM{i}' for i in range(1,10)),
                                     *(f'LPT{i}' for i in range(1,10))}:
        raise ValueError('reserved Windows filename')
    return name


def default_name(path):
    name = path.stem if path.is_file() else path.name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).rstrip(' .') or 'Note'
    try:
        return valid_name(name)
    except ValueError:
        return 'Note_'+name


def rename_entry(state, source, name):
    valid_name(name)
    entry = state['notes'][source]
    if any(key != source and other['name'].casefold() == name.casefold()
           for key, other in state['notes'].items()):
        raise Conflict('output name already registered')
    if entry['name'] == name:
        return
    # Prior outputs become unmanaged; they are never deleted or silently adopted.
    entry['name'] = name
    entry.pop('success', None)
    entry['status'], entry['error'] = 'pending', None


def rebind_entry(config, state, old, new):
    if old == new:
        return
    if new in state['notes'] and state['notes'][new].get('success'):
        raise Conflict('new source already has successful outputs; no automatic merging')
    source_path(config, new)
    found = discover(config['backup'])
    if old in found:
        raise ValueError('old source still exists; rebind is only for a moved/missing source')
    if new not in found:
        raise ValueError('new source is not a discovered note')
    state['notes'][new] = state['notes'].pop(old)
    state['notes'][new]['status'] = 'pending'


class Synchronizer:
    def __init__(self, root, config, state, emit=print, on_state=None):
        self.root, self.config, self.state = Path(root), config, state
        self.emit = emit
        self.on_state = on_state
        self.observed = {}
        self.refresh = set()
        self.retry_after = {}
        self.options = {'width_scale': config['width_scale']}

    def notify(self):
        if self.on_state is not None:
            self.on_state(deepcopy(self.state))

    def status(self, key, status, message=None):
        entry = self.state['notes'][key]
        if (entry.get('status'), entry.get('error')) != (status, message):
            entry['status'], entry['error'] = status, message
            save_json(self.root/'state.json', self.state)
            self.notify()
            self.emit(f'{status}: {key}' + (f' — {message}' if message else ''))

    def cycle(self, stop_event=None, only_sources=None):
        config, state = self.config, self.state
        require_separate(config['backup'], config['vault'], self.root)
        if not Path(config['vault']).is_dir():
            raise FileNotFoundError('Vault unavailable; it will not be recreated automatically')
        recover(self.root, config, state)
        found = discover(config['backup'])
        if config['mode'] == 'auto':
            for key, path in found.items():
                if key not in state['notes']:
                    state['notes'][key] = {'name': default_name(path), 'status': 'pending', 'error': None}
                    save_json(self.root/'state.json', state)
                    self.emit(f'registered: {key} -> {state["notes"][key]["name"]}')
        self.notify()
        for key, entry in list(state['notes'].items()):
            if stop_event is not None and stop_event.is_set():
                break
            if only_sources is not None and key not in only_sources:
                continue
            if key not in found:
                self.observed.pop(key, None)
                self.status(key, 'missing', 'source unavailable; existing outputs retained')
                continue
            # Duplicate names reserve neither the other note's files nor its ownership.
            owners = [k for k, e in state['notes'].items()
                      if e['name'].casefold() == entry['name'].casefold()]
            if len(owners) > 1:
                self.status(key, 'conflict', 'duplicate output name; use sync rename')
                continue
            try:
                source = source_path(config, key)
                value = fingerprint(source)
                now = time.monotonic()
                last = self.observed.get(key)
                if last is None or last[0] != value:
                    self.observed[key] = (value, now)
                    self.retry_after.pop(key, None)
                    self.status(key, 'waiting', 'waiting for input to settle')
                    continue
                if now - last[1] < config['settle_seconds']:
                    continue
                check_outputs(config, entry)
                success = entry.get('success', {})
                if key not in self.refresh and success.get('input') == value and success.get('options') == self.options:
                    self.status(key, 'synced')
                    continue
                if now < self.retry_after.get(key, 0):
                    continue
                self.status(key, 'converting')
                # Temporary snapshot is removed after each job, never an asset database.
                with tempfile.TemporaryDirectory(prefix='job-', dir=self.root) as temporary:
                    work = Path(temporary)
                    snapshot = work/('source.sdocx' if source.is_file() else 'source')
                    if source.is_file():
                        shutil.copyfile(source, snapshot)
                    else:
                        shutil.copytree(source, snapshot, symlinks=True)
                    if fingerprint(snapshot) != value or fingerprint(source) != value:
                        raise InputChanged('input changed during snapshot; will retry')
                    result = export_note(snapshot, work/'output', name=entry['name'],
                                         options=ConversionOptions(**self.options))
                    if fingerprint(source) != value:
                        raise InputChanged('input changed during conversion; will retry')
                    publish_pair(self.root, config, state, key, result.pdf.parent, value, self.options)
                self.refresh.discard(key)
                self.notify()
                self.emit(f'synced: {key} ({result.counts["pages"]} pages)')
            except InputChanged as error:
                self.observed.pop(key, None)
                self.status(key, 'waiting', str(error))
            except Exception as error:
                # Pending recovery must be resolved before another note can publish.
                if (self.root/'journal.json').exists():
                    raise
                self.retry_after[key] = time.monotonic() + 30
                self.status(key, 'conflict' if isinstance(error, Conflict) else 'error', str(error))

    def run(self, watch=False, stop_event=None, only_sources=None):
        stop_event = stop_event if stop_event is not None else Event()
        only_sources = None if only_sources is None else set(only_sources)
        while not stop_event.is_set():
            try:
                self.cycle(stop_event, only_sources)
            except OSError as error:
                if not watch or (self.root/'journal.json').exists():
                    raise
                self.emit(f'unavailable: {error}; will retry')
            if not watch:
                if not stop_event.wait(self.config['settle_seconds']):
                    self.cycle(stop_event, only_sources)
                return
            stop_event.wait(self.config['poll_seconds'])
