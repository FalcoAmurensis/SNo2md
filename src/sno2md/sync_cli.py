"""Phase 3 CLI. All persistent paths are selected explicitly during setup."""
import argparse
from pathlib import Path
import sys
import signal
from threading import Event
from concurrent.futures.process import BrokenProcessPool
from .worker import worker_count

from .locations import (obsidian_registry, obsidian_vaults,
                        samsung_locations, state_directory)
from .model import ConversionOptions
from .sync_config import duration, load_config, load_state, new_config, create_config, update_config, change_paths
from .sync import Synchronizer, default_name, discover, rebind_entry, rename_entry, registration
from .catalog import read_catalog
from .sync_storage import recover, save_json, store_lock


def choose(label, candidates):
    for index, path in enumerate(candidates, 1):
        print(f'  {index}. {path}')
    if not sys.stdin.isatty():
        raise ValueError(f'{label}: specify the path with a CLI option in noninteractive mode')
    answer = input(f'{label} — number or full path: ').strip()
    if answer.isdecimal() and 1 <= int(answer) <= len(candidates):
        return candidates[int(answer)-1]
    if not answer:
        raise ValueError('no path selected')
    return Path(answer).expanduser()


def setup(root, args):
    if any((root/name).exists() for name in ('config.json','state.json','journal.json')):
        raise ValueError('already configured; use settings, or a separate --state-dir for another backup/Vault')
    backup = args.backup or choose('Backup path', samsung_locations(args.windows_profile))
    vault = args.vault or choose('Obsidian Vault', obsidian_vaults(args.obsidian_config, args.windows_profile))
    config = new_config(root, backup, vault, width_scale=args.width_scale, mode=args.mode,
                        poll=args.poll, settle=args.settle, workers=args.workers, wait_timeout=args.wait_timeout,
                        registry=args.obsidian_config or obsidian_registry(args.windows_profile))
    with store_lock(root):
        create_config(root, config)
    backup, vault = config['backup'], config['vault']
    print(f'Saved: {root/"config.json"}\nBackup: {backup}\nDefault Vault: {vault}\nMode: {args.mode}')
    print('Next: sno2md sync scan, then sno2md sync run or sno2md sync watch')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sno2md sync', description='Sync Samsung Notes to the selected Vault.')
    parser.add_argument('--state-dir', type=Path, default=state_directory(), help='Operational settings/state outside backup and Vault')
    commands = parser.add_subparsers(dest='command', required=True)
    locations = commands.add_parser('locations', help='List known backup and Obsidian Vault candidates')
    setup_parser = commands.add_parser('setup', help='Select and persist default backup/Vault paths')
    for command in (locations, setup_parser):
        command.add_argument('--windows-profile', type=Path, help='Explicit Windows profile, e.g. /mnt/c/Users/name in WSL')
        command.add_argument('--obsidian-config', type=Path, help='Explicit obsidian.json path')
    setup_parser.add_argument('--backup', type=Path)
    setup_parser.add_argument('--vault', type=Path)
    setup_parser.add_argument('--mode', choices=('auto','manual'), default='auto')
    setup_parser.add_argument('--width-scale', type=float, default=0.5)
    setup_parser.add_argument('--poll', type=duration, default=5.0)
    setup_parser.add_argument('--settle', type=duration, default=3.0)
    setup_parser.add_argument('--workers', type=worker_count, default='auto')
    setup_parser.add_argument('--wait-timeout', type=float, default=300.0)
    commands.add_parser('scan', help='Read-only discovery of supported archives/note folders')
    commands.add_parser('status', help='Show registered notes and last known status')
    for name in ('run', 'watch'):
        command = commands.add_parser(name, help='Sync once' if name == 'run' else 'Poll and sync until Ctrl+C')
        command.add_argument('--workers', type=worker_count, help='Override auto or 1..8 for this run')
        command.add_argument('--refresh', action='store_true', help='Rebuild registered notes once even if inputs/options are unchanged')
        if name == 'run':
            command.add_argument('--restore-missing', metavar='SOURCE', nargs='+', help='Explicitly restore missing outputs of these registered source keys only')
    settings = commands.add_parser('settings', help='Read/change mode, width and polling; stop watch first')
    settings.add_argument('--backup', type=Path)
    settings.add_argument('--vault', type=Path)
    settings.add_argument('--mode', choices=('auto','manual'))
    settings.add_argument('--width-scale', type=float)
    settings.add_argument('--poll', type=duration)
    settings.add_argument('--settle', type=duration)
    settings.add_argument('--workers', type=worker_count)
    settings.add_argument('--wait-timeout', type=float)
    add = commands.add_parser('add', help='Register selected source keys in manual mode')
    add.add_argument('sources', nargs='*')
    rename = commands.add_parser('rename', help='Change output name; existing outputs are retained')
    rename.add_argument('source', help='Relative source key from scan/status')
    rename.add_argument('name')
    rebind = commands.add_parser('rebind', help='Reconnect a moved source to its existing output pair')
    rebind.add_argument('old_source')
    rebind.add_argument('new_source')
    commands.add_parser('recover', help='Recover an interrupted pair before resuming')
    args = parser.parse_args(argv)
    root = args.state_dir.expanduser().resolve()
    try:
        if args.command == 'locations':
            print('Samsung Notes candidates:')
            for path in samsung_locations(args.windows_profile): print(f'  {path}')
            print(f'Obsidian registry: {args.obsidian_config or obsidian_registry(args.windows_profile)}')
            for path in obsidian_vaults(args.obsidian_config, args.windows_profile): print(f'  {path}')
            return
        if args.command == 'setup':
            setup(root, args)
            return
        config = load_config(root)
        state = load_state(root)
        if args.command == 'status':
            from .folder_selection import selection_for, summary
            print('Folders: ' + summary(selection_for(config)))
            print(f'Backup: {config["backup"]}\nVault: {config["vault"]}\nMode: {config["mode"]}')
            for key, entry in state['notes'].items():
                print(f'{entry["status"]}: {key} -> {entry["name"]}' + (f' — {entry["error"]}' if entry.get('error') else ''))
            if (root/'journal.json').exists(): print(f'Recovery pending: {root/"journal.json"}')
            return
        if args.command == 'scan':
            found = discover(config['backup'])
            for key in found: print(key)
            if not found: print('No supported .sdocx or unpacked note roots found; SQLite and legacy .sdoc/.wdoc files are not parsed.')
            return
        with store_lock(root):
            config = load_config(root)
            state = load_state(root)
            if args.command != 'settings' and not Path(config['vault']).is_dir():
                raise FileNotFoundError('Vault unavailable; no outputs or recovery files changed')
            recover(root, config, state)
            if args.command == 'settings':
                if args.backup is not None or args.vault is not None:
                    config = change_paths(root, config, args.backup or config['backup'], args.vault or config['vault'])
                for argument, key in (('mode','mode'), ('width_scale','width_scale'),
                                      ('poll','poll_seconds'), ('settle','settle_seconds'),
                                      ('workers','workers'), ('wait_timeout','wait_timeout')):
                    if getattr(args, argument) is not None: config[key] = getattr(args, argument)
                ConversionOptions(width_scale=config['width_scale'])
                update_config(root, config)
                print(config)
            elif args.command == 'add':
                found = discover(config['backup'])
                selected = args.sources
                if not selected:
                    keys = list(found)
                    for index, key in enumerate(keys, 1): print(f'{index}. {key}')
                    if not sys.stdin.isatty(): raise ValueError('specify source keys for noninteractive add')
                    indices = input('Select numbers separated by spaces: ').split()
                    if any(not i.isdecimal() or not 1 <= int(i) <= len(keys) for i in indices):
                        raise ValueError('invalid note selection')
                    selected = [keys[int(i)-1] for i in indices]
                for key in selected:
                    if key not in found: raise ValueError(f'unknown source key: {key}')
                from .folder_selection import inventory, selection_for, enabled
                folders = inventory(config['backup'], found)
                catalog = folders['catalog']
                rules = selection_for(config)
                for key in selected:
                    if not enabled(folders['tree'], rules, folders['membership'][key]):
                        print(f'Excluded by folder selection: {key}')
                        continue
                    if key not in state['notes']:
                        state['notes'][key] = registration(found[key], catalog, state)
                save_json(root/'state.json', state)
            elif args.command in ('rename','rebind'):
                source = args.source if args.command == 'rename' else args.old_source
                if source not in state['notes']: raise ValueError('source is not registered')
                if args.command == 'rename': rename_entry(state, source, args.name)
                else: rebind_entry(config, state, source, args.new_source)
                save_json(root/'state.json', state)
            elif args.command in ('run','watch'):
                if args.workers is not None: config = dict(config, workers=args.workers)
                engine = Synchronizer(root, config, state)
                restore = set(getattr(args, 'restore_missing', None) or [])
                if any(key not in state['notes'] or not state['notes'][key].get('success') for key in restore):
                    raise ValueError('restore requires registered documents with successful output history')
                engine.restore_targets.update(restore)
                if args.refresh: engine.refresh.update(restore or state['notes'])
                stop = Event()
                previous = signal.signal(signal.SIGINT, lambda *_: stop.set())
                try:
                    engine.run(watch=args.command == 'watch', stop_event=stop, only_sources=restore or None)
                finally:
                    signal.signal(signal.SIGINT, previous)
                if args.command == 'run' and any(e['status'] not in ('synced', 'excluded') for key, e in state['notes'].items() if not restore or key in restore):
                    parser.exit(1, 'Some notes are pending, missing, conflicting or failed; see sync status.\n')
            elif args.command == 'recover':
                print('Recovery complete.')
    except KeyboardInterrupt:
        print('\nStopped. Any pending publication will be recovered on the next run.')
    except (ValueError, OSError, BrokenProcessPool) as error:
        parser.exit(1, f'Sync failed: {error}\n')
