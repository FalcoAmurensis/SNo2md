"""Modal folder picker; only the main thread touches Tk."""
from copy import deepcopy
from queue import Queue, Empty
from threading import Thread
from .folder_selection import inventory, check_state, set_checked, ROOT, TRASH


class FolderDialog:
    def __init__(self, parent, backup, rules, apply, closed):
        import tkinter as tk
        from tkinter import ttk
        self.window = tk.Toplevel(parent)
        self.window.title('동기화 폴더 선택')
        self.window.geometry('560x520')
        self.window.transient(parent)
        self.window.grab_set()
        self.backup, self.rules = backup, deepcopy(rules)
        self.apply_callback, self.closed_callback = apply, closed
        self.events = Queue()
        self.data = None
        self.alive = True
        self.loading = False
        self.message = tk.StringVar()
        ttk.Label(self.window, textvariable=self.message, wraplength=530).pack(fill='x', padx=12, pady=10)
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
            button.pack(side='left', padx=2)
            self.buttons.append(button)
        ttk.Button(actions, text='취소', command=self.close).pack(side='right')
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
                self.message.set(result['basis'] + ' · 체크 해제한 폴더의 기존 파일은 유지됩니다.')
                self.render()
            else:
                self.data = None
                self.tree.delete(*self.tree.get_children())
                self.message.set('읽기 실패: ' + result + '\n새로고침으로 다시 시도하세요.')
            for i, button in enumerate(self.buttons):
                button.configure(state='normal' if ok or i == 2 else 'disabled')
        except Empty: pass
        self.window.after(100, self.poll)

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
            self.tree.item(key, text=mark + ' ' + node['name'])

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
