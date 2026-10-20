"""UI-independent desktop operations. Workers send snapshots through a queue."""
from copy import deepcopy
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Queue
from threading import Event, Thread

from .locations import obsidian_vaults, samsung_locations
from .sync import Synchronizer, rename_entry
from .sync_config import create_config, load_config, load_state, new_config, update_config, change_paths
from .sync_storage import Conflict, recover, store_lock


class DesktopController:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = Queue()
        self.stop_event = Event()
        self.thread = None
        self.kind = None
        self.logger = logging.Logger('sno2md.desktop')
        handler = RotatingFileHandler(self.root/'desktop.log', maxBytes=1024*1024,
                                      backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.logger.addHandler(handler)

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def log(self, message):
        self.logger.info(message)
        self.events.put(('log', message))

    def state(self, value):
        self.events.put(('state', deepcopy(value)))

    def launch(self, action, kind='settings'):
        if self.busy:
            raise Conflict('another desktop operation is running')
        self.stop_event.clear()
        self.kind = kind
        def work():
            try:
                action()
            except Exception as error:
                # Error messages and paths only: never dump Note objects or PDF text.
                message = f'{type(error).__name__}: {error}'
                self.logger.error(message)
                self.events.put(('error', message))
            finally:
                self.events.put(('finished', kind))
        self.thread = Thread(target=work, name='sno2md-worker', daemon=False)
        self.thread.start()

    def initialize(self):
        if (self.root/'config.json').exists():
            self.events.put(('config', load_config(self.root)))
            self.state(load_state(self.root))
        candidates = {'backup': [], 'vault': []}
        for key, function in (('backup', samsung_locations), ('vault', obsidian_vaults)):
            try:
                candidates[key] = [str(p) for p in function()]
            except (ValueError, OSError) as error:
                self.log(f'Location discovery: {error}')
        self.events.put(('locations', candidates))
        self.events.put(('initialized', None))

    def save_settings(self, backup, vault, width_scale, start_on_login, native, workers='auto', folder_rules=None):
        with store_lock(self.root):
            existing = (self.root/'config.json').exists()
            if existing:
                config = load_config(self.root)
                config = change_paths(self.root, config, backup, vault)
                if (self.root/'journal.json').exists():
                    recover(self.root, load_config(self.root), load_state(self.root))
                config = dict(config, width_scale=width_scale, workers=workers)
                from .sync_config import validate_config
                validate_config(self.root, config)
            else:
                config = new_config(self.root, backup, vault, width_scale=width_scale, workers=workers)
            if folder_rules is not None:
                from .sync_config import pair_key
                config['folder_selections'] = dict(config.get('folder_selections', {}))
                config['folder_selections'][pair_key(config)] = dict(folder_rules)
            config['start_on_login'] = bool(start_on_login)
            previous = native.get_startup_command()
            command = native.startup_command() if start_on_login else None
            native.set_startup_command(command)
            try:
                if existing:
                    update_config(self.root, config)
                else:
                    create_config(self.root, config)
            except Exception:
                native.set_startup_command(previous)
                raise
            state = load_state(self.root)
        self.events.put(('config', config))
        self.state(state)
        self.log('설정을 저장했습니다.')

    def synchronize(self, watch=False, only_sources=None, restore_sources=None):
        with store_lock(self.root):
            config, state = load_config(self.root), load_state(self.root)
            engine = Synchronizer(self.root, config, state, emit=self.log, on_state=self.state)
            if restore_sources:
                if watch or any(key not in state['notes'] or not state['notes'][key].get('success') for key in restore_sources):
                    raise ValueError('restore requires registered documents with successful output history')
                engine.restore_targets.update(restore_sources)
                only_sources = set(restore_sources)
            self.state(state)
            engine.run(watch=watch, stop_event=self.stop_event, only_sources=only_sources)
            self.state(state)

    def start(self, watch=False, only_sources=None):
        self.launch(lambda: self.synchronize(watch, only_sources), kind='watch' if watch else 'sync')

    def restore(self, sources):
        targets = {sources} if isinstance(sources, str) else set(sources)
        if not targets:
            raise ValueError('select at least one document to restore')
        self.launch(lambda: self.synchronize(restore_sources=targets), kind='sync')

    def stop(self):
        self.stop_event.set()

    def rename(self, source, name):
        from .sync_storage import save_json
        with store_lock(self.root):
            config, state = load_config(self.root), load_state(self.root)
            recover(self.root, config, state)
            rename_entry(state, source, name)
            save_json(self.root/'state.json', state)
        self.state(state)
        self.log(f'출력 이름 변경: {source} → {name} (이전 파일 유지)')
        # Recreate only the renamed note. Output protection still applies.
        if not self.stop_event.is_set():
            self.synchronize(only_sources={source})

    def close(self):
        if self.busy:
            raise RuntimeError('worker must finish before closing the controller')
        for handler in self.logger.handlers:
            handler.close()
