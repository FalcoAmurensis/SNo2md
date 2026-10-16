"""Discover location candidates without changing Samsung Notes or Obsidian settings."""
import json
import os
from pathlib import Path, PureWindowsPath
import sys


PACKAGE = 'SAMSUNGELECTRONICSCoLtd.SamsungNotes_'


def state_directory():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'SNo2md'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'sno2md'


def windows_path(value):
    """Translate registry drive paths only when explicitly using Windows data in WSL."""
    path = PureWindowsPath(value)
    if os.name != 'nt' and path.drive:
        if not os.environ.get('WSL_DISTRO_NAME') or len(path.drive) != 2:
            raise ValueError('Windows Vault path requires Windows or WSL; pass --vault explicitly')
        return Path('/mnt')/path.drive[0].lower()/Path(*path.parts[1:])
    return Path(value).expanduser()


def samsung_locations(windows_profile=None):
    if windows_profile:
        local = Path(windows_profile).expanduser()/'AppData/Local'
    elif os.name == 'nt':
        local = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))
    else:
        return []
    # sdoc/wdoc contents are discovered by structure; no SQLite schema assumptions.
    return sorted(p.resolve() for p in (local/'Packages').glob(PACKAGE+'*/LocalState') if p.is_dir())


def obsidian_registry(windows_profile=None):
    if windows_profile:
        return Path(windows_profile).expanduser()/'AppData/Roaming/obsidian/obsidian.json'
    if os.name == 'nt':
        return Path(os.environ.get('APPDATA', Path.home()/'AppData/Roaming'))/'obsidian/obsidian.json'
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/obsidian/obsidian.json'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'obsidian/obsidian.json'


def obsidian_vaults(registry=None, windows_profile=None):
    registry = Path(registry) if registry else obsidian_registry(windows_profile)
    if not registry.is_file():
        return []
    data = json.loads(registry.read_text(encoding='utf-8-sig'))
    vaults = data.get('vaults')
    if not isinstance(vaults, dict):
        raise ValueError(f'unrecognized Obsidian vault registry: {registry}; pass --vault explicitly')
    paths = set()
    for item in vaults.values():
        if isinstance(item, dict) and isinstance(item.get('path'), str):
            path = windows_path(item['path']).resolve()
            if path.is_dir():
                paths.add(path)
    return sorted(paths)


def require_separate(*paths):
    resolved = [Path(p).resolve() for p in paths]
    for index, first in enumerate(resolved):
        for second in resolved[index+1:]:
            if first.is_relative_to(second) or second.is_relative_to(first):
                raise ValueError(f'backup, Vault and state paths must not overlap: {first}, {second}')
