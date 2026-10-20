"""Automatic discovery, stable snapshots and serial synchronization using stdlib polling."""
import hashlib
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool
from copy import deepcopy
from threading import Event
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from .worker import ConversionJob, convert_job, choose_workers, initialize_worker
from .catalog import read_catalog, note_title
from .locations import require_separate
from .model import GENERATOR_VERSION
from .sync_storage import Conflict, check_outputs, file_digest, publish_pair, recover, save_json, store_lock


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
        if path.name == 'SPenSDK30' and path.parent.name == 'LocalState':
            folders[:] = []
            continue
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


def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).rstrip(' .') or 'Note'
    try:
        return valid_name(name)
    except ValueError:
        return 'Note_'+name


def default_name(path):
    return safe_name(note_title(path) or (path.stem if path.is_file() else path.name))


def registration(path, catalog, state):
    metadata = catalog.get(str(path.resolve()), {})
    name = safe_name(metadata.get('title') or note_title(path) or (path.stem if path.is_file() else path.name))
    folder = '/'.join(safe_name(part) for part in metadata.get('folders', []))
    used = {(e.get('folder', '').casefold(), e['name'].casefold()) for e in state['notes'].values()}
    base, suffix = name, 2
    while (folder.casefold(), name.casefold()) in used:
        name = f'{base} ({suffix})'
        suffix += 1
    return {'name': name, 'folder': folder, 'status': 'pending', 'error': None}


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
        self.metrics = {}

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

    def measured(self, label, function, *args):
        started = time.perf_counter()
        try:
            return function(*args)
        finally:
            self.metrics[label+'_seconds'] = self.metrics.get(label+'_seconds', 0) + time.perf_counter()-started
            self.metrics[label+'_calls'] = self.metrics.get(label+'_calls', 0) + 1

    def digest(self, source):
        # Keep content hashing at every consistency boundary, even when mtimes match.
        size = sum(p.stat().st_size for _, p in input_inventory(source))
        self.metrics['hash_bytes'] = self.metrics.get('hash_bytes', 0) + size
        return self.measured('fingerprint', fingerprint, source)

    def failure(self, key, error):
        if (self.root/'journal.json').exists():
            raise error
        if isinstance(error, InputChanged):
            self.observed.pop(key, None)
            self.status(key, 'waiting', str(error))
        else:
            self.retry_after[key] = time.monotonic() + 30
            self.status(key, 'conflict' if isinstance(error, Conflict) else 'error', str(error))

    def cleanup_jobs(self):
        # Called under the coordinator's state lock, only after journal recovery.
        for path in self.root.glob('job-*'):
            if path.is_symlink() or not path.is_dir():
                continue
            try:
                with store_lock(path):
                    pass
            except Conflict:
                continue  # An orphan worker still owns this private temporary job.
            shutil.rmtree(path)

    def snapshot(self, source, value, work):
        snapshot = work/('source.sdocx' if source.is_file() else 'source')
        started = time.perf_counter()
        if source.is_file():
            shutil.copyfile(source, snapshot)
        else:
            shutil.copytree(source, snapshot, symlinks=True)
        self.metrics['snapshot_seconds'] = self.metrics.get('snapshot_seconds', 0) + time.perf_counter()-started
        self.metrics['snapshot_bytes'] = self.metrics.get('snapshot_bytes', 0) + sum(p.stat().st_size for _, p in input_inventory(snapshot))
        if self.digest(snapshot) != value or self.digest(source) != value:
            raise InputChanged('snapshot mismatch: input changed during copy; will retry')
        return snapshot

    def convert_pending(self, candidates, stop_event):
        if not candidates or stop_event.is_set():
            return
        workers, available = choose_workers(self.config.get('workers', 'auto'), len(candidates))
        self.emit('pool: '+json.dumps({'workers': workers, 'available_bytes': available}))
        pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'),
                                   initializer=initialize_worker)
        pending = {}
        remaining = iter(candidates)
        exhausted = False
        try:
            while pending or not exhausted:
                # No more than workers unfinished futures/snapshots, including startup.
                while not exhausted and not stop_event.is_set() and len(pending) < workers:
                    item = next(remaining, None)
                    if item is None:
                        exhausted = True
                        break
                    key, source, value = item
                    work = Path(tempfile.mkdtemp(prefix='job-', dir=self.root))
                    try:
                        self.status(key, 'queued')
                        snapshot = self.snapshot(source, value, work)
                        if stop_event.is_set():
                            self.status(key, 'pending', 'stopped before submission')
                            shutil.rmtree(work)
                            break
                        job = ConversionJob(str(snapshot), str(work/'output'), self.state['notes'][key]['name'],
                                            self.config['width_scale'], time.perf_counter())
                        future = pool.submit(convert_job, job)
                        pending[future] = (key, source, value, work)
                        self.status(key, 'converting')
                    except BrokenProcessPool:
                        shutil.rmtree(work)
                        raise
                    except Exception as error:
                        shutil.rmtree(work)
                        self.failure(key, error)
                if not pending:
                    break
                done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    self.status(pending[future][0], 'ready_to_publish')
                for future in done:
                    key, source, value, work = pending.pop(future)
                    try:
                        result = future.result()
                        self.emit('conversion: '+json.dumps(dict(source=key, **{k:v for k,v in result.items() if k != 'output'})))
                        if self.digest(source) != value:
                            raise InputChanged('input changed during conversion; will retry')
                        # publish_pair repeats ownership/user-edit checks immediately before writing.
                        self.measured('publish', publish_pair, self.root, self.config, self.state,
                                      key, Path(result['output']), value, self.options)
                        self.refresh.discard(key)
                        self.notify()
                        self.emit(f'synced: {key} ({result["counts"]["pages"]} pages)')
                    except BrokenProcessPool:
                        self.status(key, 'error', 'conversion worker exited unexpectedly; retry with fewer workers')
                        raise
                    except Exception as error:
                        self.failure(key, error)
                    finally:
                        shutil.rmtree(work)
                if stop_event.is_set():
                    exhausted = True  # Finish and publish already submitted jobs; submit no more.
                    for key, _, _ in candidates:
                        if self.state['notes'][key]['status'] == 'queued':
                            self.status(key, 'pending', 'stopped; no new work will start')
        finally:
            # Also drain on fatal publication errors, but never publish further results.
            for future in pending:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
            for key, _, _, work in pending.values():
                self.status(key, 'error', 'batch interrupted; retry after recovery')
                shutil.rmtree(work)

    def cycle(self, stop_event=None, only_sources=None):
        stop_event = stop_event if stop_event is not None else Event()
        self.metrics = {}
        started = time.perf_counter()
        config, state = self.config, self.state
        require_separate(config['backup'], config['vault'], self.root)
        if not Path(config['vault']).is_dir():
            raise FileNotFoundError('Vault unavailable; it will not be recreated automatically')
        recover(self.root, config, state)
        self.cleanup_jobs()
        found = self.measured('discover', discover, config['backup'])
        if config['mode'] == 'auto':
            catalog = read_catalog(config['backup']) if any(k not in state['notes'] for k in found) else {}
            for key, path in found.items():
                if key not in state['notes']:
                    state['notes'][key] = registration(path, catalog, state)
                    save_json(self.root/'state.json', state)
                    self.emit(f'registered: {key} -> {state["notes"][key]["name"]}')
        self.notify()
        candidates = []
        for key, entry in list(state['notes'].items()):
            if stop_event.is_set():
                break
            if only_sources is not None and key not in only_sources:
                continue
            if key not in found:
                self.observed.pop(key, None)
                self.status(key, 'missing', 'source unavailable; existing outputs retained')
                continue
            owners = [k for k, e in state['notes'].items()
                      if (e.get('folder', '').casefold(), e['name'].casefold()) ==
                      (entry.get('folder', '').casefold(), entry['name'].casefold())]
            if len(owners) > 1:
                self.status(key, 'conflict', 'duplicate output name; use sync rename')
                continue
            try:
                source = source_path(config, key)
                value = self.digest(source)
                now = time.monotonic()
                last = self.observed.get(key)
                if last is None or last[0] != value:
                    self.observed[key] = (value, now)
                    self.retry_after.pop(key, None)
                    self.status(key, 'waiting', 'first observation; waiting for input to settle' if last is None
                                else 'input content changed; waiting for input to settle')
                    continue
                if now - last[1] < config['settle_seconds']:
                    self.status(key, 'waiting', 'stability interval not reached')
                    continue
                self.measured('check_outputs', check_outputs, config, entry)
                success = entry.get('success', {})
                if (key not in self.refresh and success.get('input') == value and
                        success.get('options') == self.options and
                        success.get('generator_version') == GENERATOR_VERSION):
                    self.status(key, 'synced')
                    continue
                if now < self.retry_after.get(key, 0):
                    continue
                candidates.append((key, source, value))
                self.status(key, 'queued')
            except Exception as error:
                self.failure(key, error)
        try:
            self.convert_pending(candidates, stop_event)
        finally:
            for key, _, _ in candidates:
                if state['notes'][key]['status'] == 'queued':
                    self.status(key, 'pending', 'not submitted; resume synchronization to continue')
            self.metrics['cycle_seconds'] = time.perf_counter()-started
            self.emit('timing: '+json.dumps(self.metrics))

    def run(self, watch=False, stop_event=None, only_sources=None):
        stop_event = stop_event if stop_event is not None else Event()
        only_sources = None if only_sources is None else set(only_sources)
        waiting_seconds = 0.0
        while not stop_event.is_set():
            try:
                self.cycle(stop_event, only_sources)
            except OSError as error:
                if not watch or (self.root/'journal.json').exists():
                    raise
                self.emit(f'unavailable: {error}; will retry')
            if not watch:
                waiting = [key for key, e in self.state['notes'].items()
                           if (only_sources is None or key in only_sources) and e['status'] == 'waiting']
                if not waiting or stop_event.is_set():
                    return
                timeout = self.config.get('wait_timeout', 300.0)
                if waiting_seconds >= timeout:
                    for key in waiting:
                        self.status(key, 'waiting', 'input stability wait timed out; run again after backup finishes')
                    return
                delay = min(self.config['settle_seconds'], timeout-waiting_seconds)
                stop_event.wait(delay)
                waiting_seconds += delay
            else:
                stop_event.wait(self.config['poll_seconds'])
