"""Window geometry in the same logical coordinate space as Tk; no OS changes."""
import sys


def work_area(window):
    """Nearest monitor work rectangle, excluding taskbars and app bars."""
    if sys.platform == 'win32':
        import win32api
        import win32gui
        hwnd = win32gui.GetParent(window.winfo_id()) or window.winfo_id()
        monitor = win32api.MonitorFromWindow(hwnd, 2)
        return tuple(win32api.GetMonitorInfo(monitor)['Work'])
    return (0, 0, window.winfo_screenwidth(), window.winfo_screenheight())


def client_limit(window):
    """Space for a client area, reserving the current native window frame."""
    left, top, right, bottom = work_area(window)
    frame_x, frame_y = 16, 40
    if sys.platform == 'win32':
        import win32gui
        hwnd = win32gui.GetParent(window.winfo_id()) or window.winfo_id()
        outer, client = win32gui.GetWindowRect(hwnd), win32gui.GetClientRect(hwnd)
        frame_x = max(0, outer[2]-outer[0]-(client[2]-client[0]))
        frame_y = max(0, outer[3]-outer[1]-(client[3]-client[1]))
    return max(1, right-left-frame_x), max(1, bottom-top-frame_y)


def enable_dpi_awareness():
    """Opt this process into per-monitor DPI; returns whether it is DPI aware.

    Call before the first Tk window exists. Changes only this process.
    """
    if sys.platform != 'win32':
        return False
    import ctypes
    user32 = ctypes.WinDLL('user32')
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try: ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)
        except (AttributeError, OSError): return False
    try:
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        # A manifest may already have chosen awareness; either way report the result.
        return user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext()) > 0
    except (AttributeError, OSError):
        return False


def window_dpi(window):
    """The DPI Windows reports for the monitor that holds the window (96 elsewhere)."""
    if sys.platform != 'win32':
        return 96
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32')
    try:
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        return user32.GetDpiForWindow(hwnd) or user32.GetDpiForSystem() or 96
    except (AttributeError, OSError):
        return 96


def elide(text, width, measure):
    """Bound a single-line presentation without discarding its original value."""
    if measure(text) <= width: return text
    if measure('…') > width: return ''
    low, high = 0, len(text)
    while low < high:
        middle = (low+high+1)//2
        if measure(text[:middle]+'…') <= width: low = middle
        else: high = middle-1
    return text[:low]+'…'


def fit_dialog(window):
    """Keep modal controls within the current monitor without changing values."""
    width,height=client_limit(window)
    minimum=window.minsize()
    bounded=min(minimum[0],width),min(minimum[1],height)
    if minimum!=bounded:window.minsize(*bounded)
    if window.winfo_width()>width or window.winfo_height()>height:
        left,top,_,_=work_area(window)
        window.geometry(f'{min(window.winfo_width(),width)}x{min(window.winfo_height(),height)}{left:+d}{top:+d}')
