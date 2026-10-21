"""Draft-only desktop settings; persistent changes happen in the controller worker."""
from copy import deepcopy
from .folder_selection import selection_for, summary
from .sync_config import pair_key


class SettingsDialog:
    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.window = tk.Toplevel(app.window)
        self.window.title('설정 변경' if app.config else '처음 설정')
        self.window.geometry('640x500')
        self.window.minsize(480, 360)
        self.window.transient(app.window)
        self.window.grab_set()
        config = app.config or {}
        self.backup = tk.StringVar(value=config.get('backup', ''))
        self.vault = tk.StringVar(value=config.get('vault', ''))
        self.width = tk.StringVar(value=str(config.get('width_scale', 0.5)))
        self.workers = tk.StringVar(value='자동 (권장)' if config.get('workers', 'auto') == 'auto' else str(config['workers']))
        self.autostart = tk.BooleanVar(value=config.get('start_on_login', True))
        self.rules = {}
        self.reviewed = set(config.get('folder_selections', {}))
        self.folder_dialog = None
        self.saving = False
        self.save_pending = False
        self.advanced_open = False
        self.folder_summary = tk.StringVar()
        message = '삼성노트가 저장한 백업을 가져옵니다. 앱 자체를 직접 동기화하지 않습니다.'
        if not config:
            message += '\n로그인 자동 시작은 기본으로 켜져 있습니다. 고급 설정에서 변경할 수 있습니다.'
        self.message = tk.StringVar(value=message)
        self.initial = self.snapshot()
        canvas = tk.Canvas(self.window, highlightthickness=0, width=1)
        scroll = ttk.Scrollbar(self.window, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y'); canvas.pack(fill='both', expand=True)
        self.body = body = ttk.Frame(canvas, padding=14)
        content = canvas.create_window((0, 0), window=body, anchor='nw')
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(content, width=e.width))
        body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text='삼성노트 백업').grid(row=0, column=0, sticky='w')
        self.backup_box = ttk.Combobox(body, textvariable=self.backup, values=app.locations.get('backup', []), width=1)
        self.backup_box.grid(row=1, column=0, sticky='ew', pady=4)
        backup_actions = ttk.Frame(body); backup_actions.grid(row=2, column=0, sticky='w')
        self.folder_button = ttk.Button(backup_actions, text='폴더 선택', command=lambda: self.browse('backup'))
        self.folder_button.pack(side='left')
        self.file_button = ttk.Button(backup_actions, text='SDOCX 파일 선택', command=lambda: self.browse('file'))
        self.file_button.pack(side='left', padx=6)
        ttk.Label(body, text='저장 폴더/Vault').grid(row=3, column=0, sticky='w', pady=(12, 0))
        self.vault_box = ttk.Combobox(body, textvariable=self.vault, values=app.locations.get('vault', []), width=1)
        self.vault_box.grid(row=4, column=0, sticky='ew', pady=4)
        self.vault_button = ttk.Button(body, text='저장 폴더 선택', command=lambda: self.browse('vault'))
        self.vault_button.grid(row=5, column=0, sticky='w')
        ttk.Label(body, text='Obsidian Vault 또는 일반 폴더를 선택할 수 있습니다.', wraplength=430).grid(row=6, column=0, sticky='w', pady=6)
        self.select_folders_button = ttk.Button(body, text='가져올 폴더 선택', command=self.choose_folders)
        self.select_folders_button.grid(row=7, column=0, sticky='w', pady=(8, 0))
        ttk.Label(body, textvariable=self.folder_summary, wraplength=430).grid(row=8, column=0, sticky='w', pady=5)
        self.advanced_button = ttk.Button(body, text='고급 설정 펼치기', command=self.toggle_advanced)
        self.advanced_button.grid(row=9, column=0, sticky='w', pady=8)
        self.advanced = ttk.Frame(body)
        ttk.Label(self.advanced, text='필기 선 굵기 (기본값 0.5)').grid(row=0, column=0, sticky='w')
        self.width_box = ttk.Entry(self.advanced, textvariable=self.width, width=7)
        self.width_box.grid(row=0, column=1, padx=8)
        ttk.Label(self.advanced, text='작을수록 필기 선이 가늘어집니다.').grid(row=1, column=0, columnspan=2, sticky='w')
        ttk.Label(self.advanced, text='동시 처리할 노트 수').grid(row=2, column=0, sticky='w', pady=6)
        self.workers_box = ttk.Combobox(self.advanced, textvariable=self.workers, values=('자동 (권장)', *range(1, 9)), width=12, state='readonly')
        self.workers_box.grid(row=2, column=1)
        self.startup_box = ttk.Checkbutton(self.advanced, text='로그인할 때 자동 동기화 시작', variable=self.autostart)
        self.startup_box.grid(row=3, column=0, columnspan=2, sticky='w')
        ttk.Label(self.advanced, text='Windows 로그인 후 앱을 실행하고 백업 변경을 확인합니다.\n현재 세션의 자동 동기화는 별도로 시작합니다.', wraplength=430).grid(row=4, column=0, columnspan=2, sticky='w', pady=4)
        ttk.Label(body, textvariable=self.message, wraplength=430).grid(row=11, column=0, sticky='ew', pady=10)
        actions = ttk.Frame(body); actions.grid(row=12, column=0, sticky='ew')
        self.save_button = ttk.Button(actions, text='설정 저장' if app.config else '저장하고 지금 동기화', command=self.save)
        self.save_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='취소', command=self.close)
        self.cancel_button.pack(side='left', padx=8)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.backup.trace_add('write', lambda *_: self.update_summary())
        self.vault.trace_add('write', lambda *_: self.update_summary())
        self.update_summary()

    def snapshot(self):
        return (self.backup.get(), self.vault.get(), self.width.get(), self.workers.get(), self.autostart.get(), deepcopy(self.rules))

    def config(self):
        return dict(self.app.config or {}, backup=self.backup.get(), vault=self.vault.get())

    def current_rules(self):
        config = self.config()
        return self.rules.get(pair_key(config), selection_for(config))

    def update_summary(self):
        self.folder_summary.set(summary(self.current_rules()))

    def toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        if self.advanced_open: self.advanced.grid(row=10, column=0, sticky='ew')
        else: self.advanced.grid_remove()
        self.advanced_button.configure(text='고급 설정 접기' if self.advanced_open else '고급 설정 펼치기')

    def browse(self, kind):
        from tkinter import filedialog
        if kind == 'file':
            value = filedialog.askopenfilename(parent=self.window, title='삼성노트 SDOCX 파일 선택', filetypes=[('Samsung Notes', '*.sdocx')])
        else:
            value = filedialog.askdirectory(parent=self.window, title='저장 폴더/Vault 선택' if kind == 'vault' else '삼성노트 백업 폴더 선택')
        if value:
            (self.vault if kind == 'vault' else self.backup).set(value)
            self.app.selected_paths[kind] = value  # In-memory only, for opt-in path diagnostics.

    def choose_folders(self):
        if self.saving or self.folder_dialog: return
        if not self.backup.get().strip():
            self.message.set('먼저 삼성노트 백업을 선택하세요.'); return
        from .folder_dialog import FolderDialog
        key = pair_key(self.config())
        applied = []
        def apply(rules):
            self.rules[key] = rules; self.reviewed.add(key); applied.append(True); self.update_summary()
        def closed():
            self.folder_dialog = None
            self.window.grab_set()
            pending, self.save_pending = self.save_pending, False
            if pending and applied and not self.app.closing:
                self.save()
        self.folder_dialog = FolderDialog(self.window, self.backup.get(), self.current_rules(), apply, closed)

    def save(self):
        if self.saving or self.app.controller.busy or self.app.closing: return
        try:
            if not self.backup.get().strip() or not self.vault.get().strip():
                raise ValueError('삼성노트 백업과 저장 폴더/Vault를 선택하세요.')
            if pair_key(self.config()) not in self.reviewed:
                self.save_pending = True; self.choose_folders(); return
            from .model import ConversionOptions
            from .worker import worker_count
            width = float(self.width.get()); ConversionOptions(width_scale=width)
            workers = worker_count('auto' if self.workers.get() == '자동 (권장)' else self.workers.get())
            values = (self.backup.get(), self.vault.get(), width, self.autostart.get(), self.app.native, workers, deepcopy(self.current_rules()))
            first = self.app.config is None
            action = self.app.controller.save_and_sync if first else self.app.controller.save_settings
            self.saving = True; self.app.failed = False
            self.set_enabled(False)
            self.app.controller.launch(lambda: action(*values), kind='setup' if first else 'settings')
            self.message.set('설정을 저장하는 중…')
            self.app.controls()
        except (ValueError, OSError) as error:
            self.message.set(str(error))

    def set_enabled(self, enabled):
        for widget in (self.backup_box, self.vault_box, self.folder_button, self.file_button, self.vault_button,
                       self.select_folders_button, self.width_box, self.startup_box, self.advanced_button, self.save_button, self.cancel_button):
            widget.configure(state='normal' if enabled else 'disabled')
        self.workers_box.configure(state='readonly' if enabled else 'disabled')

    def close(self, force=False):
        if not force:
            if self.saving:
                self.app.exit()
                return False
            if self.snapshot() != self.initial:
                from tkinter import messagebox
                if not messagebox.askyesno('설정 변경 취소', '저장하지 않은 변경을 버릴까요?', parent=self.window): return False
        if self.folder_dialog:
            # Suppress auto-save callbacks while discarding or ending the session.
            self.save_pending = False
            self.folder_dialog.close()
        self.window.grab_release(); self.window.destroy()
        self.app.settings_dialog = None
        self.app.controls()
        return True
