"""Modal folder picker; only the main thread touches Tk."""
from copy import deepcopy
from queue import Queue, Empty
from threading import Thread
from unicodedata import normalize
from .folder_selection import inventory, check_state, set_checked, ROOT, TRASH


class FolderDialog:
    def __init__(self, parent, backup, rules, apply, closed):
        import tkinter as tk
        from tkinter import ttk
        self.window = tk.Toplevel(parent)
        self.window.title('가져올 폴더 선택')
        self.window.geometry('560x520')
        self.window.minsize(420, 360)
        self.window.transient(parent)
        self.window.grab_set()
        self.backup, self.rules = backup, deepcopy(rules)
        self.apply_callback, self.closed_callback = apply, closed
        self.events = Queue()
        self.data = None
        self.alive = True
        self.loading = False
        self.message = tk.StringVar()
        self.last_error = ''
        info = ttk.Label(self.window, textvariable=self.message, wraplength=530)
        info.pack(fill='x', padx=12, pady=10)
        info.bind('<Configure>', lambda e: info.configure(wraplength=max(180, e.width)))
        frame = ttk.Frame(self.window)
        frame.pack(fill='both', expand=True, padx=12)
        self.tree = ttk.Treeview(frame, show='tree', selectmode='browse')
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<Button-1>', self.click)
        self.tree.bind('<space>', self.space)
        actions = ttk.Frame(self.window)
        actions.pack(fill='x', padx=12, pady=12)
        self.buttons = []
        for label, command in [('전체 선택', lambda: self.all(True)), ('전체 해제', lambda: self.all(False)),
                               ('새로고침', self.refresh), ('적용', self.apply)]:
            button = ttk.Button(actions, text=label, command=command)
            button.grid(row=len(self.buttons)//3, column=len(self.buttons)%3, sticky='ew', padx=2, pady=2)
            self.buttons.append(button)
        for column in range(3): actions.columnconfigure(column, weight=1)
        self.error_button = ttk.Button(actions, text='기술 정보', command=self.show_error, state='disabled')
        self.error_button.grid(row=1, column=1, sticky='ew', padx=2)
        ttk.Button(actions, text='취소', command=self.close).grid(row=1, column=2, sticky='ew', padx=2)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        self.window.after(100, self.poll)

    def refresh(self):
        if self.loading: return
        self.loading = True
        self.message.set('폴더 구조를 읽는 중…')
        for button in self.buttons: button.configure(state='disabled')
        def read():
            try: self.events.put((True, inventory(self.backup)))
            except Exception as error: self.events.put((False, str(error)))
        Thread(target=read, name='sno2md-folders', daemon=True).start()

    def poll(self):
        if not self.alive: return
        try:
            ok, result = self.events.get_nowait()
            self.loading = False
            if ok:
                self.tree.delete(*self.tree.get_children())
                self.data = result
                self.last_error = ''
                self.error_button.configure(state='disabled')
                self.message.set(result['basis'] + ' · 선택하지 않은 폴더도 이미 저장한 파일은 삭제하지 않습니다.')
                self.render()
            else:
                self.data = None
                self.tree.delete(*self.tree.get_children())
                self.last_error = result
                self.error_button.configure(state='normal')
                self.message.set('폴더 목록을 읽지 못했습니다. 백업 경로와 접근 상태를 확인한 뒤 새로고침하세요. 원문 오류는 기술 정보에서 볼 수 있습니다.')
            for i, button in enumerate(self.buttons):
                button.configure(state='normal' if ok or i == 2 else 'disabled')
        except Empty: pass
        self.window.after(100, self.poll)

    def show_error(self):
        from tkinter import messagebox
        if self.last_error:
            messagebox.showinfo('폴더 목록 기술 정보', self.last_error, parent=self.window)

    def render(self):
        tree = self.data['tree']
        if not self.tree.get_children():
            pending = dict(tree)
            while pending:
                ready = [key for key, node in pending.items() if node['parent'] is None or self.tree.exists(node['parent'])]
                if not ready: raise ValueError('invalid folder tree')
                for key in ready:
                    node = pending.pop(key)
                    self.tree.insert(node['parent'] or '', 'end', iid=key, open=True)
        for key, node in tree.items():
            mark = {'checked': '☑', 'unchecked': '☐', 'partial': '▣'}[check_state(tree, self.rules, key)]
            # Normalize only the displayed label; IDs, rules and source names stay exact.
            self.tree.item(key, text=mark + ' ' + normalize('NFC', node['name']))

    def toggle(self, key):
        if self.loading or not self.data or not key: return
        set_checked(self.data['tree'], self.rules, key, check_state(self.data['tree'], self.rules, key) != 'checked')
        self.render()

    def click(self, event):
        if 'indicator' not in self.tree.identify_element(event.x, event.y):
            self.toggle(self.tree.identify_row(event.y))

    def space(self, event):
        self.toggle(self.tree.focus())
        return 'break'

    def all(self, value):
        self.rules = {ROOT: value}
        self.render()

    def apply(self):
        if self.loading or self.data is None: return
        self.apply_callback(deepcopy(self.rules))
        self.close()

    def close(self):
        self.alive = False
        self.window.grab_release()
        self.window.destroy()
        self.closed_callback()
