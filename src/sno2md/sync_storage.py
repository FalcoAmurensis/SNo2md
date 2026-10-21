"""Small operational state and recoverable publication of managed PDF/MD pairs."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from .model import GENERATOR_VERSION
from .markdown_regions import split, digest


class Conflict(ValueError):
    pass


class MissingOutput(Conflict):
    """Previously managed output is absent."""
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def file_digest(path):
    path = Path(path)
    if path.is_symlink():
        raise Conflict(f'symlink is not a managed file: {path}')
    if not path.exists():
        return None
    if not path.is_file():
        raise Conflict(f'expected a regular file: {path}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def store_lock(root):
    """OS lock survives neither crashes nor process exit; lock file is reusable."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root/'sync.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise Conflict('another sync command is running; stop watch before changing settings') from error
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def output_paths(config, entry):
    name = entry['name']
    if not name or name in ('.', '..') or any(c in name for c in '/\\:*?"<>|'):
        raise ValueError('invalid registered output name')
    vault = Path(config['vault']).resolve()
    folder = entry.get('folder', '')
    parts = folder.split('/') if folder else []
    parent = vault
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith((' ', '.'))
                or any(ord(c) < 32 or c in '\\:*?\"<>|' for c in part)):
            raise ValueError('invalid registered output folder')
        parent = parent/part
        if parent.is_symlink() or not parent.resolve().is_relative_to(vault):
            raise Conflict('output folder escapes Vault or is a symlink')
    return {ext: parent/(name+'.'+ext) for ext in ('pdf', 'md')}


def inspect_outputs(config, entry):
    """Collect absence and conflicts independently; retain bytes only within this check."""
    result = {'hashes': {}, 'missing': [], 'conflicts': {}, 'md_parts': None}
    expected = entry.get('success', {}).get('outputs', {})
    for ext, path in output_paths(config, entry).items():
        try:
            current = file_digest(path)
            result['hashes'][ext] = current
            recorded = expected.get(ext)
            if current is None:
                if recorded is not None:
                    result['missing'].append(ext)
                continue
            if ext == 'md':
                data = path.read_bytes()
                if digest(data) != current:
                    raise Conflict(f'output changed while reading: {path}')
                try:
                    result['md_parts'] = split(data)
                except ValueError:
                    pass
                managed = entry.get('success', {}).get('md_managed_hash')
                if managed:
                    if result['md_parts'] is None:
                        raise Conflict('Markdown boundaries damaged; preserve the complete Vault copy before resuming')
                    if digest(result['md_parts'][0]) != managed:
                        raise Conflict('Markdown generated region changed')
                    continue
            if current != recorded:
                raise Conflict(f'output changed or occupied: {path}')
        except Conflict as error:
            result['conflicts'][ext] = str(error)
    return result


def check_outputs(config, entry, allow_missing=False, resolution=None):
    return validate_outputs(inspect_outputs(config, entry), entry, allow_missing, resolution)


def validate_outputs(result, entry, allow_missing=False, resolution=None):
    conflicts = dict(result['conflicts'])
    if resolution is not None:
        if result['hashes'].get('md') != resolution['hash']:
            raise Conflict('Vault Markdown changed since confirmation; confirm again')
        if resolution['choice'] == 'use-source' and (not result['md_parts'] or
                not entry.get('success', {}).get('md_managed_hash')):
            raise Conflict('cannot identify user region; preserve the complete Vault copy instead')
        conflicts.pop('md', None)
    if conflicts:
        raise Conflict('; '.join(conflicts.values()))
    missing = set(result['missing'])
    permitted = {'pdf', 'md'} if allow_missing is True else (set(allow_missing) if allow_missing else set())
    if missing - permitted:
        raise MissingOutput(f'managed output missing: {", ".join(sorted(missing))}')
    return missing


def recover(root, config, state):
    """Roll back an uncommitted pair; retain externally edited files for manual recovery."""
    journal_path = root/'journal.json'
    if not journal_path.exists():
        return
    journal = read_json(journal_path)
    entry = state['notes'][journal['source']]
    stage = Path(journal['stage'])
    vault = Path(config['vault']).resolve()
    if stage.parent.resolve() != vault or not stage.name.startswith('.sno2md-sync-'):
        raise Conflict('invalid recovery stage location')
    if journal['name'] != entry['name'] or journal.get('folder', '') != entry.get('folder', ''):
        raise Conflict('output name changed during pending recovery')
    for ext, path in output_paths(config, entry).items():
        held = stage/'held'/path.name
        if held.exists() and file_digest(held) != journal['old'].get(ext):
            raise Conflict(f'concurrent edit retained at {held}; resolve it before retrying')
    if entry.get('success') != journal['success']:
        paths = output_paths(config, entry)
        # Inspect every file first so an external edit never causes partial rollback.
        for ext, path in paths.items():
            before, after = journal['old'].get(ext), journal['success']['outputs'][ext]
            current = file_digest(path)
            if current not in (before, after, None):
                raise Conflict(f'recovery blocked by edited output: {path}; backups retained at {stage}')
            if before is None and current is not None and not os.path.samefile(path, stage/'new'/path.name):
                raise Conflict(f'recovery blocked by newly occupied output: {path}; backups retained at {stage}')
            held = stage/'held'/path.name
            if held.exists() and file_digest(held) != before:
                raise Conflict(f'concurrent edit retained at {held}; resolve it before retrying')
            if before and current != before and file_digest(stage/'old'/path.name) != before:
                raise Conflict(f'missing/corrupt recovery copy: {stage/"old"/path.name}')
        for ext, path in paths.items():
            before, after = journal['old'].get(ext), journal['success']['outputs'][ext]
            current = file_digest(path)
            if current == before:
                continue
            if current == after:
                if before is None and not os.path.samefile(path, stage/'new'/path.name):
                    raise Conflict(f'output occupied during rollback: {path}')
                path.unlink()
            elif current is not None:
                raise Conflict(f'output changed during rollback: {path}')
            if before:
                os.link(stage/'old'/path.name, path)
    # Once committed or rolled back, the journal no longer protects any output.
    journal_path.unlink()
    shutil.rmtree(stage)


def publish_pair(root, config, state, source, generated, fingerprint, options, allow_missing=False, resolution=None):
    entry = state['notes'][source]
    if entry.get('sync_policy', 'managed') == 'retired':
        raise Conflict('document management is stopped')
    if resolution is not None:
        from .sync_config import pair_key
        if resolution['pair'] != pair_key(config):
            raise Conflict('backup/Vault selection changed since confirmation')
    inspection = inspect_outputs(config, entry)
    missing = validate_outputs(inspection, entry, allow_missing, resolution)
    if isinstance(allow_missing, (set, frozenset)) and missing != allow_missing:
        raise Conflict('missing output paths changed during conversion; retry explicitly')
    paths = output_paths(config, entry)
    paths['pdf'].parent.mkdir(parents=True, exist_ok=True)
    old = dict(inspection['hashes'])
    for ext in missing:
        old[ext] = None  # Rollback must restore absence, not resurrect a deleted file.
    stage = Path(tempfile.mkdtemp(prefix='.sno2md-sync-', dir=config['vault']))
    journal_path = root/'journal.json'
    previous = entry.get('success')
    try:
        for folder in ('new', 'old', 'held'):
            (stage/folder).mkdir()
        hashes = {}
        for ext, path in paths.items():
            new = stage/'new'/path.name
            shutil.copyfile(generated/path.name, new)
            if ext == 'md':
                data = new.read_bytes()
                try:
                    managed, user = split(data)
                except ValueError as error:
                    raise Conflict('new Markdown has invalid generated boundaries') from error
                current_parts = inspection['md_parts']
                trusted = entry.get('success', {}).get('md_managed_hash')
                if current_parts and trusted:
                    user = current_parts[1]
                new.write_bytes(managed + user)
            with new.open('r+b') as stream:
                os.fsync(stream.fileno())
            hashes[ext] = file_digest(new)
            if old.get(ext):
                shutil.copyfile(path, stage/'old'/path.name)
                with (stage/'old'/path.name).open('r+b') as stream:
                    os.fsync(stream.fileno())
                if file_digest(stage/'old'/path.name) != old[ext]:
                    raise Conflict(f'output changed while copying: {path}')
        from datetime import datetime, timezone
        success = {'revision': (previous or {}).get('revision', 0)+1,
                   'generator_version': GENERATOR_VERSION,
                   'input': fingerprint, 'options': options, 'outputs': hashes,
                   'at': datetime.now(timezone.utc).isoformat()}
        success['md_managed_hash'] = digest(split((stage/'new'/paths['md'].name).read_bytes())[0])
        journal = {'source': source, 'name': entry['name'], 'folder': entry.get('folder', ''), 'stage': str(stage),
                   'old': old, 'success': success}
        save_json(journal_path, journal)
        for ext, path in paths.items():
            if file_digest(path) != old.get(ext):
                raise Conflict(f'output changed before replacement: {path}')
            if old.get(ext):
                os.replace(path, stage/'held'/path.name)
                if file_digest(stage/'held'/path.name) != old[ext]:
                    raise Conflict(f'concurrent edit retained: {stage/"held"/path.name}')
            # No overwrite if another process creates the path while it is vacant.
            os.link(stage/'new'/path.name, path)
        if any(file_digest(paths[ext]) != digest for ext, digest in hashes.items()):
            raise Conflict('output changed during publication')
        for ext, path in paths.items():
            if old.get(ext) and file_digest(stage/'held'/path.name) != old[ext]:
                raise Conflict(f'concurrent edit retained: {stage/"held"/path.name}')
        entry['success'] = success
        entry['status'], entry['error'] = 'synced', None
        try:
            save_json(root/'state.json', state)
        except BaseException:
            if previous is None:
                entry.pop('success', None)
            else:
                entry['success'] = previous
            raise
        recover(root, config, state)
    except BaseException:
        if journal_path.exists():
            recover(root, config, state)
        raise
    finally:
        if not journal_path.exists() and stage.exists():
            shutil.rmtree(stage)
