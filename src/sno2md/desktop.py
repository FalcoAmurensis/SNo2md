"""UI-independent desktop operations. Workers send snapshots through a queue."""
from copy import deepcopy
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Queue
from threading import Event, Thread

from .locations import obsidian_vaults, samsung_locations
from .sync import Synchronizer, rename_entry
from .sync_observation import describe
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

    def results(self, results):
        for result in results:
            self.logger.info('result: %s: %s — %s; copy=%s', result.get('source'), result.get('status'),
                             result.get('message', ''), result.get('copy'))
        self.events.put(('results', results))

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
            config, state = load_config(self.root), load_state(self.root)
            Synchronizer(self.root, config, state, on_state=self.state).inspect()
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
        Synchronizer(self.root, config, state, on_state=self.state).inspect()
        self.log('설정을 저장했습니다.')

    def save_and_sync(self, *values):
        self.save_settings(*values)
        if not self.stop_event.is_set():
            self.synchronize()

    def synchronize(self, watch=False, only_sources=None, restore_sources=None, restore_skipped=()):
        with store_lock(self.root):
            config, state = load_config(self.root), load_state(self.root)
            engine = Synchronizer(self.root, config, state, emit=self.log, on_state=self.state)
            if restore_sources:
                if watch or any(key not in state['notes'] or (not state['notes'][key].get('success') or state['notes'][key].get('sync_policy') == 'retired') for key in restore_sources):
                    raise ValueError('restore requires registered documents with successful output history')
                engine.restore_targets.update(restore_sources)
                only_sources = set(restore_sources)
            engine.inspect()
            engine.run(watch=watch, stop_event=self.stop_event, only_sources=only_sources)
            engine.inspect()
            if restore_sources or only_sources is not None:
                results = list(restore_skipped)
                for key in sorted(restore_sources or only_sources):
                    entry = state['notes'][key]
                    item = engine.observations[key]
                    message = ('폴더 선택에서 제외됨' if item.get('excluded') else
                               '원본 없음: 다시 생성할 수 없음' if not item.get('source_exists') else
                               entry.get('error') or '사용자 메모 보존 · 자동 관리 중')
                    results.append({'source': key, 'status': entry['status'], 'message': message})
                self.results(results)

    def start(self, watch=False, only_sources=None):
        self.launch(lambda: self.synchronize(watch, only_sources), kind='watch' if watch else 'sync')

    def restore(self, sources, skipped=()):
        targets = {sources} if isinstance(sources, str) else set(sources)
        if not targets:
            raise ValueError('select at least one document to restore')
        self.launch(lambda: self.synchronize(restore_sources=targets, restore_skipped=skipped), kind='sync')

    def policy(self, sources, policy):
        def action():
            with store_lock(self.root):
                config, state = load_config(self.root), load_state(self.root)
                engine = Synchronizer(self.root, config, state, emit=self.log, on_state=self.state)
                results = []
                try:
                    engine.set_policy(sources, policy)
                    for key in sorted(sources):
                        results.append({'source': key, 'status': '관리 중단' if policy == 'retired' else '관리 재개', 'message': describe(engine.observations[key])})
                except (OSError, ValueError) as error:
                    results = [{'source': key, 'status': 'failed', 'message': str(error)} for key in sorted(sources)]
                self.results(results)
        self.launch(action, kind='policy')

    def prepare_resolution(self, sources):
        def action():
            with store_lock(self.root):
                config, state = load_config(self.root), load_state(self.root)
                recover(self.root, config, state)
                engine = Synchronizer(self.root, config, state, emit=self.log, on_state=self.state)
                offers, skipped = {}, []
                observations = engine.inspect()
                for key in sorted(sources):
                    try:
                        request = engine.resolution_requests({key}, 'preserve-vault', restore=True, observations=observations)[key]
                        request['use_source'] = engine.observations[key]['md_resolvable']
                        offers[key] = request
                    except (OSError, ValueError) as error:
                        skipped.append({'source': key, 'status': 'deferred', 'message': str(error)})
                self.events.put(('resolution_offer', (offers, skipped)))
        self.launch(action, kind='inspect')

    def resolve(self, requests, skipped=()):
        def action():
            with store_lock(self.root):
                config, state = load_config(self.root), load_state(self.root)
                recover(self.root, config, state)
                engine = Synchronizer(self.root, config, state, emit=self.log, on_state=self.state)
                results = list(skipped)
                for key, request in requests.items():
                    try:
                        # Validate confirmation tokens, policy and folder selection again.
                        current = engine.resolution_requests({key}, request['choice'], restore=True)[key]
                        if (current['hash'] != request['hash'] or current['missing'] != request['missing']
                                or current['pair'] != request['pair']):
                            raise Conflict('Vault files changed since confirmation; confirm again')
                        engine.resolutions[key] = request
                        engine.run(stop_event=self.stop_event, only_sources={key})
                        entry = state['notes'][key]
                        results.append({'source': key, 'status': ('synced' if key in engine.resolution_done else
                                                                'deferred' if entry['status'] in ('synced', 'pending', 'waiting', 'excluded')
                                                                else entry['status']),
                                        'message': entry.get('error') or ('자동 관리 중' if key in engine.resolution_done else '보류'),
                                        'copy': entry.get('last_preserved_copy')})
                    except (OSError, ValueError) as error:
                        results.append({'source': key, 'status': 'failed', 'message': str(error),
                                        'copy': state['notes'].get(key, {}).get('last_preserved_copy')})
                self.results(results)
        self.launch(action, kind='sync')

    def diagnose_paths(self, keys=(), selected_paths=None, tk_version=None):
        from .path_diagnostics import diagnose
        def action():
            report = diagnose(self.root, load_config(self.root), keys, selected_paths, self.stop_event, tk_version)
            self.events.put(('diagnostics', report))
        self.launch(action, kind='diagnostics')

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
