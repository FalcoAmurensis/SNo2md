"""Windows tray, single-instance window and per-user login registration."""
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread


RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'SNo2md'
WINDOW_CLASS = 'SNo2md.Desktop.Tray'
MUTEX_NAME = r'Local\SNo2md.Desktop'


def startup_command():
    if getattr(sys, 'frozen', False):
        arguments = [str(Path(sys.executable).resolve()), '--startup']
    else:
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        if not pythonw.is_file():
            raise OSError('pythonw.exe is required for login startup in source mode')
        arguments = [str(pythonw.resolve()), '-m', 'sno2md.gui', '--startup']
    command = subprocess.list2cmdline(arguments)
    if len(command) > 260:
        raise ValueError('자동 실행 경로가 너무 깁니다. EXE를 더 짧은 경로에 놓아 주세요.')
    return command


def get_startup_command():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0]
    except FileNotFoundError:
        return None


def set_startup_command(command):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, access=winreg.KEY_SET_VALUE) as key:
        if command is None:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)


class WindowsTray:
    """Dedicated native message thread; callbacks only enqueue UI commands."""
    def __init__(self, callback):
        if os.name != 'nt':
            raise OSError('Windows desktop features require Windows')
        import win32api
        import win32con
        import win32event
        import win32gui
        self.api, self.con, self.event, self.gui = win32api, win32con, win32event, win32gui
        self.callback = callback
        self.ready = Event()
        self.closing = Event()
        self.hwnd = None
        self.thread = None
        self.error = None
        self.visible = False
        self.watching = False
        self.busy = False
        self.stopping = False
        self.callback_message = win32con.WM_USER + 20
        self.show_message = win32gui.RegisterWindowMessage('SNo2md.ShowWindow')
        self.taskbar_message = win32gui.RegisterWindowMessage('TaskbarCreated')
        self.mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
        self.is_owner = win32api.GetLastError() != 183

    def toggle_state(self):
        if self.stopping: return '중지 중', False
        if self.watching: return '자동 동기화 중지', True
        if self.busy: return '작업 중', False
        return '자동 동기화 시작', True

    def activate_existing(self):
        # The first instance may still be creating its message window.
        for _ in range(30):
            hwnd = self.gui.FindWindow(WINDOW_CLASS, 'SNo2md')
            if hwnd:
                self.gui.PostMessage(hwnd, self.show_message, 0, 0)
                return True
            Event().wait(0.1)
        return False

    def _icon_data(self):
        return (self.hwnd, 0, self.gui.NIF_ICON | self.gui.NIF_MESSAGE | self.gui.NIF_TIP,
                self.callback_message, self.icon, 'SNo2md — 삼성노트 동기화')

    def _add_icon(self):
        self.gui.Shell_NotifyIcon(self.gui.NIM_ADD, self._icon_data())
        self.visible = True

    def _message(self, hwnd, message, wparam, lparam):
        con, gui = self.con, self.gui
        if message == self.show_message:
            self.callback('show')
        elif message == self.taskbar_message:
            try:
                self._add_icon()
            except Exception as error:
                self.visible = False
                self.callback('tray_error', str(error))
        elif message == self.callback_message:
            if lparam == con.WM_LBUTTONDBLCLK:
                self.callback('show')
            elif lparam in (con.WM_RBUTTONUP, con.WM_CONTEXTMENU):
                menu = gui.CreatePopupMenu()
                try:
                    gui.AppendMenu(menu, con.MF_STRING, 1, '열기')
                    label, enabled = self.toggle_state()
                    flags = con.MF_STRING | (0 if enabled else con.MF_GRAYED)
                    gui.AppendMenu(menu, flags, 2, label)
                    gui.AppendMenu(menu, con.MF_SEPARATOR, 0, '')
                    gui.AppendMenu(menu, con.MF_STRING, 3, '종료')
                    gui.SetForegroundWindow(hwnd)
                    gui.TrackPopupMenu(menu, con.TPM_RIGHTBUTTON, *gui.GetCursorPos(), 0, hwnd, None)
                    gui.PostMessage(hwnd, con.WM_NULL, 0, 0)
                finally:
                    gui.DestroyMenu(menu)
        elif message == con.WM_COMMAND:
            command = {1: 'show', 2: 'toggle', 3: 'exit'}.get(wparam & 0xffff)
            if command and (command != 'toggle' or self.toggle_state()[1]):
                self.callback(command)
        elif message == con.WM_QUERYENDSESSION:
            self.callback('session_end')
            return 1
        elif message == con.WM_ENDSESSION:
            if wparam: self.callback('session_end')
        elif message == con.WM_CLOSE:
            gui.DestroyWindow(hwnd)
        elif message == con.WM_DESTROY:
            try:
                if self.visible:
                    gui.Shell_NotifyIcon(gui.NIM_DELETE, (hwnd, 0))
            finally:
                self.visible = False
                gui.PostQuitMessage(0)
        else:
            return gui.DefWindowProc(hwnd, message, wparam, lparam)
        return 0

    def _run(self):
        gui, con = self.gui, self.con
        atom = None
        try:
            wc = gui.WNDCLASS()
            wc.hInstance = self.api.GetModuleHandle(None)
            wc.lpszClassName = WINDOW_CLASS
            wc.lpfnWndProc = self._message
            atom = gui.RegisterClass(wc)
            self.hwnd = gui.CreateWindow(atom, 'SNo2md', con.WS_OVERLAPPED,
                                         0, 0, 0, 0, 0, 0, wc.hInstance, None)
            self.icon = gui.LoadIcon(0, con.IDI_APPLICATION)
            try:
                self._add_icon()
            except Exception as error:
                self.error = str(error)
            self.ready.set()
            if self.closing.is_set():
                gui.PostMessage(self.hwnd, con.WM_CLOSE, 0, 0)
            gui.PumpMessages()
        except Exception as error:
            self.error = str(error)
            self.callback('tray_error', str(error))
        finally:
            self.ready.set()
            self.hwnd = None
            if atom:
                gui.UnregisterClass(atom, self.api.GetModuleHandle(None))

    def start(self):
        self.thread = Thread(target=self._run, name='sno2md-tray', daemon=False)
        self.thread.start()
        if not self.ready.wait(5):
            raise OSError('트레이 초기화가 지연되고 있습니다. 창을 유지합니다.')
        if self.error:
            raise OSError(self.error)

    def close(self):
        self.closing.set()
        if self.hwnd:
            self.gui.PostMessage(self.hwnd, self.con.WM_CLOSE, 0, 0)
        if self.thread:
            self.thread.join(5)
        if self.mutex:
            self.api.CloseHandle(self.mutex)
            self.mutex = None
