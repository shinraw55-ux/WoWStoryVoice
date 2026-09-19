import atexit
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes


_MUTEX_NAME = r"Local\WoWStoryVoice.Companion.v1"
_EXIT_WATCHDOG_SEC = 3.0
_mutex_handle = None
_kernel32 = None
_watchdog_started = False


def _live_threads():
    current = threading.current_thread()
    items = []
    for thread in threading.enumerate():
        if thread is current or not thread.is_alive():
            continue
        items.append(f"{thread.name}(daemon={int(bool(thread.daemon))})")
    return ", ".join(items) if items else "none"


def _release_only():
    """Release the Windows mutex without changing process lifetime."""
    global _mutex_handle
    if _mutex_handle is None or _kernel32 is None:
        return
    try:
        _kernel32.CloseHandle(_mutex_handle)
    finally:
        _mutex_handle = None


def _start_exit_watchdog():
    """Force termination only if graceful shutdown leaves the process alive.

    Some native libraries used by the companion (tray implementations,
    PyTorch/CUDA and frozen-runtime dependencies) may own threads that Python
    cannot reliably join itself. The application already asks its own speech,
    capture and tray workers to stop. If something external still prevents
    process teardown, this watchdog prevents WoWStoryVoice.exe from remaining
    in Task Manager indefinitely after the user explicitly exits.
    """
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True

    def watchdog():
        time.sleep(_EXIT_WATCHDOG_SEC)
        try:
            print(
                "EXIT WATCHDOG: process still alive after graceful shutdown; "
                f"remaining Python threads: {_live_threads()}"
            )
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
        finally:
            os._exit(0)

    threading.Thread(target=watchdog, name="WSV-ExitWatchdog", daemon=True).start()


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

    ctypes.set_last_error(0)
    handle = create_mutex(None, False, _MUTEX_NAME)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)

    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False

    _kernel32 = kernel32
    _mutex_handle = handle
    # Interpreter shutdown should only release the native handle. The forced
    # exit watchdog belongs to an explicit application Exit request, not atexit.
    atexit.register(_release_only)
    return True


def release():
    """Release single-instance ownership and guarantee explicit Exit completes."""
    _release_only()
    print(f"Exit requested; live threads before process teardown: {_live_threads()}")
    _start_exit_watchdog()
