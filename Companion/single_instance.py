import atexit
import ctypes
import sys
from ctypes import wintypes


_MUTEX_NAME = r"Local\WoWStoryVoice.Companion.v1"
_mutex_handle = None
_kernel32 = None


def acquire():
    """Return True only for the first running Windows companion instance."""
    global _mutex_handle, _kernel32
    if _mutex_handle is not None:
        return True
    if sys.platform != "win32":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE

    handle = create_mutex(None, False, _MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    error = ctypes.get_last_error()
    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False

    _kernel32 = kernel32
    _mutex_handle = handle
    atexit.register(release)
    return True


def release():
    global _mutex_handle
    if _mutex_handle is None or _kernel32 is None:
        return
    try:
        _kernel32.CloseHandle(_mutex_handle)
    finally:
        _mutex_handle = None
