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
        if hasattr(app, 'size'): app.size(self.window, 640, 630, 480, 360)
        else: self.window.geometry('640x630'); self.window.minsize(480, 360)
        self.window.transient(app.window)
        self.window.grab_set()
        config = app.config or {}
        self.backup = PathStringVar(self.window, config.get('backup', ''))
        self.vault = PathStringVar(self.window, config.get('vault', ''))
        self.width = tk.StringVar(self.window, value=str(config.get('width_scale', 0.5)))
        self.workers = tk.StringVar(self.window, value='자동 (권장)' if config.get('workers', 'auto') == 'auto' else str(config['workers']))
        self.image_storage = tk.StringVar(self.window, value='이미지 파일로 분리 (권장)' if config.get('image_storage', 'external') == 'external' else '문서 안에 내장')
        self.review_images = tk.BooleanVar(self.window,value=config.get('review_images',True))
        self.repeat_distance = tk.StringVar(self.window,value=str(config.get('repeat_distance',8)))
        self.repeat_slider = tk.DoubleVar(self.window,value=float(config.get('repeat_distance',8)))
        self._repeat_updating = False
        self.review_dialog = None
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
        sp = app.theme.space
        # Windows Settings structure: section headings over cards that share the
        # main window's surface, border and spacing tokens. The title bar names the window.
        self.window.columnconfigure(0, weight=1); self.window.rowconfigure(0, weight=1)
        center = ttk.Frame(self.window); center.grid(row=0, column=0, sticky='nsew')
        center.columnconfigure(0, weight=1); center.rowconfigure(0, weight=1)
        self.canvas = canvas = tk.Canvas(center, highlightthickness=0, width=1)
        app.theme.refresh(canvas)
        self.scroll = scroll = ttk.Scrollbar(center, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky='ns'); canvas.grid(row=0, column=0, sticky='nsew')
        self.body = body = ttk.Frame(canvas, padding=(sp('l'), sp('xs'), sp('l'), sp('l')))
        content = canvas.create_window((0, 0), window=body, anchor='nw')
        self.content = content
        self.scroll_job = None
        canvas.bind('<Configure>', self.queue_scroll)
        body.bind('<Configure>', self.queue_scroll)
        body.columnconfigure(0, weight=1)
        def wrapped(parent, text=None, variable=None, style='CardMuted.TLabel'):
            label = ttk.Label(parent, text=text, textvariable=variable, width=1, wraplength=430, style=style)
            label.bind('<Configure>', lambda e: label.configure(wraplength=max(100, e.width)))
            return label
        rows = iter(range(100))
        def section(text):
            ttk.Label(body, text=text, style='Section.TLabel').grid(row=next(rows), column=0, sticky='w', pady=(sp('l'), sp('s')))
        def card():
            frame = ttk.Frame(body, style='Card.TFrame', padding=(sp('l'), sp('m')))
            frame.grid(row=next(rows), column=0, sticky='ew', pady=(0, sp('xs')))
            frame.columnconfigure(0, weight=1)
            return frame
        section('폴더')
        backup_card = card()
        ttk.Label(backup_card, text='삼성노트 백업', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        wrapped(backup_card, text='삼성노트가 저장한 백업을 가져옵니다.').grid(row=1, column=0, sticky='ew')
        self.backup_row = backup_row = ttk.Frame(backup_card, style='Card.TFrame'); backup_row.grid(row=2, column=0, sticky='ew', pady=(sp('s'), 0))
        backup_row.columnconfigure(0, weight=1)
        self.backup_candidates = list(app.locations.get('backup', []))
        self.backup_box = ttk.Combobox(backup_row, textvariable=self.backup, values=[display(p) for p in self.backup_candidates], width=1)
        self.backup_box.bind('<<ComboboxSelected>>', lambda e: self.backup.set(self.backup_candidates[self.backup_box.current()]))
        self.backup_box.grid(row=0, column=0, sticky='ew')
        self.folder_button = ttk.Button(backup_row, text='폴더…', command=lambda: self.browse('backup'))
        self.folder_button.grid(row=0, column=1, padx=(sp('xs'), 0))
        self.file_button = ttk.Button(backup_row, text='SDOCX…', command=lambda: self.browse('file'))
        self.file_button.grid(row=0, column=2, padx=(sp('xs'), 0))
        vault_card = card()
        ttk.Label(vault_card, text='저장 폴더/Vault', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        wrapped(vault_card, text='변환한 PDF와 Markdown을 저장합니다.').grid(row=1, column=0, sticky='ew')
        self.vault_row = vault_row = ttk.Frame(vault_card, style='Card.TFrame'); vault_row.grid(row=2, column=0, sticky='ew', pady=(sp('s'), 0))
        vault_row.columnconfigure(0, weight=1)
        self.vault_candidates = list(app.locations.get('vault', []))
        self.vault_box = ttk.Combobox(vault_row, textvariable=self.vault, values=[display(p) for p in self.vault_candidates], width=1)
        self.vault_box.bind('<<ComboboxSelected>>', lambda e: self.vault.set(self.vault_candidates[self.vault_box.current()]))
        self.vault_box.grid(row=0, column=0, sticky='ew')
        self.vault_button = ttk.Button(vault_row, text='찾아보기…', command=lambda: self.browse('vault'))
        self.vault_button.grid(row=0, column=1, padx=(sp('xs'), 0))
        folder_card = card()
        ttk.Label(folder_card, text='가져올 폴더', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        wrapped(folder_card, variable=self.folder_summary).grid(row=1, column=0, sticky='ew')
        self.select_folders_button = ttk.Button(folder_card, text='변경…', command=self.choose_folders)
        self.select_folders_button.grid(row=0, column=1, rowspan=2, padx=(sp('s'), 0))
        section('시작')
        startup_card = card()
        ttk.Label(startup_card, text='Windows 로그인 시 자동 동기화', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        wrapped(startup_card, text='현재 세션의 자동 동기화는 별도로 시작합니다.').grid(row=1, column=0, sticky='ew')
        self.startup_box = ttk.Checkbutton(startup_card, variable=self.autostart, style='Card.Switch.TCheckbutton')
        self.startup_box.grid(row=0, column=1, rowspan=2, padx=(sp('s'), 0))
        section('고급')
        advanced_card = card()
        ttk.Label(advanced_card, text='변환 옵션', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        wrapped(advanced_card, text='이미지 저장 방식·필기 선 굵기·동시 처리 수').grid(row=1, column=0, sticky='ew')
        self.advanced_button = ttk.Button(advanced_card, text='고급 설정 펼치기', command=self.toggle_advanced)
        self.advanced_button.grid(row=0, column=1, rowspan=2, padx=(sp('s'), 0))
        self.advanced = ttk.Frame(advanced_card, style='Card.TFrame')
        self.advanced.columnconfigure(0, weight=1)
        ttk.Label(self.advanced, text='필기 선 굵기 (기본값 0.5)', style='Card.TLabel').grid(row=0, column=0, sticky='w', pady=(sp('m'), 0))
        self.width_box = ttk.Entry(self.advanced, textvariable=self.width, width=7)
        self.width_box.grid(row=0, column=1, sticky='e', pady=(sp('m'), 0))
        wrapped(self.advanced, text='작을수록 필기 선이 가늘어집니다.').grid(row=1, column=0, columnspan=2, sticky='ew')
        ttk.Label(self.advanced, text='동시 처리할 노트 수', style='Card.TLabel').grid(row=2, column=0, sticky='w', pady=(sp('m'), 0))
        self.workers_box = ttk.Combobox(self.advanced, textvariable=self.workers, values=('자동 (권장)', *range(1, 9)), width=12, state='readonly')
        self.workers_box.grid(row=2, column=1, sticky='e', pady=(sp('m'), 0))
        ttk.Label(self.advanced, text='Markdown 이미지', style='Card.TLabel').grid(row=3,column=0,sticky='w',pady=(sp('m'),0))
        self.image_storage_box = ttk.Combobox(self.advanced, textvariable=self.image_storage,
            values=('이미지 파일로 분리 (권장)', '문서 안에 내장'), state='readonly', width=25)
        self.image_storage_box.grid(row=3,column=1,sticky='e',padx=(sp('s'),0),pady=(sp('m'),0))
        wrapped(self.advanced, text='분리 저장은 Obsidian과 휴대폰에서 긴 이미지 문자열을 처리하는 부담을 줄입니다.').grid(row=4,column=0,columnspan=2,sticky='ew')
        self.review_box=ttk.Checkbutton(self.advanced,text='반복 장식 후보 분석·승인한 숨김 적용',variable=self.review_images,style='Card.Switch.TCheckbutton')
        self.review_box.grid(row=5,column=0,columnspan=2,sticky='w',pady=(sp('m'),0))
        ttk.Label(self.advanced,text='pHash 임계값 · 허용 거리 0~32',style='Card.TLabel').grid(row=6,column=0,columnspan=2,sticky='w',pady=(sp('s'),0))
        threshold_row=ttk.Frame(self.advanced,style='Card.TFrame');threshold_row.grid(row=7,column=0,columnspan=2,sticky='ew',pady=(sp('xs'),0))
        threshold_row.columnconfigure(0,weight=1)
        self.repeat_scale=ttk.Scale(threshold_row,from_=0,to=32,variable=self.repeat_slider,command=self.repeat_moved)
        self.repeat_box=self.repeat_scale
        self.repeat_scale.grid(row=0,column=0,sticky='ew')
        ttk.Label(threshold_row,textvariable=self.repeat_distance,width=3,anchor='center',style='Card.TLabel').grid(row=0,column=1,padx=(sp('s'),0))
        self.repeat_help_button=ttk.Button(threshold_row,text='?',width=3,command=self.repeat_help)
        self.repeat_help_button.grid(row=0,column=2,padx=(sp('xs'),0))
        self.repeat_distance.trace_add('write',self.repeat_value_changed)
        for key,delta in (('<Left>',-1),('<Right>',1),('<Down>',-1),('<Up>',1)):
            self.repeat_scale.bind(key,lambda e,d=delta:self.repeat_step(d))
        self.review_button=ttk.Button(self.advanced,text='반복 이미지 그룹 검토…',command=self.open_review)
        self.review_button.grid(row=8,column=0,columnspan=2,sticky='w',pady=(sp('s'),0))
        self.ignore_manage_button=ttk.Button(self.advanced,text='자동 무시 사진 관리…',command=lambda:self.open_review(library=True))
        self.ignore_manage_button.grid(row=9,column=0,columnspan=2,sticky='w',pady=(sp('s'),0))
        wrapped(self.advanced,text='작을수록 엄격하게 비교합니다. 기본값은 8이며, 검토하지 않은 본문 그림·수식은 보존합니다.').grid(row=10,column=0,columnspan=2,sticky='ew')
        ttk.Label(self.advanced, text='동기화 연결 해제', style='Card.TLabel').grid(row=11,column=0,sticky='w',pady=(sp('m'),0))
        self.reset_button=ttk.Button(self.advanced,text='앱 초기화…',command=self.reset)
        self.reset_button.grid(row=11,column=1,padx=(sp('s'),0),pady=(sp('m'),0))
        wrapped(self.advanced, text='설정·파일 이력·청크를 비우고 새 동기화에서 출력을 덮어씁니다. 사용자 메모는 보존합니다.').grid(row=12,column=0,columnspan=2,sticky='ew')
        if not app.config:self.reset_button.configure(state='disabled')
        self.notice = ttk.Frame(center, padding=(sp('l'), sp('s')))
        self.notice.columnconfigure(0, weight=1)
        wrapped(self.notice, variable=self.message, style='TLabel').grid(row=0, column=0, sticky='ew')
        self.message.trace_add('write', self.update_notice)
        self.footer = footer = ttk.Frame(self.window, style='Status.TFrame', padding=(sp('l'), sp('m')))
        footer.grid(row=1, column=0, sticky='ew')
        self.save_button = ttk.Button(footer, text='설정 저장' if app.config else '저장하고 지금 동기화', command=self.save, style='Accent.TButton')
        self.save_button.pack(side='right')
        self.cancel_button = ttk.Button(footer, text='취소', command=self.close)
        self.cancel_button.pack(side='right', padx=(0, sp('s')))
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
        return (self.backup.get(), self.vault.get(), self.width.get(), self.workers.get(), self.autostart.get(),
                deepcopy(self.rules), self.image_storage.get(),self.review_images.get(),self.repeat_distance.get())

    def repeat_moved(self,value):
        if self._repeat_updating:return
        number=max(0,min(32,int(float(value)+.5)))
        self._repeat_updating=True
        try:
            self.repeat_distance.set(str(number));self.repeat_slider.set(number)
        finally:self._repeat_updating=False

    def repeat_value_changed(self,*_):
        if self._repeat_updating:return
        try:
            number=int(self.repeat_distance.get())
            if 0<=number<=32:self.repeat_slider.set(number)
        except ValueError:pass

    def repeat_step(self,delta):
        self.repeat_moved(float(self.repeat_distance.get())+delta)
        return 'break'

    def repeat_help(self):
        from tkinter import messagebox
        messagebox.showinfo('pHash 임계값 안내',
            '이미지를 작은 지문으로 비교할 때 허용할 차이입니다. 64비트 중 다른 비트 수를 사용합니다.\n\n'
            '0: 가장 엄격하게 비교\n8: 기본값\n32: 가장 넓은 범위의 유사 이미지 허용\n\n'
            '작을수록 엄격합니다. 거리 0도 원본 파일의 완전한 동일성을 뜻하지는 않습니다.\n\n'
            '자동 무시는 직접 승인한 사진에만 적용하며 가로세로 비율·색상·장식 위치도 함께 확인합니다. '
            '본문 그림·표·수식은 기본 보존합니다.\n\n'
            '설정 저장 후 다음 동기화부터 적용됩니다. 자동 무시 사진 관리에서 사진을 확인하거나 무시를 해제할 수 있습니다.',parent=self.window)

    def open_review(self,library=False):
        if self.saving or self.app.controller.busy or self.app.closing:return
        tab='library' if library else 'groups'
        if self.review_dialog:
            self.review_dialog.select_tab(tab);self.review_dialog.window.lift();return
        try:
            from .image_review import validate_distance
            from .image_review_dialog import ImageReviewDialog
            distance=validate_distance(int(self.repeat_distance.get()))
            self.review_dialog=ImageReviewDialog(self,self.app.controller.root/'image-review',distance,tab=tab)
        except (OSError,ValueError) as error:self.message.set(str(error))

    def config(self):
        return dict(self.app.config or {}, backup=self.backup.get(), vault=self.vault.get())

    def current_rules(self):
        config = self.config()
        return self.rules.get(pair_key(config), selection_for(config))

    def update_summary(self):
        self.folder_summary.set(summary(self.current_rules()))

    def toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        if self.advanced_open: self.advanced.grid(row=2, column=0, columnspan=2, sticky='ew')
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
            image_storage = 'external' if self.image_storage.get() == '이미지 파일로 분리 (권장)' else 'embedded'
            distance=int(self.repeat_distance.get())
            ConversionOptions(image_storage=image_storage,review_images=self.review_images.get(),repeat_distance=distance)
            values = (self.backup.get(), self.vault.get(), width, self.autostart.get(), self.app.native, workers,
                      deepcopy(self.current_rules()), image_storage,self.review_images.get(),distance)
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
        self.image_storage_box.configure(state='readonly' if enabled else 'disabled')
        for widget in (self.review_box,self.repeat_box,self.repeat_help_button,self.review_button,self.ignore_manage_button):widget.configure(state='normal' if enabled else 'disabled')
        self.reset_button.configure(state='normal' if enabled and self.app.config else 'disabled')

    def reset(self):
        if self.saving or self.app.controller.busy or self.app.closing or not self.app.config:return
        from tkinter import messagebox
        if not messagebox.askyesno('앱 초기화',
            '현재 동기화 연결·설정·모든 파일 관리 이력·청크를 삭제하고 로그인 자동 시작을 끕니다.\n\n'
            '직접 승인한 앞으로 숨김 목록과 보관 이미지는 유지합니다.\n\n'
            '삼성노트 원본과 Vault 파일은 지금 삭제하지 않습니다. 새 동기화의 첫 대상 목록에 대응하는 PDF·Markdown은 기존 파일이 있어도 덮어씁니다.\n'
            '사용자 메모는 보존합니다. 메모 경계를 읽을 수 없는 Markdown은 전체 보존본을 남기고 다시 생성합니다.\n\n초기화할까요?',parent=self.window):return
        self.saving=True;self.set_enabled(False)
        self.app.controller.launch(lambda:self.app.controller.reset_settings(self.app.native),kind='reset')
        self.message.set('동기화 연결을 해제하는 중…');self.app.controls()

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
        if self.review_dialog:self.review_dialog.close()
        if self.scroll_job is not None:
            self.window.after_cancel(self.scroll_job)
            self.scroll_job = None
        self.window.grab_release(); self.window.destroy()
        self.app.settings_dialog = None
        self.app.controls()
        return True
