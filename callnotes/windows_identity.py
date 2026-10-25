"""Give the Python-hosted desktop app its own Windows taskbar identity."""
import sys

APP_ID = 'CallNotesLocal.CallNotes.Studio'
_icons = []


def set_app_identity():
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def set_native_icons(window, icon_path):
    if sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    user = ctypes.windll.user32
    user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.LoadImageW.restype = wintypes.HANDLE
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = ctypes.c_ssize_t
    for slot, metric in ((1, 11), (0, 49)):
        size = user.GetSystemMetrics(metric)
        handle = user.LoadImageW(None, str(icon_path), 1, size, size, 0x10)
        if handle:
            _icons.append(handle)
            user.SendMessageW(int(window.winId()), 0x80, slot, handle)
