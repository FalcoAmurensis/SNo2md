"""Small Korean desktop UI. All Tk calls stay on the main thread."""
import argparse
import os
from pathlib import Path
from queue import Empty
import sys

from .desktop import DesktopController
from .locations import state_directory


STATUS = {'missing_output': '삭제됨', 'excluded': '동기화 제외', 'pending': '대기', 'waiting': '입력 안정화 대기', 'converting': '변환 중',
          'queued': '변환 순서 대기', 'ready_to_publish': '저장 대기', 'synced': '완료', 'missing': '원본 없음', 'conflict': '충돌', 'error': '실패'}
RETRYABLE = {'error', 'conflict', 'missing', 'pending', 'waiting'}


class Application:
    def __init__(self, window, controller, native, tray=None, startup=False):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.window, self.controller, self.native, self.tray = window, controller, native, tray
        self.startup = startup
        self.config = None
        self.notes = {}
        self.folder_drafts = {}
        self.folder_reviewed = set()
        self.folder_dialog = None
        self.folder_summary = tk.StringVar(value="동기화 폴더: 전체 포함 · 휴지통 제외")
        self.closing = False
        self.initialized = False
        self.failed = False
        self.tree_keys = {}
        self.checked = set()
        self.log_lines = []
        self.backup = tk.StringVar()
        self.vault = tk.StringVar()
        self.width = tk.StringVar(value='0.5')
        self.workers = tk.StringVar(value='auto')
        self.autostart = tk.BooleanVar(value=True)
        self.banner = tk.StringVar(value='설정을 불러오는 중…')
        window.title('SNo2md — 삼성노트 동기화')
        window.geometry('1000x590')
        window.minsize(700, 480)
        window.configure(background='white')
        style = ttk.Style(window)
        style.theme_use('clam')
        style.configure('.', font=('맑은 고딕', 10), background='white')
        style.configure('TButton', padding=(9, 5), background='#f3f4f6')
        style.configure('Treeview', background='white', fieldbackground='white', rowheight=29)
        style.configure('Title.TLabel', font=('맑은 고딕', 17, 'bold'))
        body = ttk.Frame(window, padding=18)
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(6, weight=1)
        ttk.Label(body, text='Samsung Notes → Obsidian', style='Title.TLabel').grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 16))
        ttk.Label(body, text='백업 입력').grid(row=1, column=0, sticky='w', padx=(0, 12))
        self.backup_box = ttk.Combobox(body, textvariable=self.backup)
        self.backup_box.grid(row=1, column=1, sticky='ew', pady=5)
        self.folder_button = ttk.Button(body, text='폴더 선택', command=lambda: self.browse('backup'))
        self.folder_button.grid(row=1, column=2, padx=5)
        self.file_button = ttk.Button(body, text='SDOCX 선택', command=lambda: self.browse('file'))
        self.file_button.grid(row=1, column=3)
        ttk.Label(body, text='Vault').grid(row=2, column=0, sticky='w')
        self.vault_box = ttk.Combobox(body, textvariable=self.vault)
        self.vault_box.grid(row=2, column=1, sticky='ew', pady=5)
        self.vault_button = ttk.Button(body, text='폴더 선택', command=lambda: self.browse('vault'))
        self.vault_button.grid(row=2, column=2, padx=5)
        options = ttk.Frame(body)
        options.grid(row=3, column=0, columnspan=4, sticky='ew', pady=10)
        ttk.Label(options, text='필기 굵기 배율').pack(side='left')
        self.width_box = ttk.Entry(options, textvariable=self.width, width=7)
        self.width_box.pack(side='left', padx=(8, 22))
        ttk.Label(options, text='동시 변환').pack(side='left')
        self.workers_box = ttk.Combobox(options, textvariable=self.workers, values=('auto', *range(1, 9)), width=5, state='readonly')
        self.workers_box.pack(side='left', padx=(8, 15))
        self.startup_box = ttk.Checkbutton(options, text='Windows 로그인 시 시작', variable=self.autostart)
        self.startup_box.pack(side='left')
        self.save_button = ttk.Button(options, text='설정 저장', command=self.save)
        self.save_button.pack(side='right')
        actions = ttk.Frame(body)
        actions.grid(row=4, column=0, columnspan=4, sticky='ew', pady=(0, 10))
        self.once_button = ttk.Button(actions, text='한 번 동기화', command=self.sync_once)
        self.once_button.pack(side='left')
        self.watch_button = ttk.Button(actions, text='감시 시작', command=self.toggle)
        self.watch_button.pack(side='left', padx=7)
        self.open_button = ttk.Button(actions, text='Vault 열기', command=self.open_vault)
        self.open_button.pack(side='left')
        self.select_folders_button = ttk.Button(actions, text='동기화 폴더 선택', command=self.choose_folders)
        self.select_folders_button.pack(side='left', padx=7)
        ttk.Label(actions, textvariable=self.folder_summary).pack(side='left')
        ttk.Label(body, textvariable=self.banner, wraplength=800).grid(row=5, column=0, columnspan=4, sticky='w', pady=(0, 8))
        table = ttk.Frame(body)
        table.grid(row=6, column=0, columnspan=4, sticky='nsew')
        self.tree = ttk.Treeview(table, columns=('check', 'name', 'status', 'source'), show='headings', selectmode='browse')
        self.tree.heading('check', text='☐', command=self.toggle_all_checked)
        self.tree.column('check', width=42, minwidth=42, stretch=False, anchor='center')
        for key, label, width in (('name','문서 / 출력 이름',240), ('status','상태',135), ('source','백업 내 경로',350)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=80)
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<Button-1>', self.click_check)
        self.tree.bind('<space>', self.space_check)
        self.tree.bind('<<TreeviewSelect>>', lambda event: self.controls())
        footer = ttk.Frame(body)
        footer.grid(row=7, column=0, columnspan=4, sticky='ew', pady=(10, 0))
        self.retry_button = ttk.Button(footer, text='체크 문서 재시도', command=self.retry)
        self.retry_button.pack(side='left')
        self.restore_button = ttk.Button(footer, text='누락 파일 재생성', command=self.restore)
        self.restore_button.pack(side='left', padx=7)
        self.rename_button = ttk.Button(footer, text='출력 이름 변경', command=self.rename)
        self.rename_button.pack(side='left', padx=7)
        ttk.Button(footer, text='오류 / 로그 보기', command=self.details).pack(side='right')
        ttk.Label(body, text='창을 닫으면 트레이에서 계속 실행됩니다. 종료는 트레이 메뉴를 이용하세요.', foreground='#666666').grid(row=8, column=0, columnspan=4, sticky='w', pady=(12, 0))
        window.protocol('WM_DELETE_WINDOW', self.hide)
        window.report_callback_exception = lambda kind, value, tb: self.error(f'{kind.__name__}: {value}')
        self.backup.trace_add('write', lambda *_: self.update_folder_summary())
        self.vault.trace_add('write', lambda *_: self.update_folder_summary())
        self.controls()
        window.after(100, self.poll)
        controller.launch(controller.initialize, kind='initialize')

    def selected(self):
        selected = self.tree.selection()
        return self.tree_keys.get(selected[0]) if selected else None

    def checked_targets(self, statuses=None):
        visible = set(self.tree_keys.values())
        return {key for key in self.checked & visible
                if statuses is None or self.notes[key]['status'] in statuses}

    def single_target(self):
        targets = self.checked_targets()
        return next(iter(targets)) if len(targets) == 1 else (self.selected() if not targets else None)

    def refresh_checks(self):
        for iid, key in self.tree_keys.items():
            self.tree.set(iid, 'check', '☑' if key in self.checked else '☐')
        visible = set(self.tree_keys.values())
        count = len(self.checked & visible)
        self.tree.heading('check', text='☑' if visible and count == len(visible) else ('▣' if count else '☐'))
        self.controls()

    def toggle_check(self, iid):
        key = self.tree_keys.get(iid)
        if key is None: return
        if key in self.checked: self.checked.remove(key)
        else: self.checked.add(key)
        self.refresh_checks()

    def click_check(self, event):
        if self.tree.identify_region(event.x, event.y) == 'cell' and self.tree.identify_column(event.x) == '#1':
            iid = self.tree.identify_row(event.y)
            if iid:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.toggle_check(iid)
            return 'break'

    def space_check(self, event):
        self.toggle_check(self.tree.focus())
        return 'break'

    def toggle_all_checked(self):
        visible = set(self.tree_keys.values())
        if visible <= self.checked: self.checked.difference_update(visible)
        else: self.checked.update(visible)
        self.refresh_checks()

    def controls(self):
        busy = self.controller.busy
        idle = self.initialized and not busy and not self.closing and self.folder_dialog is None
        for widget in (self.save_button, self.width_box, self.startup_box):
            widget.configure(state='normal' if idle else 'disabled')
        self.select_folders_button.configure(state='normal' if idle else 'disabled')
        self.workers_box.configure(state='readonly' if idle else 'disabled')
        for widget in (self.folder_button, self.file_button, self.vault_button):
            widget.configure(state='normal' if idle else 'disabled')
        for widget in (self.backup_box, self.vault_box):
            widget.configure(state='normal' if idle else 'disabled')
        self.once_button.configure(state='normal' if idle and self.config else 'disabled')
        self.open_button.configure(state='normal' if self.config else 'disabled')
        stopping = busy and self.controller.stop_event.is_set()
        self.watch_button.configure(text='중지 중…' if stopping else ('감시 / 작업 중지' if busy and self.controller.kind in ('watch','sync') else '감시 시작'),
                                    state='normal' if not self.closing and not stopping and (idle and self.config or busy and self.controller.kind in ('watch','sync')) else 'disabled')
        key = self.single_target()
        retry = self.checked_targets(RETRYABLE)
        restore = self.checked_targets({'missing_output'})
        self.rename_button.configure(state='normal' if idle and key else 'disabled')
        self.retry_button.configure(text=f'체크 문서 재시도 ({len(retry)})', state='normal' if idle and retry else 'disabled')
        self.restore_button.configure(text=f'누락 파일 재생성 ({len(restore)})', state='normal' if idle and restore else 'disabled')
        if self.tray:
            self.tray.watching = busy and self.controller.kind in ('watch','sync')

    def browse(self, kind):
        from tkinter import filedialog
        if kind == 'file':
            value = filedialog.askopenfilename(parent=self.window, title='SDOCX 파일 선택', filetypes=[('Samsung Notes', '*.sdocx')])
        else:
            value = filedialog.askdirectory(parent=self.window, title='Vault 선택' if kind == 'vault' else '삼성노트 백업 폴더 선택')
        if value: (self.vault if kind == 'vault' else self.backup).set(value)

    def update_folder_summary(self):
        from .folder_selection import summary
        self.folder_summary.set('동기화 폴더: ' + summary(self.current_folder_rules()))

    def folder_config(self):
        return dict(self.config or {}, backup=self.backup.get(), vault=self.vault.get())

    def current_folder_rules(self):
        from .sync_config import pair_key
        from .folder_selection import selection_for
        config = self.folder_config()
        return self.folder_drafts.get(pair_key(config), selection_for(config))

    def choose_folders(self):
        if self.controller.busy or self.folder_dialog or self.closing: return
        if not self.backup.get().strip():
            self.error('먼저 백업 입력을 선택해 주세요.')
            return
        from .folder_dialog import FolderDialog
        from .folder_selection import summary
        from .sync_config import pair_key
        key = pair_key(self.folder_config())
        def apply(rules):
            self.folder_drafts[key] = rules
            self.folder_reviewed.add(key)
            self.folder_summary.set('동기화 폴더: ' + summary(rules) + ' (설정 저장 필요)')
        def closed():
            self.folder_dialog = None
            self.controls()
        self.folder_dialog = FolderDialog(self.window, self.backup.get(), self.current_folder_rules(), apply, closed)
        self.controls()

    def save(self):
        try:
            if not self.backup.get().strip() or not self.vault.get().strip():
                raise ValueError('백업 경로와 Vault를 선택해 주세요.')
            from .sync_config import pair_key
            key = pair_key(self.folder_config())
            saved_pairs = (self.config or {}).get('folder_selections', {})
            if key not in self.folder_reviewed and key not in saved_pairs:
                self.choose_folders()
                return
            width = float(self.width.get())
            from .model import ConversionOptions
            ConversionOptions(width_scale=width)
            from .worker import worker_count
            values = (self.backup.get(), self.vault.get(), width, self.autostart.get(), self.native, worker_count(self.workers.get()), self.current_folder_rules())
            self.failed = False
            self.controller.launch(lambda: self.controller.save_settings(*values))
            self.banner.set('설정을 저장하는 중…')
        except (ValueError, OSError) as error:
            self.error(str(error))
        self.controls()

    def paths_saved(self):
        from .folder_selection import selection_for
        if self.config and self.current_folder_rules() != selection_for(self.config):
            self.error('동기화 폴더 선택을 변경했습니다. 먼저 설정 저장을 눌러 주세요.')
            return False
        if self.config and (self.backup.get() != self.config['backup'] or
                            self.vault.get() != self.config['vault']):
            self.error('경로를 변경했습니다. 먼저 설정 저장을 눌러 주세요.')
            return False
        return True

    def sync_once(self, sources=None):
        if self.controller.busy or self.config is None or self.folder_dialog: return
        if not self.paths_saved(): return
        self.failed = False
        self.controller.start(only_sources=sources)
        self.banner.set('동기화 준비 중…')
        self.controls()

    def toggle(self):
        if self.folder_dialog: return
        if self.controller.busy:
            if self.controller.kind in ('watch','sync'):
                self.controller.stop()
                self.banner.set('이미 시작한 변환과 저장을 마친 뒤 중지합니다.')
        elif self.config and not self.closing:
            if not self.paths_saved():
                self.show()
                return
            self.failed = False
            self.controller.start(watch=True)
            self.banner.set('백업 감시 중…')
        else:
            self.show()
        self.controls()

    def retry(self):
        targets = self.checked_targets(RETRYABLE)
        if targets: self.sync_once(targets)

    def restore(self):
        from tkinter import messagebox
        targets = self.checked_targets({'missing_output'})
        if self.controller.busy or self.folder_dialog or not targets: return
        if not self.paths_saved(): return
        names = [self.notes[key]['name'] for key in sorted(targets)]
        preview = '\n'.join(names[:8])
        if len(names) > 8: preview += f'\n외 {len(names)-8}개'
        skipped = len(self.checked_targets()) - len(targets)
        message = (f'삭제됨으로 표시된 문서 {len(targets)}개의 PDF·MD를 다시 생성할까요?\n\n'
                   + preview + '\n\n남아 있는 파일이 수정됐다면 해당 문서는 복원하지 않습니다.')
        if skipped: message += f'\n삭제됨 상태가 아닌 {skipped}개는 제외합니다.'
        if messagebox.askyesno('누락 파일 재생성', message, parent=self.window):
            self.failed = False
            self.controller.restore(targets)
            self.banner.set(f'체크한 {len(targets)}개 문서의 누락 파일을 재생성하는 중…')
            self.controls()

    def rename(self):
        from tkinter import simpledialog
        key = self.single_target()
        if not key or self.controller.busy or self.folder_dialog: return
        if not self.paths_saved(): return
        name = simpledialog.askstring('출력 이름 변경', '새 파일 이름 (확장자 제외)\n기존 PDF·MD는 보존합니다.',
                                      initialvalue=self.notes[key]['name'], parent=self.window)
        if name:
            self.failed = False
            self.controller.launch(lambda: self.controller.rename(key, name), kind='sync')
            self.banner.set('출력 이름을 변경하고 다시 생성하는 중…')
        self.controls()

    def open_vault(self):
        try:
            if self.config and Path(self.config['vault']).is_dir():
                os.startfile(self.config['vault'])
            else: self.error('Vault 경로를 찾을 수 없습니다.')
        except OSError as error: self.error(str(error))

    def details(self):
        view = self.tk.Toplevel(self.window)
        view.title('SNo2md — 오류와 최근 로그')
        view.geometry('800x430')
        text = self.tk.Text(view, wrap='word', font=('맑은 고딕', 10), background='white')
        text.pack(fill='both', expand=True, padx=12, pady=12)
        keys = self.checked_targets() or ({self.selected()} if self.selected() else set())
        for key in sorted(keys):
            if self.notes[key].get('error'):
                text.insert('end', f'{key}\n{self.notes[key]["error"]}\n\n')
        text.insert('end', '\n'.join(self.log_lines) or '기록된 오류가 없습니다.')
        text.insert('end', f'\n\n로그 파일: {self.controller.root / "desktop.log"}')
        text.configure(state='disabled')

    def update_state(self, state):
        selected = self.selected()
        self.notes = state['notes']
        self.tree.delete(*self.tree.get_children())
        self.tree_keys.clear()
        self.checked.intersection_update(key for key, entry in self.notes.items() if entry['status'] != 'excluded')
        for number, (key, entry) in enumerate(self.notes.items()):
            if entry['status'] == 'excluded': continue
            iid = str(number)
            self.tree_keys[iid] = key
            self.tree.insert('', 'end', iid=iid, values=('☑' if key in self.checked else '☐', '/'.join(p for p in (entry.get('folder', ''), entry['name']) if p), STATUS.get(entry['status'], entry['status']), key))
            if key == selected: self.tree.selection_set(iid)
        self.refresh_checks()
        converting = [e['name'] for e in self.notes.values() if e['status'] == 'converting']
        ready = sum(e['status'] == 'ready_to_publish' for e in self.notes.values())
        queued = sum(e['status'] == 'queued' for e in self.notes.values())
        if self.controller.busy and self.controller.stop_event.is_set():
            self.banner.set(f'중지 중 · 실행 중인 {len(converting)+ready}개를 마무리합니다. 새 문서는 시작하지 않습니다.')
        elif (converting or ready or queued) and self.controller.busy:
            self.banner.set(f'변환 작업 {len(converting)}개 · 순서 대기 {queued}개 · 저장 대기 {ready}개')
        elif self.controller.busy and self.controller.kind == 'watch' and not self.controller.stop_event.is_set():
            issues = sum(e['status'] in ('error', 'conflict', 'missing', 'missing_output') for e in self.notes.values())
            self.banner.set(f'백업 감시 중 · 확인할 문서 {issues}개' if issues else '백업 감시 중…')

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def hide(self):
        if self.tray and self.tray.visible:
            self.window.withdraw()
        else:
            from tkinter import messagebox
            if messagebox.askyesno('종료', '트레이를 사용할 수 없습니다. 현재 작업을 마친 뒤 종료할까요?', parent=self.window):
                self.exit()

    def error(self, message):
        self.failed = True
        self.banner.set('오류: '+message)
        self.log_lines.append(message)
        self.log_lines = self.log_lines[-100:]
        self.show()

    def exit(self):
        if self.folder_dialog: self.folder_dialog.close()
        self.closing = True
        self.controller.stop()
        self.banner.set('현재 작업을 안전하게 마친 뒤 종료합니다…')
        self.controls()

    def poll(self):
        try:
            while True:
                kind, value = self.controller.events.get_nowait()
                if kind == 'config':
                    from .sync_config import pair_key
                    if self.config is None or pair_key(self.config) != pair_key(value):
                        self.checked.clear()
                    self.config = value
                    from .folder_selection import selection_for, summary
                    self.folder_drafts.clear()
                    self.folder_summary.set('동기화 폴더: ' + summary(selection_for(value)))
                    self.backup.set(value['backup'])
                    self.vault.set(value['vault'])
                    self.width.set(str(value['width_scale']))
                    self.workers.set(str(value.get('workers', 'auto')))
                    self.autostart.set(value.get('start_on_login', False))
                elif kind == 'locations':
                    self.backup_box.configure(values=value['backup'])
                    self.vault_box.configure(values=value['vault'])
                elif kind == 'state': self.update_state(value)
                elif kind == 'log':
                    self.log_lines.append(value)
                    self.log_lines = self.log_lines[-100:]
                elif kind in ('error', 'tray_error'): self.error(value)
                elif kind == 'show': self.show()
                elif kind == 'toggle': self.toggle()
                elif kind in ('exit','session_end'): self.exit()
                elif kind == 'initialized': self.initialized = True
                elif kind == 'finished':
                    if value == 'initialize':
                        self.initialized = True
                        if self.startup and self.config and self.config.get('start_on_login') and not self.failed and not self.closing:
                            # Wait until the initialization thread has actually returned.
                            self.window.after(100, self.toggle)
                        elif self.startup: self.show()
                    if not self.failed and not self.closing:
                        issues = sum(e['status'] in ('error','conflict','missing','missing_output','waiting','pending') for e in self.notes.values())
                        self.banner.set(f'대기 중 · 확인할 문서 {issues}개' if issues else '대기 중')
        except Empty:
            pass
        self.controls()
        if self.closing and not self.controller.busy:
            if self.tray: self.tray.close()
            self.controller.close()
            self.window.destroy()
            return
        self.window.after(100, self.poll)


def main(argv=None):
    from multiprocessing import freeze_support
    freeze_support()
    parser = argparse.ArgumentParser(description='SNo2md Windows desktop')
    parser.add_argument('--startup', action='store_true', help='Start hidden and resume configured watching')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('Windows 11 x64 desktop only; use sno2md CLI on this platform')
    import tkinter as tk
    from tkinter import messagebox
    from . import windows_desktop as native
    window = tk.Tk()
    window.withdraw()
    controller = DesktopController(state_directory())
    tray = None
    try:
        try:
            tray = native.WindowsTray(lambda kind, value=None: controller.events.put((kind, value)))
        except (ImportError, OSError) as error:
            controller.events.put(('tray_error', str(error)))
        if tray and not tray.is_owner:
            if not args.startup and not tray.activate_existing():
                messagebox.showinfo('SNo2md', '이미 실행 중입니다. 트레이에서 열어 주세요.', parent=window)
            tray.close()
            controller.close()
            window.destroy()
            return
        if tray:
            try:
                tray.start()
            except OSError as error:
                controller.events.put(('tray_error', str(error)))
        Application(window, controller, native, tray, startup=args.startup)
        if not args.startup or not tray or not tray.visible: window.deiconify()
        window.mainloop()
    except Exception as error:
        messagebox.showerror('SNo2md 실행 오류', str(error), parent=window)
        controller.stop()
        if controller.thread: controller.thread.join()
        if tray: tray.close()
        controller.close()
        window.destroy()
        raise


if __name__ == '__main__':
    main()
