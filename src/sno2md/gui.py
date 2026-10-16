"""Small Korean desktop UI. All Tk calls stay on the main thread."""
import argparse
import os
from pathlib import Path
from queue import Empty
import sys

from .desktop import DesktopController
from .locations import state_directory


STATUS = {'pending': '대기', 'waiting': '입력 안정화 대기', 'converting': '변환 중',
          'synced': '완료', 'missing': '원본 없음', 'conflict': '충돌', 'error': '실패'}
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
        self.closing = False
        self.initialized = False
        self.failed = False
        self.tree_keys = {}
        self.log_lines = []
        self.backup = tk.StringVar()
        self.vault = tk.StringVar()
        self.width = tk.StringVar(value='0.5')
        self.autostart = tk.BooleanVar(value=True)
        self.banner = tk.StringVar(value='설정을 불러오는 중…')
        window.title('SNo2md — 삼성노트 동기화')
        window.geometry('880x590')
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
        ttk.Label(body, textvariable=self.banner, wraplength=800).grid(row=5, column=0, columnspan=4, sticky='w', pady=(0, 8))
        table = ttk.Frame(body)
        table.grid(row=6, column=0, columnspan=4, sticky='nsew')
        self.tree = ttk.Treeview(table, columns=('name', 'status', 'source'), show='headings', selectmode='browse')
        for key, label, width in (('name','문서 / 출력 이름',240), ('status','상태',135), ('source','백업 내 경로',350)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=80)
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda event: self.controls())
        footer = ttk.Frame(body)
        footer.grid(row=7, column=0, columnspan=4, sticky='ew', pady=(10, 0))
        self.retry_button = ttk.Button(footer, text='선택 문서 재시도', command=self.retry)
        self.retry_button.pack(side='left')
        self.rename_button = ttk.Button(footer, text='출력 이름 변경', command=self.rename)
        self.rename_button.pack(side='left', padx=7)
        ttk.Button(footer, text='오류 / 로그 보기', command=self.details).pack(side='right')
        ttk.Label(body, text='창을 닫으면 트레이에서 계속 실행됩니다. 종료는 트레이 메뉴를 이용하세요.', foreground='#666666').grid(row=8, column=0, columnspan=4, sticky='w', pady=(12, 0))
        window.protocol('WM_DELETE_WINDOW', self.hide)
        window.report_callback_exception = lambda kind, value, tb: self.error(f'{kind.__name__}: {value}')
        self.controls()
        window.after(100, self.poll)
        controller.launch(controller.initialize, kind='initialize')

    def selected(self):
        selected = self.tree.selection()
        return self.tree_keys.get(selected[0]) if selected else None

    def controls(self):
        busy = self.controller.busy
        idle = self.initialized and not busy and not self.closing
        for widget in (self.save_button, self.width_box, self.startup_box):
            widget.configure(state='normal' if idle else 'disabled')
        for widget in (self.folder_button, self.file_button, self.vault_button):
            widget.configure(state='normal' if idle and self.config is None else 'disabled')
        for widget in (self.backup_box, self.vault_box):
            widget.configure(state='normal' if idle and self.config is None else 'disabled')
        self.once_button.configure(state='normal' if idle and self.config else 'disabled')
        self.open_button.configure(state='normal' if self.config else 'disabled')
        stopping = busy and self.controller.stop_event.is_set()
        self.watch_button.configure(text='중지 중…' if stopping else ('감시 / 작업 중지' if busy and self.controller.kind in ('watch','sync') else '감시 시작'),
                                    state='normal' if not self.closing and not stopping and (idle and self.config or busy and self.controller.kind in ('watch','sync')) else 'disabled')
        key = self.selected()
        self.rename_button.configure(state='normal' if idle and key else 'disabled')
        self.retry_button.configure(state='normal' if idle and key and self.notes[key]['status'] in RETRYABLE else 'disabled')
        if self.tray:
            self.tray.watching = busy and self.controller.kind in ('watch','sync')

    def browse(self, kind):
        from tkinter import filedialog
        if kind == 'file':
            value = filedialog.askopenfilename(parent=self.window, title='SDOCX 파일 선택', filetypes=[('Samsung Notes', '*.sdocx')])
        else:
            value = filedialog.askdirectory(parent=self.window, title='Vault 선택' if kind == 'vault' else '삼성노트 백업 폴더 선택')
        if value: (self.vault if kind == 'vault' else self.backup).set(value)

    def save(self):
        try:
            if not self.backup.get().strip() or not self.vault.get().strip():
                raise ValueError('백업 경로와 Vault를 선택해 주세요.')
            width = float(self.width.get())
            from .model import ConversionOptions
            ConversionOptions(width_scale=width)
            values = (self.backup.get(), self.vault.get(), width, self.autostart.get(), self.native)
            self.failed = False
            self.controller.launch(lambda: self.controller.save_settings(*values))
            self.banner.set('설정을 저장하는 중…')
        except (ValueError, OSError) as error:
            self.error(str(error))
        self.controls()

    def sync_once(self, sources=None):
        if self.controller.busy or self.config is None: return
        self.failed = False
        self.controller.start(only_sources=sources)
        self.banner.set('동기화 준비 중…')
        self.controls()

    def toggle(self):
        if self.controller.busy:
            if self.controller.kind in ('watch','sync'):
                self.controller.stop()
                self.banner.set('현재 문서를 안전하게 마친 뒤 중지합니다.')
        elif self.config and not self.closing:
            self.failed = False
            self.controller.start(watch=True)
            self.banner.set('백업 감시 중…')
        else:
            self.show()
        self.controls()

    def retry(self):
        key = self.selected()
        if key: self.sync_once({key})

    def rename(self):
        from tkinter import simpledialog
        key = self.selected()
        if not key or self.controller.busy: return
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
        key = self.selected()
        if key and self.notes[key].get('error'):
            text.insert('end', f'{key}\n{self.notes[key]["error"]}\n\n')
        text.insert('end', '\n'.join(self.log_lines) or '기록된 오류가 없습니다.')
        text.insert('end', f'\n\n로그 파일: {self.controller.root / "desktop.log"}')
        text.configure(state='disabled')

    def update_state(self, state):
        selected = self.selected()
        self.notes = state['notes']
        self.tree.delete(*self.tree.get_children())
        self.tree_keys.clear()
        for number, (key, entry) in enumerate(self.notes.items()):
            iid = str(number)
            self.tree_keys[iid] = key
            self.tree.insert('', 'end', iid=iid, values=(entry['name'], STATUS.get(entry['status'], entry['status']), key))
            if key == selected: self.tree.selection_set(iid)
        converting = [e['name'] for e in self.notes.values() if e['status'] == 'converting']
        if converting and self.controller.busy and not self.controller.stop_event.is_set():
            self.banner.set('변환 중: '+converting[0])
        elif self.controller.busy and self.controller.kind == 'watch' and not self.controller.stop_event.is_set():
            issues = sum(e['status'] in ('error', 'conflict', 'missing') for e in self.notes.values())
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
        self.closing = True
        self.controller.stop()
        self.banner.set('현재 작업을 안전하게 마친 뒤 종료합니다…')
        self.controls()

    def poll(self):
        try:
            while True:
                kind, value = self.controller.events.get_nowait()
                if kind == 'config':
                    self.config = value
                    self.backup.set(value['backup'])
                    self.vault.set(value['vault'])
                    self.width.set(str(value['width_scale']))
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
                        issues = sum(e['status'] in ('error','conflict','missing') for e in self.notes.values())
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
