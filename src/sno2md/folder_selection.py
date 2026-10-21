"""Folder membership and inherited selection rules, shared by GUI and sync."""
from copy import deepcopy
from pathlib import Path
import os

ROOT = 'root'
UNCATEGORIZED = 'category:uncategorized'
TRASH = 'category:trash'


def category_id(value):
    if value in ('', None, 'uncategorized:///', 'UUID_UNCATEGORIZED', 'old:///'):
        return UNCATEGORIZED
    return TRASH if value == 'trash:///' else 'category:' + value


def default_rules():
    return {ROOT: True, TRASH: False}


def selection_for(config):
    from .sync_config import pair_key
    return deepcopy(config.get('folder_selections', {}).get(pair_key(config), default_rules()))


def enabled(tree, rules, key):
    seen = set()
    while key is not None:
        if key in seen:
            raise ValueError('cycle in folder tree')
        seen.add(key)
        if key in rules:
            return rules[key]
        key = tree.get(key, {}).get('parent', ROOT if key != ROOT else None)
    return True


def descendants(tree, key):
    result = {key}
    pending = [key]
    while pending:
        parent = pending.pop()
        children = {k for k, node in tree.items() if node['parent'] == parent} - result
        result.update(children)
        pending.extend(children)
    return result


def set_checked(tree, rules, key, value):
    for child in descendants(tree, key):
        rules.pop(child, None)
    rules[key] = value


def check_state(tree, rules, key):
    values = {enabled(tree, rules, item) for item in descendants(tree, key)}
    return 'partial' if len(values) > 1 else ('checked' if True in values else 'unchecked')


def summary(rules):
    base = rules.get(ROOT, True)
    overrides = {key: value for key, value in rules.items() if key != ROOT}
    if not base:
        return '선택한 범위만 가져오기' if any(overrides.values()) else '가져올 폴더 없음'
    if not overrides:
        return '모든 폴더 (휴지통 포함)'
    if overrides == {TRASH: False}:
        return '모든 폴더 (휴지통 제외)'
    return '전체 폴더 · 일부 제외' if not all(overrides.values()) else '모든 폴더 (휴지통 포함)'


def inventory(backup, found=None):
    from .catalog import read_catalog
    from .sync import discover
    root = Path(backup)
    found = discover(root) if found is None else found
    catalog, tree = read_catalog(root, with_tree=True)
    membership = {}
    if tree is not None:
        for key, path in found.items():
            membership[key] = category_id(catalog.get(str(path.resolve()), {}).get('category'))
        return {'tree': tree, 'membership': membership, 'catalog': catalog, 'basis': '삼성노트 분류 폴더 기준'}
    tree = {ROOT: {'parent': None, 'name': root.name if root.is_file() else '전체 폴더'}}
    if root.is_dir():
        from .sync import note_folder, fail_walk
        for directory, folders, _ in os.walk(root, followlinks=False, onerror=fail_walk):
            path = Path(directory)
            if note_folder(path) or (path.name == 'SPenSDK30' and path.parent.name == 'LocalState'):
                folders[:] = []
                continue
            folders[:] = sorted(name for name in folders if not (path/name).is_symlink())
            if path != root:
                relative = path.relative_to(root)
                parent = 'directory:' + relative.parent.as_posix() if relative.parent != Path('.') else ROOT
                tree['directory:' + relative.as_posix()] = {'parent': parent, 'name': path.name}
    for key, path in found.items():
        # Unpacked note roots are documents, not category directories.
        relative = path.parent.relative_to(root) if root.is_dir() and path != root else Path('.')
        parent = ROOT
        for index in range(len(relative.parts)):
            part = Path(*relative.parts[:index+1]).as_posix()
            node = 'directory:' + part
            tree[node] = {'parent': parent, 'name': relative.parts[index]}
            parent = node
        membership[key] = parent
    return {'tree': tree, 'membership': membership, 'catalog': catalog, 'basis': '파일시스템 폴더 기준'}
