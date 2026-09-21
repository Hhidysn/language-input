"""Windows child-process helpers: no console flash, no throwaway ``tasklist``.

The settings app is shipped as a PyInstaller **``--windowed``** (GUI-subsystem)
bundle: the process owns *no* console.  Windows then allocates a brand-new
console window for every console-subsystem child process that does not ask it
not to — which is exactly the "several console windows flash on startup" report.

Two rules are enforced here:

1. **Every** ``subprocess`` spawn in this package passes
   :func:`no_window_kwargs` so the child can never show a console:
   ``CREATE_NO_WINDOW`` plus a ``STARTUPINFO`` with ``STARTF_USESHOWWINDOW`` /
   ``SW_HIDE`` (belt-and-braces).  Both are no-ops on non-Windows.

2. Process liveness checks use :func:`list_process_ids` — a pure ctypes
   ``EnumProcesses`` / ``OpenProcess`` / ``QueryFullProcessImageNameW`` walk
   that spawns nothing at all, replacing the old ``tasklist`` probes.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

__all__ = [
    "CREATE_NO_WINDOW",
    "DETACHED_PROCESS",
    "hidden_startupinfo",
    "no_window_kwargs",
    "list_process_ids",
    "is_process_running",
]

# --- process-creation flags -------------------------------------------------

# ``CREATE_NO_WINDOW`` (0x08000000): the child is a console application run
# *without* a console window.  ``DETACHED_PROCESS`` (0x00000008): the child
# gets no console at all (used for long-lived detached helpers).
CREATE_NO_WINDOW: int = 0x08000000 if sys.platform == "win32" else 0
DETACHED_PROCESS: int = 0x00000008 if sys.platform == "win32" else 0


def hidden_startupinfo() -> "subprocess.STARTUPINFO | None":
    """A ``STARTUPINFO`` that hides the child's window (``None`` off-Windows).

    Belt-and-braces with ``CREATE_NO_WINDOW``: ``STARTF_USESHOWWINDOW`` plus
    ``SW_HIDE`` guarantees nothing is shown even if a window were allocated.
    ``subprocess`` accepts ``None`` as "use the default" on every platform.
    """
    if sys.platform != "win32":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def no_window_kwargs(*, detached: bool = False) -> dict:
    """Keyword arguments for ``Popen`` / ``run`` that suppress any console.

    ``detached=True`` also sets ``DETACHED_PROCESS`` so the child does not
    inherit (or allocate) a console and can outlive this app.

    Usage::

        subprocess.run(command, **no_window_kwargs())
        subprocess.Popen(command, **no_window_kwargs(detached=True))
    """
    flags = CREATE_NO_WINDOW | (DETACHED_PROCESS if detached else 0)
    return {
        "creationflags": flags,
        "startupinfo": hidden_startupinfo(),
    }


# --- ctypes process enumeration ---------------------------------------------

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def list_process_ids(image_name: str) -> list[int]:
    """PIDs of processes whose executable base name equals ``image_name``.

    Pure ``kernel32`` (``EnumProcesses`` + ``OpenProcess`` +
    ``QueryFullProcessImageNameW``): **no child process is created**.  This is
    behaviourally equivalent to ``tasklist /FI "IMAGENAME eq <name>"`` but
    without the console flash.  Never raises; returns ``[]`` on non-Windows,
    on an empty name or on any enumeration failure.
    """
    if sys.platform != "win32" or not image_name:
        return []

    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:  # pragma: no cover - defensive
        return []

    # On Windows 7+ the API is exported as ``K32EnumProcesses``; older SDKs /
    # docs call it ``EnumProcesses``.  Accept either name.
    enum_processes = getattr(kernel32, "K32EnumProcesses", None) or getattr(
        kernel32, "EnumProcesses", None
    )
    if enum_processes is None:  # pragma: no cover - defensive
        return []

    try:
        enum_processes.argtypes = [
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        enum_processes.restype = wintypes.BOOL

        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE

        query_image_name = kernel32.QueryFullProcessImageNameW
        query_image_name.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query_image_name.restype = wintypes.BOOL

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        return []

    target = image_name.lower()

    # EnumProcesses may need a larger buffer than the first guess; grow and
    # retry only while the returned byte count fills the whole buffer.
    pids: list[int] = []
    capacity = 1024
    while capacity <= 1 << 20:
        array = (wintypes.DWORD * capacity)()
        needed = wintypes.DWORD(0)
        if not enum_processes(
            array, ctypes.sizeof(array), ctypes.byref(needed)
        ):
            return pids
        count = needed.value // ctypes.sizeof(wintypes.DWORD)
        if count < capacity:
            pids = [int(array[index]) for index in range(count)]
            break
        capacity *= 2
    else:  # pragma: no cover - absurd process count
        return []

    buffer = ctypes.create_unicode_buffer(32768)
    found: list[int] = []
    for pid in pids:
        if pid == 0:
            continue
        handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            size = wintypes.DWORD(len(buffer))
            if query_image_name(handle, 0, buffer, ctypes.byref(size)):
                if os.path.basename(buffer.value).lower() == target:
                    found.append(pid)
        finally:
            close_handle(handle)
    return found


def is_process_running(image_name: str) -> bool:
    """True when at least one process matches ``image_name`` (never raises)."""
    return bool(list_process_ids(image_name))
