"""Draft-only desktop settings; persistent changes happen in the controller worker."""
from copy import deepcopy
from .folder_selection import selection_for, summary
from .sync_config import pair_key
from .gui_text import NFCStringVar, PathStringVar, display


class SettingsDialog:
    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.window = tk.Toplevel(app.window)
        self.window.title('설정' if app.config else '처음 설정')
        self.window.geometry('640x500')
        self.window.minsize(480, 360)
        self.window.transient(app.window)
        self.window.grab_set()
        config = app.config or {}
        self.backup = PathStringVar(self.window, config.get('backup', ''))
        self.vault = PathStringVar(self.window, config.get('vault', ''))
        self.width = tk.StringVar(self.window, value=str(config.get('width_scale', 0.5)))
        self.workers = tk.StringVar(self.window, value='자동 (권장)' if config.get('workers', 'auto') == 'auto' else str(config['workers']))
        self.autostart = tk.BooleanVar(self.window, value=config.get('start_on_login', True))
        self.rules = {}
        self.reviewed = set(config.get('folder_selections', {}))
        self.folder_dialog = None
        self.saving = False
        self.save_pending = False
        self.advanced_open = False
        self.folder_summary = NFCStringVar(self.window)
        self.message = NFCStringVar(self.window)
        self.initial = self.snapshot()
        self.window.columnconfigure(0, weight=1); self.window.rowconfigure(1, weight=1)
        self.header = ttk.Frame(self.window, padding=(14, 10))
        self.header.grid(row=0, column=0, sticky='ew')
        ttk.Label(self.header, text='설정' if config else '처음 설정').pack(anchor='w')
        center = ttk.Frame(self.window); center.grid(row=1, column=0, sticky='nsew')
        center.columnconfigure(0, weight=1); center.rowconfigure(0, weight=1)
        self.canvas = canvas = tk.Canvas(center, highlightthickness=0, width=1)
        self.scroll = scroll = ttk.Scrollbar(center, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky='ns'); canvas.grid(row=0, column=0, sticky='nsew')
        self.body = body = ttk.Frame(canvas, padding=14)
        content = canvas.create_window((0, 0), window=body, anchor='nw')
        self.content = content
        self.scroll_job = None
        canvas.bind('<Configure>', self.queue_scroll)
        body.bind('<Configure>', self.queue_scroll)
        body.columnconfigure(0, weight=1)
        def wrapped(parent, text=None, variable=None):
            label = ttk.Label(parent, text=text, textvariable=variable, width=1, wraplength=430)
            label.bind('<Configure>', lambda e: label.configure(wraplength=max(100, e.width)))
            return label
        ttk.Label(body, text='일반').grid(row=0, column=0, sticky='w', pady=(0, 10))
        ttk.Label(body, text='삼성노트 백업').grid(row=1, column=0, sticky='w')
        self.backup_row = backup_row = ttk.Frame(body); backup_row.grid(row=2, column=0, sticky='ew', pady=4)
        backup_row.columnconfigure(0, weight=1)
        self.backup_candidates = list(app.locations.get('backup', []))
        self.backup_box = ttk.Combobox(backup_row, textvariable=self.backup, values=[display(p) for p in self.backup_candidates], width=1)
        self.backup_box.bind('<<ComboboxSelected>>', lambda e: self.backup.set(self.backup_candidates[self.backup_box.current()]))
        self.backup_box.grid(row=0, column=0, sticky='ew')
        self.folder_button = ttk.Button(backup_row, text='폴더…', command=lambda: self.browse('backup'))
        self.folder_button.grid(row=0, column=1, padx=(4, 0))
        self.file_button = ttk.Button(backup_row, text='SDOCX…', command=lambda: self.browse('file'))
        self.file_button.grid(row=0, column=2, padx=(4, 0))
        wrapped(body, text='삼성노트가 저장한 백업을 가져옵니다.').grid(row=3, column=0, sticky='ew', pady=(0, 6))
        ttk.Label(body, text='저장 폴더/Vault').grid(row=4, column=0, sticky='w', pady=(6, 0))
        self.vault_row = vault_row = ttk.Frame(body); vault_row.grid(row=5, column=0, sticky='ew', pady=4)
        vault_row.columnconfigure(0, weight=1)
        self.vault_candidates = list(app.locations.get('vault', []))
        self.vault_box = ttk.Combobox(vault_row, textvariable=self.vault, values=[display(p) for p in self.vault_candidates], width=1)
        self.vault_box.bind('<<ComboboxSelected>>', lambda e: self.vault.set(self.vault_candidates[self.vault_box.current()]))
        self.vault_box.grid(row=0, column=0, sticky='ew')
        self.vault_button = ttk.Button(vault_row, text='찾아보기…', command=lambda: self.browse('vault'))
        self.vault_button.grid(row=0, column=1, padx=(4, 0))
        ttk.Label(body, text='가져올 폴더').grid(row=6, column=0, sticky='w', pady=(10, 0))
        folder_row = ttk.Frame(body); folder_row.grid(row=7, column=0, sticky='ew', pady=4)
        folder_row.columnconfigure(0, weight=1)
        wrapped(folder_row, variable=self.folder_summary).grid(row=0, column=0, sticky='ew')
        self.select_folders_button = ttk.Button(folder_row, text='변경…', command=self.choose_folders)
        self.select_folders_button.grid(row=0, column=1, padx=(4, 0))
        self.startup_box = ttk.Checkbutton(body, text='Windows 로그인 시 자동 동기화', variable=self.autostart)
        self.startup_box.grid(row=8, column=0, sticky='w', pady=(8, 0))
        wrapped(body, text='현재 세션의 자동 동기화는 별도로 시작합니다.').grid(row=9, column=0, sticky='ew', pady=4)
        ttk.Separator(body).grid(row=10, column=0, sticky='ew', pady=8)
        self.advanced_button = ttk.Button(body, text='고급 설정 펼치기', command=self.toggle_advanced)
        self.advanced_button.grid(row=11, column=0, sticky='w', pady=(0, 8))
        self.advanced = ttk.Frame(body)
        ttk.Label(self.advanced, text='필기 선 굵기 (기본값 0.5)').grid(row=0, column=0, sticky='w')
        self.width_box = ttk.Entry(self.advanced, textvariable=self.width, width=7)
        self.width_box.grid(row=0, column=1, padx=8)
        wrapped(self.advanced, text='작을수록 필기 선이 가늘어집니다.').grid(row=1, column=0, columnspan=2, sticky='ew')
        ttk.Label(self.advanced, text='동시 처리할 노트 수').grid(row=2, column=0, sticky='w', pady=6)
        self.workers_box = ttk.Combobox(self.advanced, textvariable=self.workers, values=('자동 (권장)', *range(1, 9)), width=12, state='readonly')
        self.workers_box.grid(row=2, column=1)
        self.notice = ttk.Frame(center, padding=(14, 6))
        self.notice.columnconfigure(0, weight=1)
        wrapped(self.notice, variable=self.message).grid(row=0, column=0, sticky='ew')
        self.message.trace_add('write', self.update_notice)
        self.footer = footer = ttk.Frame(self.window, padding=(14, 8))
        footer.grid(row=2, column=0, sticky='ew')
        self.save_button = ttk.Button(footer, text='설정 저장' if app.config else '저장하고 지금 동기화', command=self.save)
        self.save_button.pack(side='right')
        self.cancel_button = ttk.Button(footer, text='취소', command=self.close)
        self.cancel_button.pack(side='right', padx=(0, 8))
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.backup.trace_add('write', lambda *_: self.update_summary())
        self.vault.trace_add('write', lambda *_: self.update_summary())
        def focus_scroll(event):
            canvas.update_idletasks()
            top = event.widget.winfo_rooty() - body.winfo_rooty()
            height = max(1, body.winfo_height())
            first, last = canvas.yview()
            if top < first*height: canvas.yview_moveto(top/height)
            elif top + event.widget.winfo_height() > last*height:
                canvas.yview_moveto((top + event.widget.winfo_height() - canvas.winfo_height())/height)
        def bind_focus(widget):
            widget.bind('<FocusIn>', focus_scroll, add=True)
            widget.bind('<Configure>', self.queue_scroll, add=True)
            if not isinstance(widget, ttk.Combobox): widget.bind('<MouseWheel>', self.scroll_wheel, add=True)
            for child in widget.winfo_children(): bind_focus(child)
        bind_focus(body)
        canvas.bind('<MouseWheel>', self.scroll_wheel)
        self.update_summary()
        self.queue_scroll()

    def update_notice(self, *_):
        if self.message.get(): self.notice.grid(row=1, column=0, columnspan=2, sticky='ew')
        else: self.notice.grid_remove()

    def queue_scroll(self, event=None):
        if self.scroll_job is None:
            self.scroll_job = self.window.after_idle(self.update_scroll)

    def update_scroll(self):
        self.scroll_job = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.itemconfigure(self.content, width=width)
        required = self.body.winfo_reqheight()
        overflow = required > height
        if overflow:
            if not self.scroll.winfo_manager(): self.scroll.grid(row=0, column=1, sticky='ns')
        else:
            if self.scroll.winfo_manager():
                self.scroll.grid_remove()
                # Reassert the viewport after changing grid columns during map.
                self.canvas.grid()
            self.canvas.yview_moveto(0)
        self.canvas.itemconfigure(self.content, height=max(required, height))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def scroll_wheel(self, event):
        if self.scroll.winfo_manager(): self.canvas.yview_scroll(-int(event.delta/120), 'units')
        return 'break'

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
        if self.advanced_open: self.advanced.grid(row=12, column=0, sticky='ew')
        else: self.advanced.grid_remove()
        self.advanced_button.configure(text='고급 설정 접기' if self.advanced_open else '고급 설정 펼치기')
        self.queue_scroll()

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
        if self.scroll_job is not None:
            self.window.after_cancel(self.scroll_job)
            self.scroll_job = None
        self.window.grab_release(); self.window.destroy()
        self.app.settings_dialog = None
        self.app.controls()
        return True
