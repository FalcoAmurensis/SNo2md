"""Normalize presentation text without changing filesystem paths or document IDs."""
import tkinter as tk
from tkinter import simpledialog, ttk
from unicodedata import normalize


def display(value):
    return normalize('NFC', str(value))


class NFCStringVar(tk.StringVar):
    def __init__(self, master=None, value='', name=None):
        super().__init__(master, display(value), name)
        self.trace_add('write', self._normalize)

    def _normalize(self, *_):
        value = super().get()
        normalized = display(value)
        if value != normalized:
            super().set(normalized)

    def set(self, value):
        super().set(display(value))

    def get(self):
        return display(super().get())


class PathStringVar(tk.StringVar):
    """Tk sees NFC; application code gets the exact selected or entered path."""
    def __init__(self, master=None, value='', name=None):
        self._raw = str(value)
        self._updating = False
        super().__init__(master, display(value), name)
        super().trace_add('write', self._edited)

    def _edited(self, *_):
        if self._updating:
            return
        value = super().get()
        if value == display(self._raw):
            return
        self._raw = value
        self._updating = True
        try:
            super().set(display(self._raw))
        finally:
            self._updating = False

    def trace_add(self, mode, callback):
        # Tcl runs newer traces first: capture edits before notifying consumers.
        def observed(*args):
            if mode == 'write' or 'write' in mode:
                self._edited()
            return callback(*args)
        return super().trace_add(mode, observed)

    def get(self):
        return self._raw

    def set(self, value):
        self._raw = str(value)
        self._updating = True
        try:
            super().set(display(value))
        finally:
            self._updating = False


class NFCText(tk.Text):
    def insert(self, index, chars, *args):
        # Text.insert accepts alternating text and tag lists after chars.
        values = tuple(display(value) if i % 2 else value for i, value in enumerate(args))
        return super().insert(index, display(chars), *values)


def ask_name(parent, initial):
    class NameDialog(simpledialog.Dialog):
        def body(self, master):
            ttk.Label(master, text='새 파일 이름 (확장자 제외)\n기존 PDF와 Markdown은 그대로 남고 새 이름이 관리 대상이 됩니다.\n기존 사용자 메모는 새 파일로 자동 이전되지 않습니다.').pack()
            self.name = NFCStringVar(master, initial)
            entry = ttk.Entry(master, textvariable=self.name, width=45)
            entry.pack(fill='x', pady=8)
            entry.selection_range(0, 'end')
            return entry

        def apply(self):
            self.result = self.name.get()

        def buttonbox(self):
            box = ttk.Frame(self, padding=10)
            ttk.Button(box, text='확인', style='Accent.TButton', command=self.ok).pack(side='right')
            ttk.Button(box, text='취소', command=self.cancel).pack(side='right', padx=(0, 8))
            self.bind('<Return>', self.ok)
            self.bind('<Escape>', self.cancel)
            box.pack(fill='x')

    return NameDialog(parent, '다른 이름으로 저장').result
