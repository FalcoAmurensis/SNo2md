"""Read Samsung Notes titles and category paths without changing the source DB."""
from pathlib import Path
from contextlib import closing
import shutil
import tempfile
import sqlite3
import zipfile
from .binary import note_header
from .decoder import text_object


def note_title(source):
    try:
        if source.is_dir():
            data = (source/'note.note').read_bytes()
        else:
            with zipfile.ZipFile(source) as archive:
                data = archive.read('note.note')
        return text_object(data, note_header(data)['title_object']).strip()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return ''


def read_catalog(backup, *, with_tree=False):
    """Optional LocalState database; unrelated standalone notes need no database."""
    root = Path(backup)
    candidates = [root/'LocalState/Storage.sqlite', root/'Storage.sqlite']
    if root.name == 'wdoc':
        candidates.append(root.parent/'Storage.sqlite')
    database = next((p for p in candidates if p.is_file()), None)
    if database is None:
        return ({}, None) if with_tree else {}
    # SQLite can create WAL/SHM files even with mode=ro. Query a private copy,
    # including committed WAL data, so discovery never writes into the backup.
    with tempfile.TemporaryDirectory(prefix='sno2md-catalog-') as temporary:
        snapshot = Path(temporary)/database.name
        sources = [database, database.with_name(database.name+'-wal')]
        def stamps():
            return [(p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None
                    for p in sources]
        before = stamps()
        for source, stamp in zip(sources, before):
            if stamp is not None:
                shutil.copyfile(source, Path(temporary)/source.name)
        if stamps() != before:
            raise ValueError('Samsung Notes catalog changed while reading; retry after backup finishes')
        with closing(sqlite3.connect(snapshot.as_uri()+'?mode=ro', uri=True, timeout=2)) as connection:
            connection.execute('BEGIN')
            folders = {uuid: (parent, name) for uuid, parent, name in connection.execute(
                'SELECT UUID, ParentUUID, DisplayName FROM CategoryTreeDB')}
            notes = list(connection.execute('SELECT UUID, Title, RecommendedTitle, CategoryUUID FROM NoteDB'))
    def category_path(uuid):
        result, seen = [], set()
        while uuid not in ('', None, 'uncategorized:///', 'UUID_UNCATEGORIZED', 'old:///'):
            if uuid in seen:
                raise ValueError('cycle in Samsung Notes category tree')
            seen.add(uuid)
            if uuid == 'trash:///':
                result.append('휴지통')
                break
            if uuid not in folders:
                raise ValueError(f'unknown Samsung Notes category: {uuid}')
            uuid, name = folders[uuid]
            if not name:
                raise ValueError('empty Samsung Notes category name')
            result.append(name)
        return list(reversed(result))
    wdoc = database.parent/'wdoc'
    catalog = {str((wdoc/uuid).resolve()): {'title': (title or recommended or '').strip(),
            'folders': category_path(category), 'category': category} for uuid, title, recommended, category in notes}

    if not with_tree:
        return catalog
    from .folder_selection import ROOT, UNCATEGORIZED, TRASH, category_id
    tree = {ROOT: {'parent': None, 'name': '전체 폴더'},
            UNCATEGORIZED: {'parent': ROOT, 'name': '미분류'},
            TRASH: {'parent': ROOT, 'name': '휴지통'}}
    for uuid, (parent, name) in folders.items():
        category_path(uuid)  # Validate empty folders as well as occupied ones.
        key = category_id(uuid)
        if key in (UNCATEGORIZED, TRASH):
            continue
        parent_key = category_id(parent)
        tree[key] = {'parent': ROOT if parent_key == UNCATEGORIZED else parent_key, 'name': name}
    return catalog, tree
